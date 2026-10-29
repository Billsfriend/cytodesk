"""FCS channel metadata helpers."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


@dataclass(frozen=True)
class ChannelInfo:
    index: int
    pnn: str
    pns: str = ""
    range: float | None = None
    is_scatter: bool = False
    is_time: bool = False
    is_null: bool = False

    @property
    def short_label(self) -> str:
        """A human friendly label, preferring the PnS (stain) label."""

        pns = (self.pns or "").strip()
        pnn = (self.pnn or "").strip()
        if pns and pns.lower() not in {"", "none"} and pns != pnn:
            return pns
        return pnn or f"Channel {self.index + 1}"

    @property
    def label(self) -> str:
        pns = (self.pns or "").strip()
        pnn = (self.pnn or "").strip()
        if pns and pns != pnn:
            return f"{pns} ({pnn})"
        return pnn or f"Channel {self.index + 1}"

    def sort_key(self) -> tuple[int, str]:
        return (self.index, self.pnn)


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _indices(value: Any) -> set[int]:
    if value is None:
        return set()
    try:
        return {int(v) for v in value}
    except (TypeError, ValueError):
        return set()


def channels_from_sample(sample) -> list[ChannelInfo]:
    """Build :class:`ChannelInfo` objects from a FlowKit ``Sample``."""

    pnn_labels = list(getattr(sample, "pnn_labels", []) or [])
    pns_labels = list(getattr(sample, "pns_labels", []) or [])
    metadata = getattr(sample, "metadata", {}) or {}

    scatter = _indices(getattr(sample, "scatter_indices", None))
    time_index = getattr(sample, "time_index", None)
    try:
        time_index = int(time_index) if time_index is not None else None
    except (TypeError, ValueError):
        time_index = None
    null_channels = _indices(getattr(sample, "null_channels", None))

    # Newer FlowIO versions expose a channels DataFrame with a PnR range column.
    ranges: dict[int, float | None] = {}
    frame = getattr(sample, "channels", None)
    if frame is not None and hasattr(frame, "columns"):
        range_column = "pnr" if "pnr" in frame.columns else ("range" if "range" in frame.columns else None)
        if range_column:
            for _, row in frame.iterrows():
                try:
                    ranges[int(row.get("channel_number", 0)) - 1] = _to_float(row.get(range_column))
                except (TypeError, ValueError):
                    continue

    result: list[ChannelInfo] = []
    for i, pnn in enumerate(pnn_labels):
        pns = pns_labels[i] if i < len(pns_labels) else ""
        channel_range = ranges.get(i)
        if channel_range is None:
            channel_range = _to_float(metadata.get(f"$P{i + 1}R"))
        result.append(
            ChannelInfo(
                index=i,
                pnn=pnn,
                pns=pns or "",
                range=channel_range,
                is_scatter=i in scatter,
                is_time=i == time_index,
                is_null=i in null_channels,
            )
        )
    return result
