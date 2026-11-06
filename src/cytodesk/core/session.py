"""High level analysis session used by the CytoDesk UI.

This module is deliberately UI independent so it can be tested headlessly.
It wraps FlowKit's ``Sample``, ``GatingStrategy`` and ``GatingResults``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np
from flowkit import GatingStrategy, Sample
from flowkit import gates as fk_gates

from .channels import ChannelInfo, channels_from_sample
from .compensation import (
    apply_custom_compensation,
    apply_embedded_compensation,
    clear_compensation,
    compensation_info,
    compensation_payload,
    restore_compensation,
)
from .gates import (
    gate_dimension_ids,
    gate_from_dict,
    gate_to_dict,
)
from .transforms import (
    TransformSpec,
    default_transform_spec,
    transform_id_for_channel,
)


GATE_PALETTE = [
    "#1f77b4",
    "#d62728",
    "#2ca02c",
    "#ff7f0e",
    "#9467bd",
    "#17becf",
    "#8c564b",
    "#e377c2",
    "#bcbd22",
    "#7f7f7f",
]


@dataclass
class SampleRecord:
    id: str
    path: Path
    sample: Sample
    channels: list[ChannelInfo] = field(default_factory=list)
    compensation_error: str | None = None
    compensation_source: str | None = None

    def __post_init__(self) -> None:
        if not self.channels:
            self.channels = channels_from_sample(self.sample)

    @property
    def event_count(self) -> int:
        return int(getattr(self.sample, "event_count", 0))

    def channel(self, pnn: str) -> ChannelInfo | None:
        for channel in self.channels:
            if channel.pnn == pnn:
                return channel
        return None

    def transform_candidates(self) -> list[ChannelInfo]:
        return [c for c in self.channels if not c.is_null]


@dataclass
class PopulationStat:
    gate_name: str
    full_path: tuple[str, ...]
    gate_type: str
    count: int
    percent_parent: float
    percent_total: float
    medians: dict[str, float] = field(default_factory=dict)
    means: dict[str, float] = field(default_factory=dict)

    @property
    def path_label(self) -> str:
        return " / ".join(self.full_path[1:]) if len(self.full_path) > 1 else self.gate_name


@dataclass
class BatchPopulationStat(PopulationStat):
    """A population statistic belonging to one sample in a batch analysis."""

    sample_id: str = ""
    sample_total: int = 0


@dataclass
class SampleGatingStatus:
    """Outcome of applying the shared gating strategy to one sample."""

    sample_id: str
    ok: bool
    error: str | None = None
    missing_channels: tuple[str, ...] = ()

    @property
    def missing_label(self) -> str:
        if not self.missing_channels:
            return ""
        preview = ", ".join(self.missing_channels[:5])
        if len(self.missing_channels) > 5:
            preview += " …"
        return preview


def load_sample_file(
    path: str | Path,
    sample_id: str | None = None,
    repair: bool = False,
    auto_compensate: bool = True,
) -> SampleRecord:
    """Load an FCS file into a :class:`SampleRecord`.

    ``repair`` enables FlowKit/FlowIO's offset tolerance options for
    non-compliant FCS files.  ``auto_compensate`` applies the FCS file's
    ``$SPILLOVER``/``$SPILL`` matrix, matching FlowJo's default behaviour.
    """

    path = Path(path)
    sample = Sample(
        path,
        sample_id=sample_id or path.stem,
        ignore_offset_error=repair,
        ignore_offset_discrepancy=repair,
        use_header_offsets=repair,
    )
    record = SampleRecord(
        id=sample.id,
        path=path,
        sample=sample,
    )
    if auto_compensate:
        # Missing embedded matrices are normal; errors are kept on the record
        # for the inspector but do not prevent loading the sample.
        apply_embedded_compensation(record)
    return record


def record_has_compensation(record: SampleRecord) -> bool:
    return getattr(record.sample, "compensation", None) is not None


class AnalysisSession:
    """Owns samples, transforms, gates and gating results for one project."""

    def __init__(self, gating_strategy: GatingStrategy | None = None):
        self.gating_strategy = gating_strategy or GatingStrategy()
        self.samples: dict[str, SampleRecord] = {}
        self.transform_specs: dict[str, TransformSpec] = {}
        self.transform_ids: dict[str, str] = {}
        self.gate_colors: dict[str, str] = {}
        self._results: dict[str, object] = {}
        self._gating_errors: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Samples
    # ------------------------------------------------------------------
    def add_sample(
        self,
        path: str | Path,
        sample_id: str | None = None,
        repair: bool = False,
    ) -> SampleRecord:
        record = load_sample_file(path, sample_id=sample_id, repair=repair)
        return self.add_record(record)

    def add_record(self, record: SampleRecord) -> SampleRecord:
        record.id = self._unique_sample_id(record.id or record.path.stem)
        record.sample.id = record.id
        self.samples[record.id] = record
        for channel in record.channels:
            self.ensure_transform(channel)
        return record

    def remove_sample(self, sample_id: str) -> None:
        self.samples.pop(sample_id, None)
        self._results.pop(sample_id, None)
        self._gating_errors.pop(sample_id, None)

    def _unique_sample_id(self, base: str) -> str:
        base = (base or "sample").strip() or "sample"
        if base not in self.samples:
            return base
        i = 2
        while f"{base}_{i}" in self.samples:
            i += 1
        return f"{base}_{i}"

    def get_sample(self, sample_id: str | None) -> SampleRecord | None:
        return self.samples.get(sample_id) if sample_id else None

    def channel_info(self, sample_id: str, pnn: str) -> ChannelInfo | None:
        record = self.samples.get(sample_id)
        return record.channel(pnn) if record else None

    # ------------------------------------------------------------------
    # Compensation
    # ------------------------------------------------------------------
    def compensation_info(self, record: SampleRecord):
        return compensation_info(record)

    def apply_compensation(
        self,
        record: SampleRecord,
        detectors: Iterable[str],
        matrix: np.ndarray,
        fluorochromes: Iterable[str] | None = None,
    ) -> None:
        apply_custom_compensation(record, detectors, matrix, fluorochromes)
        self.invalidate_results()

    def apply_fcs_compensation(self, record: SampleRecord) -> str | None:
        error = apply_embedded_compensation(record)
        self.invalidate_results()
        return error

    def clear_compensation(self, record: SampleRecord) -> None:
        clear_compensation(record)
        self.invalidate_results()

    def restore_compensation(self, record: SampleRecord, payload: dict | None) -> str | None:
        error = restore_compensation(record, payload)
        self.invalidate_results()
        return error

    def compensation_payload(self, record: SampleRecord) -> dict | None:
        return compensation_payload(record)

    # ------------------------------------------------------------------
    # Transforms
    # ------------------------------------------------------------------
    def ensure_transform(self, channel: ChannelInfo) -> str:
        if channel.pnn not in self.transform_specs:
            self.transform_specs[channel.pnn] = default_transform_spec(channel)
        if channel.pnn not in self.transform_ids:
            used = set(self.transform_ids.values())
            self.transform_ids[channel.pnn] = transform_id_for_channel(channel.pnn, used)
        transform_id = self.transform_ids[channel.pnn]
        self.gating_strategy.transformations[transform_id] = self.transform_specs[
            channel.pnn
        ].build()
        return transform_id

    def get_transform(self, channel: str) -> TransformSpec:
        spec = self.transform_specs.get(channel)
        if spec is None:
            # Create a default if this channel was never registered.
            spec = TransformSpec("logicle", {"t": 262144.0, "w": 0.5, "m": 4.5, "a": 0.0})
            self.transform_specs[channel] = spec
        return spec

    def get_transform_id(self, channel: str) -> str:
        if channel not in self.transform_ids:
            info = ChannelInfo(index=0, pnn=channel)
            self.ensure_transform(info)
        return self.transform_ids[channel]

    def set_transform(self, channel: str, spec: TransformSpec) -> None:
        self.transform_specs[channel] = spec
        transform_id = self.get_transform_id(channel)
        self.gating_strategy.transformations[transform_id] = spec.build()
        self.invalidate_results()

    def restore_transforms(self, specs: dict[str, TransformSpec]) -> None:
        for channel, spec in specs.items():
            self.transform_specs[channel] = spec
            transform_id = self.get_transform_id(channel)
            self.gating_strategy.transformations[transform_id] = spec.build()

    # ------------------------------------------------------------------
    # Event access
    # ------------------------------------------------------------------
    def _raw_or_comp_events(self, record: SampleRecord) -> np.ndarray:
        if getattr(record.sample, "compensation", None) is not None:
            return record.sample.get_events(source="comp")
        return record.sample.get_events(source="raw")

    def transformed_channel(
        self,
        record: SampleRecord,
        channel: str,
        events: np.ndarray | None = None,
    ) -> np.ndarray:
        info = record.channel(channel)
        if info is None:
            raise KeyError(f"Channel {channel!r} not found in sample {record.id!r}")
        source = self._raw_or_comp_events(record) if events is None else events
        values = np.asarray(source[:, info.index], dtype=float)
        return self.get_transform(channel).apply(values)

    def transformed_matrix(
        self,
        record: SampleRecord,
        channels: Iterable[str],
    ) -> tuple[dict[str, np.ndarray], np.ndarray]:
        source = self._raw_or_comp_events(record)
        result: dict[str, np.ndarray] = {}
        for channel in channels:
            result[channel] = self.transformed_channel(record, channel, source)
        return result, source

    # ------------------------------------------------------------------
    # Gates
    # ------------------------------------------------------------------
    def iter_gate_nodes(self) -> Iterator[tuple[object, tuple[str, ...]]]:
        """Yield ``(GateNode, full_path)`` pairs in tree pre-order."""

        root = self.gating_strategy._gate_tree
        for node in root.descendants:
            path = tuple(n.name for n in node.path)
            yield node, path

    def gate_full_paths(self) -> list[tuple[str, ...]]:
        return [path for _, path in self.iter_gate_nodes()]

    def gate_by_path(self, full_path: tuple[str, ...]):
        if not full_path or full_path[0] != "root" or len(full_path) < 2:
            return None
        try:
            return self.gating_strategy.get_gate(full_path[-1], full_path[:-1])
        except Exception:
            return None

    def gate_node_by_path(self, full_path: tuple[str, ...]):
        for node, path in self.iter_gate_nodes():
            if path == tuple(full_path):
                return node
        return None

    def child_names(self, parent_path: tuple[str, ...]) -> set[str]:
        return {
            path[-1]
            for path in self.gate_full_paths()
            if path[:-1] == tuple(parent_path)
        }

    def unique_gate_name(self, parent_path: tuple[str, ...]) -> str:
        existing = self.child_names(parent_path)
        i = 1
        while f"Gate {i}" in existing:
            i += 1
        return f"Gate {i}"

    def add_gate(self, gate, parent_path: tuple[str, ...]) -> tuple[str, ...]:
        parent_path = tuple(parent_path)
        if not parent_path:
            parent_path = ("root",)
        self._validate_gate_name(gate.gate_name)
        self.gating_strategy.add_gate(gate, parent_path)
        full_path = parent_path + (gate.gate_name,)
        if "/".join(full_path) not in self.gate_colors:
            idx = len(self.gate_colors) % len(GATE_PALETTE)
            self.gate_colors["/".join(full_path)] = GATE_PALETTE[idx]
        self.invalidate_results()
        return full_path

    def add_gate_from_dict(
        self, data: dict, parent_path: tuple[str, ...]
    ) -> tuple[str, ...]:
        gate = gate_from_dict(data, self._transform_ids_for_channels(data["channels"]))
        return self.add_gate(gate, parent_path)

    def remove_gate(
        self, full_path: tuple[str, ...], keep_children: bool = False
    ) -> None:
        full_path = tuple(full_path)
        self.gating_strategy.remove_gate(
            full_path[-1], gate_path=full_path[:-1], keep_children=keep_children
        )
        prefix = "/".join(full_path)
        for key in list(self.gate_colors):
            if key == prefix or key.startswith(prefix + "/"):
                self.gate_colors.pop(key, None)
        self.invalidate_results()

    def rename_gate(
        self, full_path: tuple[str, ...], new_name: str
    ) -> tuple[str, ...]:
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("门名称不能为空")
        self._validate_gate_name(new_name)
        if new_name in self.child_names(full_path[:-1]):
            raise ValueError(f"同一层级已存在名为 {new_name!r} 的门")
        self.gating_strategy.rename_gate(
            full_path[-1], new_name, gate_path=full_path[:-1]
        )
        new_path = full_path[:-1] + (new_name,)
        old_prefix = "/".join(full_path)
        new_prefix = "/".join(new_path)
        for key in list(self.gate_colors):
            if key == old_prefix:
                self.gate_colors[new_prefix] = self.gate_colors.pop(key)
            elif key.startswith(old_prefix + "/"):
                self.gate_colors[new_prefix + key[len(old_prefix):]] = (
                    self.gate_colors.pop(key)
                )
        self.invalidate_results()
        return new_path

    def gate_color(self, full_path: tuple[str, ...]) -> str:
        key = "/".join(full_path)
        if key not in self.gate_colors:
            idx = len(self.gate_colors) % len(GATE_PALETTE)
            self.gate_colors[key] = GATE_PALETTE[idx]
        return self.gate_colors[key]

    @staticmethod
    def _validate_gate_name(name: str) -> None:
        if not name or name.strip() != name:
            raise ValueError("门名称不能为空，且首尾不能包含空格")
        if "/" in name:
            raise ValueError("门名称不能包含斜杠 '/'")
        if name in {".", ".."}:
            raise ValueError("门名称不能是 '.' 或 '..'")

    def set_gate_color(self, full_path: tuple[str, ...], color: str) -> None:
        self.gate_colors["/".join(full_path)] = color

    def gate_matches_axes(
        self, gate, x_channel: str | None, y_channel: str | None
    ) -> bool:
        ids = gate_dimension_ids(gate)
        if x_channel is None:
            return False
        if len(ids) == 1:
            return ids[0] == x_channel
        return (
            len(ids) == 2
            and y_channel is not None
            and ids[0] == x_channel
            and ids[1] == y_channel
        )

    def _transform_ids_for_channels(self, channels: Iterable[str]) -> dict[str, str]:
        return {channel: self.get_transform_id(channel) for channel in channels}

    # ------------------------------------------------------------------
    # Gating and statistics
    # ------------------------------------------------------------------
    def invalidate_results(self) -> None:
        self._results.clear()
        self._gating_errors.clear()
        try:
            self.gating_strategy.clear_cache()
        except Exception:
            pass

    def gate_sample(self, record: SampleRecord, cache: bool = False):
        results = self.gating_strategy.gate_sample(record.sample, cache_events=cache)
        self._results[record.id] = results
        self._gating_errors.pop(record.id, None)
        return results

    def results_for(self, record: SampleRecord):
        return self._results.get(record.id)

    def gating_error(self, sample_id: str) -> str | None:
        return self._gating_errors.get(sample_id)

    def gated_sample_ids(self) -> set[str]:
        return {
            sample_id
            for sample_id in self._results
            if sample_id in self.samples
        }

    def gate_all(
        self,
        records: Iterable[SampleRecord] | None = None,
        cache: bool = False,
    ) -> list[SampleGatingStatus]:
        """Apply the shared gating strategy to several samples.

        Samples with missing gate channels are reported in the returned
        statuses instead of aborting the whole batch.  Previously computed
        results are replaced for every sample.
        """

        records = list(self.samples.values()) if records is None else list(records)
        statuses: list[SampleGatingStatus] = []
        if not self.has_gates():
            for record in records:
                self._results.pop(record.id, None)
                self._gating_errors.pop(record.id, None)
                statuses.append(SampleGatingStatus(record.id, True))
            return statuses

        for record in records:
            missing = tuple(self.missing_gate_channels(record))
            if missing:
                self._results.pop(record.id, None)
                message = f"缺少门所需通道：{', '.join(missing[:5])}"
                if len(missing) > 5:
                    message += " …"
                self._gating_errors[record.id] = message
                statuses.append(
                    SampleGatingStatus(
                        record.id,
                        False,
                        error=message,
                        missing_channels=missing,
                    )
                )
                continue
            try:
                self.gate_sample(record, cache=cache)
            except Exception as exc:  # pragma: no cover - file dependent
                self._results.pop(record.id, None)
                self._gating_errors[record.id] = str(exc)
                statuses.append(
                    SampleGatingStatus(record.id, False, error=str(exc))
                )
            else:
                statuses.append(SampleGatingStatus(record.id, True))
        return statuses

    def gate_membership(self, record: SampleRecord, full_path: tuple[str, ...]):
        results = self._results.get(record.id)
        if results is None:
            results = self.gate_sample(record)
        return results.get_gate_membership(full_path[-1], full_path[:-1])

    def has_gates(self) -> bool:
        return any(True for _ in self.iter_gate_nodes())

    def gate_channel_ids(self) -> list[str]:
        channels: list[str] = []
        seen: set[str] = set()
        for node, _path in self.iter_gate_nodes():
            for channel in gate_dimension_ids(node.gate):
                if channel and channel not in seen:
                    seen.add(channel)
                    channels.append(channel)
        return channels

    def missing_gate_channels(self, record: SampleRecord) -> list[str]:
        available = {channel.pnn for channel in record.channels}
        return [channel for channel in self.gate_channel_ids() if channel not in available]

    def population_stats(
        self,
        record: SampleRecord,
        stat_channels: Iterable[str] = (),
    ) -> list[PopulationStat]:
        results = self._results.get(record.id)
        if results is None:
            return []
        report = results.report
        if report is None or report.empty:
            return []

        stat_channels = [c for c in dict.fromkeys(stat_channels) if record.channel(c)]
        channel_arrays: dict[str, np.ndarray] = {}
        if stat_channels:
            channel_arrays, _ = self.transformed_matrix(record, stat_channels)

        rows: list[PopulationStat] = []
        for _, row in report.iterrows():
            name = str(row["gate_name"])
            gate_path = row["gate_path"]
            if isinstance(gate_path, str):
                gate_path = tuple(gate_path.split("/"))
            else:
                gate_path = tuple(gate_path)
            full_path = gate_path + (name,)
            try:
                membership = results.get_gate_membership(name, gate_path)
            except Exception:
                membership = None

            medians: dict[str, float] = {}
            means: dict[str, float] = {}
            if membership is not None and membership.any():
                for channel, values in channel_arrays.items():
                    selected = values[membership]
                    medians[channel] = float(np.median(selected))
                    means[channel] = float(np.mean(selected))
            rows.append(
                PopulationStat(
                    gate_name=name,
                    full_path=full_path,
                    gate_type=str(row.get("gate_type", "")),
                    count=int(row["count"]),
                    percent_parent=float(row["relative_percent"]),
                    percent_total=float(row["absolute_percent"]),
                    medians=medians,
                    means=means,
                )
            )
        return rows

    def population_counts(self, record: SampleRecord) -> dict[str, int]:
        """Return ``path -> count`` for every population of one sample."""

        results = self._results.get(record.id)
        if results is None:
            return {}
        report = results.report
        if report is None or report.empty:
            return {}
        counts: dict[str, int] = {}
        for _, row in report.iterrows():
            name = str(row["gate_name"])
            gate_path = row["gate_path"]
            if isinstance(gate_path, str):
                gate_path = tuple(gate_path.split("/"))
            else:
                gate_path = tuple(gate_path)
            counts["/".join(gate_path + (name,))] = int(row["count"])
        return counts

    def all_population_counts(self) -> dict[str, dict[str, int]]:
        return {
            record.id: self.population_counts(record)
            for record in self.samples.values()
        }

    def batch_population_stats(
        self,
        stat_channels: Iterable[str] = (),
        sample_ids: Iterable[str] | None = None,
    ) -> list[BatchPopulationStat]:
        """Population statistics for every gated sample, in sample order."""

        wanted = set(sample_ids) if sample_ids is not None else None
        rows: list[BatchPopulationStat] = []
        for record in self.samples.values():
            if wanted is not None and record.id not in wanted:
                continue
            for stat in self.population_stats(record, stat_channels):
                rows.append(
                    BatchPopulationStat(
                        gate_name=stat.gate_name,
                        full_path=stat.full_path,
                        gate_type=stat.gate_type,
                        count=stat.count,
                        percent_parent=stat.percent_parent,
                        percent_total=stat.percent_total,
                        medians=stat.medians,
                        means=stat.means,
                        sample_id=record.id,
                        sample_total=record.event_count,
                    )
                )
        return rows

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------
    def gate_tree_dicts(self) -> list[dict]:
        """Serialize gates in parent-before-child order."""

        items: list[dict] = []
        for node, path in self.iter_gate_nodes():
            if isinstance(node.gate, fk_gates.Quadrant):
                continue
            data = gate_to_dict(node.gate)
            data["parent_path"] = list(path[:-1])
            data["color"] = self.gate_color(path)
            items.append(data)
        return items

    def sample_dicts(self) -> list[dict]:
        return [
            {
                "id": record.id,
                "path": str(record.path),
                "compensation": self.compensation_payload(record),
            }
            for record in self.samples.values()
        ]

    def transform_dicts(self) -> dict[str, dict]:
        return {channel: spec.to_dict() for channel, spec in self.transform_specs.items()}
