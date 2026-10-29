"""Helpers for creating, serializing and manipulating FlowKit gates."""

from __future__ import annotations

from typing import Any, Iterable

import numpy as np
from flowkit import gates as fk_gates
from flowkit import Dimension


def make_dimension(
    channel: str,
    transform_id: str,
    range_min: float | None = None,
    range_max: float | None = None,
    compensation_ref: str = "FCS",
) -> Dimension:
    return Dimension(
        channel,
        compensation_ref=compensation_ref,
        transformation_ref=transform_id,
        range_min=range_min,
        range_max=range_max,
    )


def make_rectangle_gate(
    name: str,
    x_channel: str,
    y_channel: str,
    x_range: tuple[float, float],
    y_range: tuple[float, float],
    x_transform: str,
    y_transform: str,
) -> fk_gates.RectangleGate:
    dims = [
        make_dimension(x_channel, x_transform, x_range[0], x_range[1]),
        make_dimension(y_channel, y_transform, y_range[0], y_range[1]),
    ]
    return fk_gates.RectangleGate(name, dims)


def make_range_gate(
    name: str,
    channel: str,
    value_range: tuple[float, float],
    transform_id: str,
) -> fk_gates.RectangleGate:
    dim = make_dimension(channel, transform_id, value_range[0], value_range[1])
    return fk_gates.RectangleGate(name, [dim])


def make_polygon_gate(
    name: str,
    x_channel: str,
    y_channel: str,
    vertices: Iterable[tuple[float, float]],
    x_transform: str,
    y_transform: str,
) -> fk_gates.PolygonGate:
    dims = [
        make_dimension(x_channel, x_transform),
        make_dimension(y_channel, y_transform),
    ]
    return fk_gates.PolygonGate(name, dims, [(float(x), float(y)) for x, y in vertices])


def make_ellipse_gate(
    name: str,
    x_channel: str,
    y_channel: str,
    center: tuple[float, float],
    half_width: float,
    half_height: float,
    x_transform: str,
    y_transform: str,
) -> fk_gates.EllipsoidGate:
    dims = [
        make_dimension(x_channel, x_transform),
        make_dimension(y_channel, y_transform),
    ]
    covariance = [[float(half_width) ** 2, 0.0], [0.0, float(half_height) ** 2]]
    return fk_gates.EllipsoidGate(
        name,
        dims,
        [float(center[0]), float(center[1])],
        covariance,
        1.0,
    )


def gate_dimension_ids(gate) -> list[str]:
    ids = []
    for dim in gate.dimensions or []:
        ids.append(getattr(dim, "id", None) or getattr(dim, "dimension_ref", ""))
    return ids


def gate_dimensions(gate) -> list[Dimension]:
    return list(gate.dimensions or [])


def gate_type(gate) -> str:
    return getattr(gate, "gate_type", type(gate).__name__)


def gate_is_2d(gate) -> bool:
    return len(gate_dimension_ids(gate)) == 2


def gate_bounds(gate) -> tuple[float, float, float, float] | None:
    """Return (x_min, x_max, y_min, y_max) in transformed coordinates."""

    gtype = gate_type(gate)
    if gtype == "RectangleGate":
        dims = gate_dimensions(gate)
        if len(dims) != 2:
            return None
        x0 = dims[0].min if dims[0].min is not None else -np.inf
        x1 = dims[0].max if dims[0].max is not None else np.inf
        y0 = dims[1].min if dims[1].min is not None else -np.inf
        y1 = dims[1].max if dims[1].max is not None else np.inf
        return float(x0), float(x1), float(y0), float(y1)

    if gtype == "PolygonGate":
        verts = np.asarray(gate.vertices, dtype=float)
        if verts.ndim != 2 or verts.shape[1] < 2 or len(verts) == 0:
            return None
        return (
            float(verts[:, 0].min()),
            float(verts[:, 0].max()),
            float(verts[:, 1].min()),
            float(verts[:, 1].max()),
        )

    if gtype == "EllipsoidGate":
        center = np.asarray(gate.coordinates, dtype=float)
        cov = np.asarray(gate.covariance_matrix, dtype=float)
        if center.size != 2 or cov.shape != (2, 2):
            return None
        vals, vecs = np.linalg.eigh(cov)
        vals = np.clip(vals, 0, None)
        radii = np.sqrt(vals * float(gate.distance_square))
        extent_x = float(np.hypot(vecs[0, 0] * radii[0], vecs[0, 1] * radii[1]))
        extent_y = float(np.hypot(vecs[1, 0] * radii[0], vecs[1, 1] * radii[1]))
        return (
            float(center[0] - extent_x),
            float(center[0] + extent_x),
            float(center[1] - extent_y),
            float(center[1] + extent_y),
        )

    return None


