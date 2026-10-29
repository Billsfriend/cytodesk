"""Shared UI label translations."""

GATE_TYPE_LABELS = {
    "RectangleGate": "矩形/范围",
    "PolygonGate": "多边形",
    "EllipsoidGate": "椭圆",
    "QuadrantGate": "象限",
    "BooleanGate": "布尔",
}


def gate_type_label(type_name: str) -> str:
    return GATE_TYPE_LABELS.get(type_name, type_name)
