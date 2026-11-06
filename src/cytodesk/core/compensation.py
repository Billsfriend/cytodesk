"""Compensation matrix helpers.

CytoDesk uses FlowKit's :class:`flowkit.Matrix` for compensation.  This module
keeps the matrix parsing, validation and (de)serialization logic independent
from the Qt user interface so it can be tested headlessly.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

import numpy as np
from flowkit import Matrix

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .session import SampleRecord


SPILLOVER_KEYS = ("spillover", "spill", "$SPILLOVER", "$SPILL")


@dataclass
class CompensationInfo:
    """A snapshot of the compensation currently applied to a sample."""

    source: str
    detectors: list[str]
    fluorochromes: list[str]
    matrix: np.ndarray

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "detectors": list(self.detectors),
            "fluorochromes": list(self.fluorochromes),
            "matrix": [[float(value) for value in row] for row in self.matrix],
        }


# ----------------------------------------------------------------------
# Embedded FCS matrix
# ----------------------------------------------------------------------
def embedded_spill_text(sample) -> str | None:
    """Return the FCS ``$SPILLOVER``/``$SPILL`` text, if present."""

    metadata = getattr(sample, "metadata", {}) or {}
    for key in SPILLOVER_KEYS:
        value = metadata.get(key)
        if value:
            return str(value)
    return None


def fluorochrome_labels(record: "SampleRecord") -> tuple[list[str], list[str]]:
    """Return detector and fluorochrome labels for the record's fluoro channels."""

    sample = record.sample
    null_channels = set(getattr(sample, "null_channels", []) or [])
    detectors: list[str] = []
    fluorochromes: list[str] = []
    for index in sample.fluoro_indices:
        detector = sample.pnn_labels[index]
        if detector in null_channels:
            continue
        detectors.append(detector)
        label = sample.pns_labels[index] if index < len(sample.pns_labels) else ""
        fluorochromes.append(label or "")
    return detectors, fluorochromes


def compensation_info(record: "SampleRecord") -> CompensationInfo | None:
    """Return the compensation matrix currently applied to ``record``."""

    matrix = getattr(record.sample, "compensation", None)
    if matrix is None:
        return None
    detectors = list(getattr(matrix, "detectors", []) or [])
    fluorochromes = list(getattr(matrix, "fluorochromes", []) or [])
    values = np.asarray(getattr(matrix, "matrix", np.empty((0, 0))), dtype=float)
    if values.size == 0:
        return None
    return CompensationInfo(
        source=record.compensation_source or "fcs",
        detectors=detectors,
        fluorochromes=fluorochromes,
        matrix=values,
    )


def apply_embedded_compensation(record: "SampleRecord") -> str | None:
    """Apply the FCS embedded matrix to ``record``.

    Returns ``None`` on success, or a human readable error message.  The
    previous compensation state is left untouched when no matrix exists.
    """

    spill = embedded_spill_text(record.sample)
    if not spill:
        return "FCS 文件中没有 $SPILLOVER/$SPILL 补偿矩阵"
    detectors, fluorochromes = fluorochrome_labels(record)
    if not detectors:
        return "样本没有可用于补偿的荧光通道"
    try:
        matrix = Matrix(
            spill,
            detectors,
            fluorochromes,
            null_channels=getattr(record.sample, "null_channels", None),
        )
        record.sample.apply_compensation(matrix)
    except Exception as exc:
        record.compensation_error = str(exc)
        record.compensation_source = None
        return str(exc)
    record.compensation_error = None
    record.compensation_source = "fcs"
    return None


# ----------------------------------------------------------------------
# Applying custom matrices
# ----------------------------------------------------------------------
def validate_matrix(matrix: np.ndarray, detectors: Iterable[str]) -> np.ndarray:
    """Validate and normalize a square compensation matrix."""

    values = np.array(matrix, dtype=float, copy=True)
    detectors = list(detectors)
    if values.ndim != 2 or values.shape[0] != values.shape[1]:
        raise ValueError("补偿矩阵必须是方阵")
    if values.shape[0] != len(detectors):
        raise ValueError(
            f"补偿矩阵大小 {values.shape[0]} 与通道数量 {len(detectors)} 不一致"
        )
    if not np.all(np.isfinite(values)):
        raise ValueError("补偿矩阵包含无效数值")
    if np.any(np.abs(values) > 100.0):
        raise ValueError("补偿矩阵数值过大（绝对值必须 ≤ 100）")
    # FlowJo/FlowKit spillover matrices always have a unit diagonal.
    np.fill_diagonal(values, 1.0)
    return values