def ellipse_outline(gate, count: int = 96) -> np.ndarray:
    center = np.asarray(gate.coordinates, dtype=float)
    cov = np.asarray(gate.covariance_matrix, dtype=float)
    vals, vecs = np.linalg.eigh(cov)
    vals = np.clip(vals, 0, None)
    radii = np.sqrt(vals * float(gate.distance_square))
    angles = np.linspace(0.0, 2.0 * np.pi, count)
    unit = np.vstack([np.cos(angles), np.sin(angles)])
    points = (vecs @ (radii[:, None] * unit)).T + center
    return points


def translate_gate(gate, dx: float, dy: float) -> None:
    """Move a gate in place by a delta in transformed coordinates."""

    gtype = gate_type(gate)
    if gtype == "RectangleGate":
        dims = gate_dimensions(gate)
        if len(dims) >= 2:
            for dim, delta in ((dims[0], dx), (dims[1], dy)):
                if dim.min is not None:
                    dim.min += delta
                if dim.max is not None:
                    dim.max += delta
        elif len(dims) == 1:
            dim = dims[0]
            if dim.min is not None:
                dim.min += dx
            if dim.max is not None:
                dim.max += dx
    elif gtype == "PolygonGate":
        gate.vertices = [(float(x) + dx, float(y) + dy) for x, y in gate.vertices]
    elif gtype == "EllipsoidGate":
        gate.coordinates = [
            float(gate.coordinates[0]) + dx,
            float(gate.coordinates[1]) + dy,
        ]


def set_gate_vertex(gate, index: int, x: float, y: float) -> None:
    """Update one control vertex of a gate.

    For rectangles, index 0/1/2/3 are the corners in
    (x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max) order.
    For ellipses, index 0 is the center.
    """

    gtype = gate_type(gate)
    if gtype == "PolygonGate":
        verts = [list(v) for v in gate.vertices]
        if 0 <= index < len(verts):
            verts[index] = [float(x), float(y)]
            gate.vertices = [tuple(v) for v in verts]
    elif gtype == "RectangleGate":
        dims = gate_dimensions(gate)
        if len(dims) < 2:
            return
        x0 = dims[0].min if dims[0].min is not None else -np.inf
        x1 = dims[0].max if dims[0].max is not None else np.inf
        y0 = dims[1].min if dims[1].min is not None else -np.inf
        y1 = dims[1].max if dims[1].max is not None else np.inf
        if index == 0:
            x0, y0 = x, y
        elif index == 1:
            x1, y0 = x, y
        elif index == 2:
            x1, y1 = x, y
        elif index == 3:
            x0, y1 = x, y
        if np.isfinite(x0) and np.isfinite(x1) and x0 > x1:
            x0, x1 = x1, x0
        if np.isfinite(y0) and np.isfinite(y1) and y0 > y1:
            y0, y1 = y1, y0
        dims[0].min, dims[0].max = x0, x1
        dims[1].min, dims[1].max = y0, y1
    elif gtype == "EllipsoidGate":
        if index == 0:
            gate.coordinates = [float(x), float(y)]
        elif index >= 1:
            center = np.asarray(gate.coordinates, dtype=float)
            cov = np.asarray(gate.covariance_matrix, dtype=float)
            vals, vecs = np.linalg.eigh(cov)
            vals = np.clip(vals, 0, None)
            axis = (index - 1) // 2
            if axis > 1:
                return
            relative = np.array([float(x), float(y)]) - center
            projection = float(vecs[:, axis] @ relative)
            distance_square = float(gate.distance_square)
            if distance_square <= 0:
                return
            vals[axis] = (projection**2) / distance_square
            new_cov = vecs @ np.diag(vals) @ vecs.T
            gate.covariance_matrix = [
                [float(new_cov[0, 0]), float(new_cov[0, 1])],
                [float(new_cov[1, 0]), float(new_cov[1, 1])],
            ]


