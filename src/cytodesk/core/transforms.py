"""Channel transform definitions and helpers.

CytoDesk keeps a serializable description of the transform used for each
channel.  The actual transform objects are created from these descriptions and
registered with FlowKit's ``GatingStrategy``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any

import numpy as np
from flowkit import transforms as fk_transforms

from .channels import ChannelInfo


AsinhTransform = fk_transforms.AsinhTransform
HyperlogTransform = fk_transforms.HyperlogTransform
LinearTransform = fk_transforms.LinearTransform
LogTransform = fk_transforms.LogTransform
LogicleTransform = fk_transforms.LogicleTransform


KIND_LABELS: dict[str, str] = {
    "logicle": "Logicle",
    "linear": "Linear",
    "asinh": "Asinh",
    "log": "Log",
    "hyperlog": "Hyperlog",
}


@dataclass
class TransformSpec:
    """A serializable description of a FlowKit transform."""

    kind: str
    params: dict[str, float] = field(default_factory=dict)

    def build(self):
        kind = self.kind
        p = self.params
        if kind == "linear":
            return LinearTransform(p["t"], p.get("a", 0.0))
        if kind == "logicle":
            return LogicleTransform(
                p["t"], p.get("w", 0.5), p.get("m", 4.5), p.get("a", 0.0)
            )
        if kind == "asinh":
            return AsinhTransform(p["t"], p.get("m", 4.5), p.get("a", 0.0))
        if kind == "log":
            return LogTransform(p["t"], p.get("m", 4.5))
        if kind == "hyperlog":
            return HyperlogTransform(
                p["t"], p.get("w", 0.5), p.get("m", 4.5), p.get("a", 0.0)
            )
        raise ValueError(f"Unknown transform kind: {kind}")

    def apply(self, values: np.ndarray) -> np.ndarray:
        arr = np.asarray(values, dtype=float).reshape(-1, 1)
        return self.build().apply(arr).ravel()

    def inverse(self, values: np.ndarray) -> np.ndarray:
        arr = np.asarray(values, dtype=float).reshape(-1, 1)
        return self.build().inverse(arr).ravel()

    def inverse_scalar(self, value: float) -> float:
        try:
            return float(self.inverse(np.array([value]))[0])
        except Exception:
            return float("nan")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "params": dict(self.params)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TransformSpec":
        return cls(kind=data["kind"], params={k: float(v) for k, v in data["params"].items()})

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


def default_transform_spec(channel: ChannelInfo) -> TransformSpec:
    """Return a sensible default transform for a channel.

    Scatter and time channels use a linear scale.  Fluorescence channels use a
    Logicle scale with the top of scale taken from the ``$PnR`` FCS keyword.
    """

    top = channel.range if channel.range and channel.range > 0 else 262144.0
    if channel.is_scatter or channel.is_time:
        return TransformSpec("linear", {"t": float(top), "a": 0.0})
    return TransformSpec(
        "logicle",
        {"t": float(top), "w": 0.5, "m": 4.5, "a": 0.0},
    )


def default_spec_for_kind(kind: str, channel: ChannelInfo) -> TransformSpec:
    top = channel.range if channel.range and channel.range > 0 else 262144.0
    if kind == "linear":
        return TransformSpec("linear", {"t": float(top), "a": 0.0})
    if kind == "logicle":
        return TransformSpec("logicle", {"t": float(top), "w": 0.5, "m": 4.5, "a": 0.0})
    if kind == "asinh":
        return TransformSpec("asinh", {"t": float(top), "m": 4.5, "a": 0.0})
    if kind == "log":
        return TransformSpec("log", {"t": float(top), "m": 4.5})
    if kind == "hyperlog":
        return TransformSpec("hyperlog", {"t": float(top), "w": 0.5, "m": 4.5, "a": 0.0})
    raise ValueError(kind)


def transform_id_for_channel(channel: str, existing: set[str] | None = None) -> str:
    base = "xform_" + re.sub(r"[^A-Za-z0-9_.-]+", "_", channel).strip("_")
    if not base or base == "xform_":
        base = "xform_channel"
    if existing is None or base not in existing:
        return base
    i = 2
    while f"{base}_{i}" in existing:
        i += 1
    return f"{base}_{i}"


def nice_number(value: float) -> str:
    """Format a raw channel value for an axis tick label."""

    if value is None or not math.isfinite(value):
        return ""
    av = abs(value)
    if av == 0:
        return "0"
    if av >= 1e6:
        return f"{value / 1e6:.3g}M"
    if av >= 1e4:
        return f"{value / 1e3:.3g}k"
    if av >= 100:
        return f"{value:.0f}"
    if av >= 1:
        return f"{value:.2g}"
    return f"{value:.2g}"