def apply_custom_compensation(
    record: "SampleRecord",
    detectors: Iterable[str],
    matrix: np.ndarray,
    fluorochromes: Iterable[str] | None = None,
    source: str = "custom",
) -> Matrix:
    """Apply a custom matrix (with detector labels) to one sample."""

    detectors = list(detectors)
    values = validate_matrix(matrix, detectors)
    if fluorochromes is None:
        sample = record.sample
        by_detector = {
            sample.pnn_labels[index]: (
                sample.pns_labels[index] if index < len(sample.pns_labels) else ""
            )
            for index in sample.fluoro_indices
        }
        fluorochromes = [by_detector.get(det, "") for det in detectors]
    matrix_obj = Matrix(values, detectors, list(fluorochromes))
    record.sample.apply_compensation(matrix_obj)
    record.compensation_source = source
    record.compensation_error = None
    return matrix_obj


def clear_compensation(record: "SampleRecord") -> None:
    """Remove compensation from a sample."""

    record.sample.apply_compensation(None)
    record.compensation_source = None
    record.compensation_error = None


# ----------------------------------------------------------------------
# Matrix file parsing
# ----------------------------------------------------------------------
def _parse_float(value: str) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_fcs_style(text: str) -> tuple[list[str], np.ndarray] | None:
    """Parse a single-line FCS ``n,label,…,values…`` matrix."""

    compact = " ".join(text.split())
    if not compact:
        return None
    parts = [part.strip() for part in compact.split(",")]
    if len(parts) < 2:
        return None
    try:
        count = int(parts[0])
    except ValueError:
        return None
    if count <= 0 or len(parts) != count + 1 + count * count:
        return None
    labels = parts[1 : 1 + count]
    try:
        values = np.asarray(parts[1 + count :], dtype=float).reshape(count, count)
    except ValueError:
        return None
    return labels, values