def gate_handle_points(gate) -> list[tuple[float, float]]:
    """Control points used by the plot editor."""

    gtype = gate_type(gate)
    if gtype == "PolygonGate":
        return [(float(x), float(y)) for x, y in gate.vertices]
    if gtype == "RectangleGate":
        bounds = gate_bounds(gate)
        if bounds is None:
            return []
        x0, x1, y0, y1 = bounds
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if gtype == "EllipsoidGate":
        center = gate.coordinates
        vals, vecs = np.linalg.eigh(np.asarray(gate.covariance_matrix, dtype=float))
        radii = np.sqrt(np.clip(vals, 0, None) * float(gate.distance_square))
        points = [tuple(float(v) for v in center)]
        for i in range(2):
            direction = vecs[:, i]
            for sign in (-1, 1):
                point = np.asarray(center) + sign * radii[i] * direction
                points.append(tuple(float(v) for v in point))
        return points
    return []


def gate_to_dict(gate) -> dict[str, Any]:
    gtype = gate_type(gate)
    data: dict[str, Any] = {
        "name": gate.gate_name,
        "type": gtype,
        "channels": gate_dimension_ids(gate),
        "transforms": [dim.transformation_ref for dim in gate_dimensions(gate)],
        "compensations": [dim.compensation_ref for dim in gate_dimensions(gate)],
    }
    if gtype == "RectangleGate":
        data["ranges"] = [
            [
                None if dim.min is None else float(dim.min),
                None if dim.max is None else float(dim.max),
            ]
            for dim in gate_dimensions(gate)
        ]
    elif gtype == "PolygonGate":
        data["vertices"] = [[float(x), float(y)] for x, y in gate.vertices]
    elif gtype == "EllipsoidGate":
        data["coordinates"] = [float(v) for v in gate.coordinates]
        data["covariance"] = [
            [float(v) for v in row] for row in gate.covariance_matrix
        ]
        data["distance_square"] = float(gate.distance_square)
    return data


def gate_from_dict(
    data: dict[str, Any],
    transform_ids: dict[str, str],
) -> Any:
    gtype = data["type"]
    name = data["name"]
    channels = data["channels"]
    xf = [transform_ids.get(ch) for ch in channels]
    comp = data.get("compensations") or ["FCS"] * len(channels)
    if len(comp) < len(channels):
        comp = list(comp) + ["FCS"] * (len(channels) - len(comp))
    if gtype == "RectangleGate":
        ranges = data.get("ranges", [])
        dims = []
        for i, ch in enumerate(channels):
            lo, hi = (ranges[i] if i < len(ranges) else (None, None))
            dims.append(make_dimension(ch, xf[i], lo, hi, comp[i]))
        return fk_gates.RectangleGate(name, dims)
    if gtype == "PolygonGate":
        dims = [make_dimension(ch, xf[i], compensation_ref=comp[i]) for i, ch in enumerate(channels)]
        return fk_gates.PolygonGate(name, dims, [tuple(v) for v in data["vertices"]])
    if gtype == "EllipsoidGate":
        dims = [make_dimension(ch, xf[i], compensation_ref=comp[i]) for i, ch in enumerate(channels)]
        return fk_gates.EllipsoidGate(
            name,
            dims,
            [float(v) for v in data["coordinates"]],
            [[float(v) for v in row] for row in data["covariance"]],
            float(data["distance_square"]),
        )
    raise ValueError(f"Unsupported gate type for project files: {gtype}")