def parse_matrix_text(text: str) -> tuple[list[str] | None, np.ndarray]:
    """Parse a CSV/TSV compensation matrix.

    Returns ``(labels, matrix)``.  ``labels`` is ``None`` for headerless
    matrices where the order is assumed to match the sample detectors.
    """

    text = (text or "").lstrip("\ufeff")
    fcs_style = _parse_fcs_style(text)
    if fcs_style is not None:
        return fcs_style

    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("补偿矩阵文件为空")
    if "\t" in lines[0] and "," not in lines[0]:
        delimiter = "\t"
    elif ";" in lines[0] and "," not in lines[0]:
        delimiter = ";"
    else:
        delimiter = ","
    rows = [
        [cell.strip() for cell in row]
        for row in csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
        if any(cell.strip() for cell in row)
    ]
    if not rows:
        raise ValueError("无法解析补偿矩阵")

    first = rows[0]
    first_is_numeric = all(_parse_float(cell) is not None for cell in first)

    if first_is_numeric:
        labels = None
        data_rows = rows
    else:
        # Common layouts:
        #   A,B / 1,0 / 0,1                  (header labels, no corner cell)
        #   ,A,B / 1,0,0 / 0,1,0 / 0,0,1      (blank corner cell)
        #   Detector,A,B / Detector,1,0 / …   (corner cell plus row labels)
        data_rows = rows[1:]
        if not data_rows:
            raise ValueError("补偿矩阵没有数据行")
        data_width = len(data_rows[0])
        row_labeled = _parse_float(data_rows[0][0]) is None
        corner = _normalized_label(first[0]) if first else ""
        generic_corner = corner in {
            "",
            "detector",
            "detectors",
            "channel",
            "channels",
            "fluorochrome",
            "fluorochromes",
        }
        if data_width == len(first) - 1:
            labels = first[1:]
        elif data_width == len(first):
            if row_labeled:
                labels = first[1:]
                data_rows = [row[1:] for row in data_rows]
            elif generic_corner:
                labels = first[1:]
            else:
                labels = first
        else:
            raise ValueError("补偿矩阵行列数量不一致")

    try:
        values = np.asarray(
            [[float(cell) for cell in row] for row in data_rows], dtype=float
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("补偿矩阵包含非数值内容") from exc
    if values.ndim != 2 or values.shape[0] != values.shape[1]:
        raise ValueError("补偿矩阵必须是方阵")
    if labels is not None and len(labels) != values.shape[0]:
        raise ValueError("补偿矩阵表头标签数量与矩阵大小不一致")
    return labels, values


def read_matrix_file(path: str | Path) -> tuple[list[str] | None, np.ndarray]:
    path = Path(path)
    return parse_matrix_text(path.read_text(encoding="utf-8-sig", errors="replace"))


def _normalized_label(label: str) -> str:
    return "".join(str(label).strip().lower().split())


def align_matrix_to_detectors(
    labels: Iterable[str] | None,
    matrix: np.ndarray,
    detectors: Iterable[str],
    aliases: dict[str, Iterable[str]] | None = None,
) -> np.ndarray:
    """Reorder/expand an imported matrix to ``detectors`` order.

    Missing detectors are left uncompensated (identity rows/columns).  This
    allows importing a matrix that covers only a subset of the acquired
    fluorescence channels.  ``aliases`` optionally maps a detector name to
    alternative labels (for example its fluorochrome/PnS label).
    """

    detectors = list(detectors)
    values = np.asarray(matrix, dtype=float)
    if values.ndim != 2 or values.shape[0] != values.shape[1]:
        raise ValueError("补偿矩阵必须是方阵")
    source_labels = list(labels) if labels is not None else None

    if source_labels is None:
        if values.shape[0] != len(detectors):
            raise ValueError(
                f"无表头补偿矩阵大小 {values.shape[0]} 与样本荧光通道数量 "
                f"{len(detectors)} 不一致"
            )
        aligned = values.copy()
        np.fill_diagonal(aligned, 1.0)
        return aligned

    lookup: dict[str, int] = {}
    for index, label in enumerate(source_labels):
        lookup.setdefault(_normalized_label(label), index)
    positions: dict[int, int] = {}
    aliases = aliases or {}
    for target_index, detector in enumerate(detectors):
        candidates = [detector, *list(aliases.get(detector, []))]
        source_index = None
        for candidate in candidates:
            source_index = lookup.get(_normalized_label(candidate))
            if source_index is not None:
                break
        if source_index is not None:
            positions[target_index] = source_index
    if not positions:
        raise ValueError("补偿矩阵的通道标签与样本荧光通道不匹配")

    aligned = np.eye(len(detectors), dtype=float)
    target_indices = list(positions)
    for row in target_indices:
        for col in target_indices:
            aligned[row, col] = values[positions[row], positions[col]]
    np.fill_diagonal(aligned, 1.0)
    return aligned


def compensation_payload(record: "SampleRecord") -> dict | None:
    """Serialize the record's compensation state for a project file."""

    info = compensation_info(record)
    if info is None:
        return {"source": "none"}
    if record.compensation_source == "fcs":
        return {"source": "fcs"}
    return info.as_dict()


def restore_compensation(record: "SampleRecord", payload: dict | None) -> str | None:
    """Restore a compensation state from a project payload.

    Returns an error message or ``None`` on success.
    """

    if not payload:
        return None
    source = str(payload.get("source") or "custom")
    if source == "none":
        clear_compensation(record)
        return None
    if source == "fcs":
        if getattr(record.sample, "compensation", None) is not None:
            record.compensation_source = "fcs"
            return None
        return apply_embedded_compensation(record)
    try:
        detectors = [str(value) for value in payload.get("detectors") or []]
        matrix = np.asarray(payload.get("matrix") or [], dtype=float)
        fluorochromes = [str(value) for value in payload.get("fluorochromes") or []]
        if len(fluorochromes) != len(detectors):
            fluorochromes = fluorochromes[: len(detectors)]
            fluorochromes += [""] * (len(detectors) - len(fluorochromes))
        apply_custom_compensation(
            record, detectors, matrix, fluorochromes, source="custom"
        )
    except Exception as exc:
        record.compensation_error = str(exc)
        record.compensation_source = None
        return str(exc)
    return None
