"""Interactive scatter / density plot with FlowKit gate editing."""

from __future__ import annotations

from enum import Enum
import math

import numpy as np
import pyqtgraph as pg
from contourpy import contour_generator
from PySide6 import QtCore, QtGui, QtWidgets
from scipy.ndimage import gaussian_filter

from ..core.gates import (
    ellipse_outline,
    gate_bounds,
    gate_dimension_ids,
    gate_handle_points,
    gate_type,
    set_gate_vertex,
    translate_gate,
)
from ..core.session import AnalysisSession, SampleRecord
from ..core.transforms import TransformSpec, nice_number


MAX_DISPLAY_POINTS = 300_000
MAX_HIGHLIGHT_POINTS = 25_000
CONTOUR_LEVELS = (0.05, 0.10, 0.18, 0.30, 0.45, 0.62)


class GateTool(str, Enum):
    RECTANGLE = "rectangle"
    POLYGON = "polygon"
    ELLIPSE = "ellipse"
    RANGE = "range"


class TransformedAxis(pg.AxisItem):
    """An axis whose tick labels show inverse-transformed (raw) values."""

    def __init__(self, spec, orientation: str):
        super().__init__(orientation=orientation)
        self._spec = spec
        self._transform = spec.build() if spec is not None else None
        self.enableAutoSIPrefix(False)

    def set_spec(self, spec) -> None:
        self._spec = spec
        self._transform = spec.build() if spec is not None else None
        self.update()

    def tickStrings(self, values, scale, spacing):
        if self._transform is None:
            return ["" for _ in values]
        out = []
        for value in values:
            try:
                raw = float(self._transform.inverse(np.array([[float(value)]]))[0][0])
                out.append(nice_number(raw))
            except Exception:
                out.append("")
        return out


class _GateViewBox(pg.ViewBox):
    def __init__(self, panel: "PlotPanel"):
        super().__init__()
        self.panel = panel
        self.setMenuEnabled(False)

    def mouseDragEvent(self, ev, axis=None):
        if self.panel.on_drag(ev):
            ev.accept()
            return
        super().mouseDragEvent(ev, axis=axis)


def _qcolor(hex_color: str, alpha: int = 255) -> QtGui.QColor:
    color = QtGui.QColor(hex_color)
    color.setAlpha(alpha)
    return color


def _subsample_indices(n: int, limit: int, seed: int = 42) -> np.ndarray:
    if n <= limit:
        return np.arange(n)
    rng = np.random.default_rng(seed)
    return np.sort(rng.choice(n, size=limit, replace=False))


class PlotPanel(QtWidgets.QWidget):
    """A single plot panel.

    The panel renders transformed channel values.  Gate coordinates therefore
    map directly between the FlowKit gate objects and plot data coordinates.
    """

    activated = QtCore.Signal(object)
    gate_drawn = QtCore.Signal(dict)
    gate_selected = QtCore.Signal(object)
    gate_changed = QtCore.Signal(object)
    gate_menu_requested = QtCore.Signal(object, object)
    gate_child_requested = QtCore.Signal(object)
    cursor_moved = QtCore.Signal(str)

    def __init__(self, session: AnalysisSession, parent=None):
        super().__init__(parent)
        self.session = session
        self.record: SampleRecord | None = None
        self.x_channel: str | None = None
        self.y_channel: str | None = None
        self.mode = "scatter"
        self.tool = GateTool.ELLIPSE
        self.show_contours = True
        self.selected_gate_path: tuple[str, ...] | None = None
        self._gate_percentages: dict[str, float] = {}

        self._x = np.empty(0)
        self._y = np.empty(0)
        self._display_idx = np.empty(0, dtype=int)
        self._view_indices: np.ndarray | None = None
        self._population_mask: np.ndarray | None = None
        self._population_path: tuple[str, ...] | None = None
        self._population_label: str = ""
        self._x_bounds = (0.0, 1.0)
        self._y_bounds = (0.0, 1.0)
        self._gate_items: dict[tuple[str, ...], QtWidgets.QGraphicsPathItem] = {}
        self._gate_labels: list[pg.TextItem] = []
        self._handle_items: list[tuple[int, QtWidgets.QGraphicsEllipseItem]] = []
        self._contour_items: list[QtWidgets.QGraphicsPathItem] = []

        self._drawing = False
        self._draw_start: QtCore.QPointF | None = None
        self._draw_current: QtCore.QPointF | None = None
        self._polygon_points: list[tuple[float, float]] = []
        self._drag_gate: tuple[str, ...] | None = None
        self._drag_handle: int | None = None
        self._drag_last: tuple[float, float] | None = None

        self._preview_item: QtWidgets.QGraphicsPathItem | None = None

        self.x_axis = TransformedAxis(None, "bottom")
        self.y_axis = TransformedAxis(None, "left")
        self.view_box = _GateViewBox(self)
        self.plot_widget = pg.PlotWidget(
            viewBox=self.view_box,
            axisItems={"bottom": self.x_axis, "left": self.y_axis},
            background="w",
        )
        self.plot_item = self.plot_widget.getPlotItem()
        self.plot_item.setMenuEnabled(False)
        self.plot_item.hideButtons()
        self.plot_item.showGrid(x=True, y=True, alpha=0.12)
        self.plot_item.setContentsMargins(6, 6, 6, 6)
        self.plot_widget.setFocusPolicy(QtCore.Qt.StrongFocus)

        self._scatter = pg.ScatterPlotItem(
            size=2.0,
            pen=None,
            brush=pg.mkBrush(35, 35, 35, 60),
            pxMode=True,
            antialias=False,
        )
        self._scatter.setZValue(1)
        self.plot_item.addItem(self._scatter)

        self._highlight = pg.ScatterPlotItem(
            size=4.0, pen=None, brush=pg.mkBrush(214, 39, 40, 220), pxMode=True
        )
        self._highlight.setZValue(3)
        self.plot_item.addItem(self._highlight)

        self._density = pg.ImageItem(axisOrder="col-major")
        self._density.setZValue(1)
        self._density.setVisible(False)
        self._density.setOpts(autoDownsample=True)
        self.plot_item.addItem(self._density)
        self._build_colormap()

        self._histogram = pg.PlotDataItem(
            stepMode="center",
            fillLevel=0.0,
            brush=pg.mkBrush(45, 110, 200, 80),
            pen=pg.mkPen("#2d6cdf", width=1.2),
        )
        self._histogram.setZValue(1)
        self._histogram.setVisible(False)
        self.plot_item.addItem(self._histogram)
        self._hist_edges: np.ndarray | None = None
        self._hist_counts: np.ndarray | None = None

        self._empty_label = pg.TextItem(
            "打开 FCS 文件并选择 X/Y 通道", color=(120, 120, 120), anchor=(0.5, 0.5)
        )
        self._empty_label.setZValue(30)
        self.plot_item.addItem(self._empty_label)
        self._empty_label.setPos(0.5, 0.5)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot_widget)

        self.plot_widget.scene().sigMouseMoved.connect(self._on_scene_mouse_moved)
        self.plot_widget.scene().sigMouseClicked.connect(self._on_scene_clicked)
        self.plot_widget.viewport().installEventFilter(self)
        self.plot_item.vb.sigRangeChanged.connect(self._on_range_changed)

    # ------------------------------------------------------------------
    # Public state
    # ------------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if mode == "histogram":
            if self.tool != GateTool.RANGE:
                self.set_tool(GateTool.RANGE)
        elif self.tool == GateTool.RANGE:
            self.set_tool(GateTool.ELLIPSE)
        if mode == "density" and len(self._x):
            self._render_density()
        elif mode == "histogram" and len(self._x):
            self._render_histogram()
        else:
            self._clear_contours()
        self._update_visibility()
        self._update_axes()
        self._set_view_bounds()
        self._render_gates()
        self._highlight.setData([], [])

    def set_tool(self, tool: GateTool | str) -> None:
        self.tool = GateTool(tool)
        self.cancel_drawing()
        self.plot_widget.setCursor(QtCore.Qt.CrossCursor)

    def set_show_contours(self, show: bool) -> None:
        self.show_contours = bool(show)
        if self.mode == "density" and self.show_contours:
            self._render_contours()
        else:
            self._clear_contours()

    def set_population_context(
        self,
        mask: np.ndarray | None,
        path: tuple[str, ...] | None,
        label: str,
        reload: bool = True,
    ) -> None:
        self._population_mask = None if mask is None else np.asarray(mask, dtype=bool)
        self._population_path = tuple(path) if path else None
        self._population_label = label or ""
        self.selected_gate_path = None
        self._highlight.setData([], [])
        if reload and self.record is not None and self.x_channel and self.y_channel:
            self._load_events()
            self._update_axes()
            self.refresh_gates()

    def set_channels(self, x_channel: str | None, y_channel: str | None) -> None:
        self.x_channel = x_channel
        self.y_channel = y_channel
        if self.record is not None and x_channel and y_channel:
            self._load_events()
            self._update_axes()
        else:
            self._x = np.empty(0)
            self._y = np.empty(0)
            self._display_idx = np.empty(0, dtype=int)
            self._scatter.setData([], [])
            self._highlight.setData([], [])
            self._histogram.setData([], [])
            self._hist_counts = None
            self._hist_edges = None
            self._density.setVisible(False)
            self._clear_contours()
            self._empty_label.setVisible(True)
        self.refresh_gates()

    def set_record(self, record: SampleRecord | None) -> None:
        self.record = record
        self.selected_gate_path = None
        if record is None:
            self._x = np.empty(0)
            self._y = np.empty(0)
            self._display_idx = np.empty(0, dtype=int)
            self._scatter.setData([], [])
            self._highlight.setData([], [])
            self._histogram.setData([], [])
            self._hist_counts = None
            self._hist_edges = None
            self._density.setVisible(False)
            self._clear_contours()
            self._empty_label.setVisible(True)
            self.refresh_gates()
            return
        if self.x_channel is None or self.x_channel not in [c.pnn for c in record.channels]:
            candidates = [c.pnn for c in record.transform_candidates()]
            self.x_channel = candidates[0] if candidates else None
        if self.y_channel is None or self.y_channel not in [c.pnn for c in record.channels]:
            candidates = [c.pnn for c in record.transform_candidates()]
            self.y_channel = candidates[1] if len(candidates) > 1 else self.x_channel
        if self.x_channel and self.y_channel:
            self._load_events()
            self._update_axes()
        self.refresh_gates()

    def refresh_events(self) -> None:
        if self.record is not None and self.x_channel and self.y_channel:
            self._load_events()
            self._update_axes()
        self.refresh_gates()

    def refresh_gates(self) -> None:
        self._render_gates()
        self._update_highlight()

    def set_selected_gate(self, full_path: tuple[str, ...] | None) -> None:
        self.selected_gate_path = tuple(full_path) if full_path else None
        self._render_gates()
        self._update_highlight()

    def set_gate_percentages(self, percentages: dict[str, float]) -> None:
        self._gate_percentages = percentages
        self._render_gates()

    def clear(self) -> None:
        self.record = None
        self._x = np.empty(0)
        self._y = np.empty(0)
        self._view_indices = None
        self._population_mask = None
        self._population_path = None
        self._population_label = ""
        self._scatter.setData([], [])
        self._highlight.setData([], [])
        self._histogram.setData([], [])
        self._hist_counts = None
        self._hist_edges = None
        self._density.setVisible(False)
        self._empty_label.setVisible(True)
        self._clear_contours()
        self.refresh_gates()

    def reset_view(self) -> None:
        if len(self._x):
            self._set_view_bounds()

    def begin_vector_export(self):
        """Temporarily thin scatter points for a manageable SVG/PDF export."""

        if self.mode != "scatter" or len(self._display_idx) == 0:
            return None
        idx = self._display_idx
        step = max(1, len(idx) // 5_000)
        reduced = idx[::step]
        self._scatter.setData(x=self._x[reduced], y=self._y[reduced])
        return idx

    def end_vector_export(self, state) -> None:
        if state is None:
            return
        self._scatter.setData(x=self._x[state], y=self._y[state])

    # ------------------------------------------------------------------
    # Data and rendering
    # ------------------------------------------------------------------
    def _load_events(self) -> None:
        if self.record is None or not self.x_channel or not self.y_channel:
            return
        try:
            matrix, _ = self.session.transformed_matrix(
                self.record, [self.x_channel, self.y_channel]
            )
            full_x = np.asarray(matrix[self.x_channel], dtype=float)
            full_y = np.asarray(matrix[self.y_channel], dtype=float)
            if (
                self._population_mask is not None
                and len(self._population_mask) == len(full_x)
            ):
                self._view_indices = np.flatnonzero(self._population_mask)
            else:
                self._view_indices = np.arange(len(full_x))
            x = full_x[self._view_indices]
            y = full_y[self._view_indices]
        except Exception:
            x = np.empty(0)
            y = np.empty(0)
            self._view_indices = np.empty(0, dtype=int)
        self._x, self._y = x, y
        self._display_idx = _subsample_indices(len(x), MAX_DISPLAY_POINTS)
        self._x_bounds, self._y_bounds = self._compute_bounds(x, y)
        self._empty_label.setVisible(len(x) == 0)
        self._render_events()
        self._set_view_bounds()

    def _compute_bounds(self, x: np.ndarray, y: np.ndarray) -> tuple[tuple[float, float], tuple[float, float]]:
        if len(x) == 0:
            return (0.0, 1.0), (0.0, 1.0)

        def bounds(values: np.ndarray) -> tuple[float, float]:
            finite = values[np.isfinite(values)]
            if len(finite) == 0:
                return 0.0, 1.0
            lo, hi = np.percentile(finite, [0.1, 99.9])
            if lo == hi:
                lo, hi = float(finite.min()), float(finite.max())
            if lo == hi:
                lo, hi = lo - 0.5, hi + 0.5
            pad = (hi - lo) * 0.04
            return float(lo - pad), float(hi + pad)

        return bounds(x), bounds(y)

    def _render_events(self) -> None:
        if len(self._x) == 0:
            self._scatter.setData([], [])
            self._density.setVisible(False)
            self._histogram.setVisible(False)
            self._hist_counts = None
            self._hist_edges = None
            self._clear_contours()
            self._empty_label.setVisible(True)
            return
        idx = self._display_idx
        self._scatter.setData(x=self._x[idx], y=self._y[idx])
        if self.mode == "density":
            self._render_density()
        elif self.mode == "histogram":
            self._clear_contours()
            self._render_histogram()
        else:
            self._clear_contours()
        self._update_visibility()

    def _render_histogram(self) -> None:
        if len(self._x) == 0:
            self._histogram.setData([], [])
            return
        x0, x1 = self._x_bounds
        bins = min(512, max(32, int(np.sqrt(len(self._x)))))
        counts, edges = np.histogram(self._x, bins=bins, range=(x0, x1))
        self._hist_edges = edges
        self._hist_counts = counts
        self._histogram.setData(x=edges, y=counts, stepMode="center", fillLevel=0.0)
        self._histogram.setVisible(self.mode == "histogram")

    def _render_density(self) -> None:
        if len(self._x) == 0:
            self._density.setVisible(False)
            self._clear_contours()
            return
        idx = self._display_idx
        x = self._x[idx]
        y = self._y[idx]
        x0, x1 = self._x_bounds
        y0, y1 = self._y_bounds
        bins = 200
        hist, _, _ = np.histogram2d(
            x,
            y,
            bins=bins,
            range=[[x0, x1], [y0, y1]],
        )
        hist = gaussian_filter(hist, sigma=2.5)
        image = np.log1p(hist)
        peak = float(image.max()) if image.size else 0.0
        if peak <= 0:
            self._density.setVisible(False)
            self._clear_contours()
            return
        image = image / peak
        image[image < 0.06] = 0.0
        self._density.setImage(image, autoLevels=False, levels=(0.0, 1.0))
        self._density.setRect(
            QtCore.QRectF(x0, y0, x1 - x0, y1 - y0)
        )
        self._density.setVisible(self.mode == "density")
        if self.show_contours and self.mode == "density":
            self._render_contours()
        else:
            self._clear_contours()

    def _render_contours(self) -> None:
        self._clear_contours()
        if (
            not self.show_contours
            or self.mode != "density"
            or len(self._x) == 0
            or len(self._display_idx) == 0
        ):
            return
        idx = self._display_idx
        x = self._x[idx]
        y = self._y[idx]
        x0, x1 = self._x_bounds
        y0, y1 = self._y_bounds
        bins = 200
        hist, x_edges, y_edges = np.histogram2d(
            x,
            y,
            bins=bins,
            range=[[x0, x1], [y0, y1]],
        )
        hist = gaussian_filter(hist, sigma=2.2)
        peak = float(hist.max()) if hist.size else 0.0
        if peak <= 0:
            return
        x_centers = (x_edges[:-1] + x_edges[1:]) / 2.0
        y_centers = (y_edges[:-1] + y_edges[1:]) / 2.0
        x_span = max(x1 - x0, 1e-12)
        y_span = max(y1 - y0, 1e-12)
        min_area = x_span * y_span * 0.0015
        generator = contour_generator(
            x=x_centers,
            y=y_centers,
            z=hist.T,
            name="serial",
        )
        for fraction in CONTOUR_LEVELS:
            level = peak * fraction
            for line in generator.lines(level):
                if line is None or len(line) < 8:
                    continue
                area = 0.5 * abs(
                    np.dot(line[:, 0], np.roll(line[:, 1], -1))
                    - np.dot(line[:, 1], np.roll(line[:, 0], -1))
                )
                if area < min_area:
                    continue
                path = QtGui.QPainterPath()
                path.moveTo(float(line[0][0]), float(line[0][1]))
                for point in line[1:]:
                    path.lineTo(float(point[0]), float(point[1]))
                path.closeSubpath()
                item = QtWidgets.QGraphicsPathItem(path)
                item.setPen(
                    pg.mkPen(
                        _qcolor("#26303c", 190),
                        width=1.1,
                        cosmetic=True,
                    )
                )
                item.setBrush(pg.mkBrush(0, 0, 0, 0))
                item.setZValue(6)
                self.plot_item.addItem(item, ignoreBounds=True)
                self._contour_items.append(item)
                if len(self._contour_items) >= 80:
                    return

    def _clear_contours(self) -> None:
        for item in self._contour_items:
            try:
                self.plot_item.removeItem(item)
            except Exception:
                pass
        self._contour_items.clear()

    def _build_colormap(self) -> None:
        colors = [
            (255, 255, 255),
            (205, 230, 255),
            (105, 170, 235),
            (35, 90, 170),
            (95, 20, 135),
            (200, 30, 70),
            (255, 195, 35),
        ]
        cmap = pg.ColorMap(np.linspace(0.0, 1.0, len(colors)), colors)
        self._density.setLookupTable(cmap.getLookupTable(0.0, 1.0, 256))

    def _update_visibility(self) -> None:
        show_density = self.mode == "density"
        show_histogram = self.mode == "histogram"
        self._density.setVisible(show_density and len(self._x) > 0)
        self._scatter.setVisible(not show_density and not show_histogram and len(self._x) > 0)
        self._histogram.setVisible(show_histogram and len(self._x) > 0)
        self._empty_label.setVisible(len(self._x) == 0)

    def _set_view_bounds(self) -> None:
        x0, x1 = self._x_bounds
        if self.mode == "histogram" and self._hist_counts is not None and len(self._hist_counts):
            peak = float(self._hist_counts.max())
            self.plot_item.setXRange(x0, x1, padding=0)
            self.plot_item.setYRange(0.0, max(peak * 1.1, 1.0), padding=0)
            return
        y0, y1 = self._y_bounds
        self.plot_item.setXRange(x0, x1, padding=0)
        self.plot_item.setYRange(y0, y1, padding=0)

    def _update_axes(self) -> None:
        if self.record is None:
            return
        xinfo = self.record.channel(self.x_channel) if self.x_channel else None
        yinfo = self.record.channel(self.y_channel) if self.y_channel else None
        if self.x_channel:
            xspec = self.session.get_transform(self.x_channel)
            self.x_axis.set_spec(xspec)
            label = xinfo.short_label if xinfo else self.x_channel
            self.plot_item.setLabel("bottom", f"{label} · {xspec.label}")
        if self.mode == "histogram":
            self.y_axis.set_spec(TransformSpec("linear", {"t": 1.0, "a": 0.0}))
            self.plot_item.setLabel("left", "事件数")
        elif self.y_channel:
            yspec = self.session.get_transform(self.y_channel)
            self.y_axis.set_spec(yspec)
            label = yinfo.short_label if yinfo else self.y_channel
            self.plot_item.setLabel("left", f"{label} · {yspec.label}")
        if self.record:
            event_count = len(self._x)
            label = self._population_label or self.record.id
            if self.mode in {"scatter", "density"} and event_count > MAX_DISPLAY_POINTS:
                detail = f"显示 {MAX_DISPLAY_POINTS:,} / {event_count:,} 事件"
            else:
                detail = f"{event_count:,} 事件"
            self.plot_item.setTitle(
                f"<span style='color:#333;font-size:10pt'>{label}"
                f" &nbsp;·&nbsp; {detail}</span>"
            )

    def _on_range_changed(self, *_args) -> None:
        # Keep single-dimension range gates visually spanning the current view.
        for path, item in self._gate_items.items():
            gate = self.session.gate_by_path(path)
            if gate is not None and len(gate_dimension_ids(gate)) == 1:
                self._update_gate_item_geometry(path, gate, item)

    # ------------------------------------------------------------------
    # Gate rendering
    # ------------------------------------------------------------------
    def _render_gates(self) -> None:
        for item in list(self._gate_items.values()):
            self.plot_item.removeItem(item)
        self._gate_items.clear()
        for label in self._gate_labels:
            self.plot_item.removeItem(label)
        self._gate_labels.clear()
        for _idx, handle in self._handle_items:
            self.plot_item.removeItem(handle)
        self._handle_items.clear()

        if self.record is None:
            return

        for node, full_path in self.session.iter_gate_nodes():
            if full_path[:-1] != (self._population_path or ("root",)):
                continue
            gate = node.gate
            ids = gate_dimension_ids(gate)
            if self.mode == "histogram":
                if len(ids) != 1 or ids[0] != self.x_channel:
                    continue
            elif not self.session.gate_matches_axes(
                gate, self.x_channel, self.y_channel
            ):
                continue
            color = self.session.gate_color(full_path)
            selected = self.selected_gate_path == full_path
            path_item = QtWidgets.QGraphicsPathItem()
            path_item.setPen(
                pg.mkPen(
                    color,
                    width=3.0 if selected else 2.0,
                    cosmetic=True,
                )
            )
            fill = _qcolor(color, 55 if selected else 22)
            path_item.setBrush(pg.mkBrush(fill))
            path_item.setZValue(10)
            self.plot_item.addItem(path_item, ignoreBounds=True)
            self._gate_items[full_path] = path_item
            self._update_gate_item_geometry(full_path, gate, path_item)

            label_text = full_path[-1]
            percent = self._gate_percentages.get("/".join(full_path))
            if percent is not None:
                label_text = f"{label_text}  {percent:.2f}% parent"
            label = pg.TextItem(
                text=label_text,
                color=color,
                anchor=(0.0, 1.0),
                fill=pg.mkBrush(255, 255, 255, 200),
                border=pg.mkPen(_qcolor(color, 110)),
            )
            label.setZValue(20)
            self.plot_item.addItem(label, ignoreBounds=True)
            bounds = gate_bounds(gate)
            if bounds is not None:
                x0, _x1, _y0, y1 = bounds
                if not math.isfinite(x0):
                    x0 = self._x_bounds[0]
                if not math.isfinite(y1):
                    y1 = self._y_bounds[1]
                x_span = self._x_bounds[1] - self._x_bounds[0]
                label.setPos(x0 + x_span * 0.015, y1)
            elif len(gate_dimension_ids(gate)) == 1:
                dim = gate.dimensions[0]
                x0 = dim.min if dim.min is not None else self._x_bounds[0]
                y0, y1 = self.plot_item.vb.viewRange()[1]
                x_span = self._x_bounds[1] - self._x_bounds[0]
                label.setPos(x0 + x_span * 0.03, y1 - (y1 - y0) * 0.10)
            self._gate_labels.append(label)

            if selected:
                self._add_handles(full_path, gate, color)

    def _update_gate_item_geometry(self, full_path, gate, item) -> None:
        gtype = gate_type(gate)
        dims = gate_dimension_ids(gate)
        path = QtGui.QPainterPath()
        if gtype == "RectangleGate":
            bounds = gate_bounds(gate)
            if bounds is None:
                if len(dims) == 1:
                    dim = gate.dimensions[0]
                    x0 = dim.min if dim.min is not None else self._x_bounds[0]
                    x1 = dim.max if dim.max is not None else self._x_bounds[1]
                    y0, y1 = self.plot_item.vb.viewRange()[1]
                    path.addRect(QtCore.QRectF(float(x0), float(y0), float(x1 - x0), float(y1 - y0)))
                    item.setPath(path)
                return
            x0, x1, y0, y1 = bounds
            path.addRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
        elif gtype == "PolygonGate":
            verts = list(gate.vertices)
            if not verts:
                return
            path.moveTo(float(verts[0][0]), float(verts[0][1]))
            for x, y in verts[1:]:
                path.lineTo(float(x), float(y))
            path.closeSubpath()
        elif gtype == "EllipsoidGate":
            points = ellipse_outline(gate)
            path.moveTo(float(points[0][0]), float(points[0][1]))
            for x, y in points[1:]:
                path.lineTo(float(x), float(y))
            path.closeSubpath()
        item.setPath(path)

    def _add_handles(self, full_path, gate, color) -> None:
        for i, (x, y) in enumerate(gate_handle_points(gate)):
            handle = QtWidgets.QGraphicsEllipseItem(-4.5, -4.5, 9.0, 9.0)
            handle.setBrush(pg.mkBrush(255, 255, 255))
            handle.setPen(pg.mkPen(color, width=1.5, cosmetic=True))
            handle.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
            handle.setZValue(30)
            handle.setPos(QtCore.QPointF(float(x), float(y)))
            self.plot_item.addItem(handle, ignoreBounds=True)
            self._handle_items.append((i, handle))

    def _update_highlight(self) -> None:
        if (
            self.record is None
            or self.selected_gate_path is None
            or len(self._x) == 0
            or self.mode in {"histogram", "density"}
        ):
            self._highlight.setData([], [])
            return
        if self.selected_gate_path[:-1] != (self._population_path or ("root",)):
            self._highlight.setData([], [])
            return
        results = self.session.results_for(self.record)
        if results is None:
            self._highlight.setData([], [])
            return
        try:
            membership = results.get_gate_membership(
                self.selected_gate_path[-1], self.selected_gate_path[:-1]
            )
        except Exception:
            self._highlight.setData([], [])
            return
        if (
            self._population_mask is not None
            and self._view_indices is not None
            and len(membership) == len(self._population_mask)
        ):
            membership = membership[self._view_indices]
        indices = np.flatnonzero(membership)
        if len(indices) > MAX_HIGHLIGHT_POINTS:
            rng = np.random.default_rng(7)
            indices = np.sort(
                rng.choice(indices, size=MAX_HIGHLIGHT_POINTS, replace=False)
            )
        color = self.session.gate_color(self.selected_gate_path)
        self._highlight.setBrush(pg.mkBrush(_qcolor(color, 220)))
        self._highlight.setData(x=self._x[indices], y=self._y[indices])

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def _data_pos(self, pos: QtCore.QPointF) -> QtCore.QPointF:
        return self.plot_item.vb.mapToView(pos)

    def _on_scene_clicked(self, ev) -> None:
        scene_pos = ev.scenePos()
        if not self.plot_item.vb.sceneBoundingRect().contains(scene_pos):
            return
        self._emit_active()
        view_pos = self.plot_item.vb.mapSceneToView(scene_pos)
        self._handle_click(
            ev.button(),
            bool(ev.double()),
            view_pos,
            ev.screenPos().toPoint(),
        )

    def _handle_click(self, button, double: bool, pos: QtCore.QPointF, screen_pos) -> bool:
        if button == QtCore.Qt.RightButton:
            if self.tool == GateTool.POLYGON and self._polygon_points:
                self._finish_polygon()
                return True
            hit = self._gate_at(pos, outline_only=False)
            if hit is not None:
                self._set_selected(hit)
                self.gate_menu_requested.emit(hit, screen_pos)
                return True
            return False

        if button != QtCore.Qt.LeftButton:
            return False

        if self.tool == GateTool.POLYGON:
            if double:
                if self._polygon_points:
                    self._finish_polygon()
                else:
                    hit = self._gate_at(pos, outline_only=False)
                    if hit is not None:
                        self._request_child_gate(hit)
                return True
            point = (float(pos.x()), float(pos.y()))
            if self._polygon_points and math.dist(self._polygon_points[-1], point) < 1e-9:
                return True
            self._polygon_points.append(point)
            self._update_polygon_preview()
            return True

        if double:
            hit = self._gate_at(pos, outline_only=False)
            if hit is not None:
                self._request_child_gate(hit)
                return True
            return False

        hit = self._gate_at(pos, outline_only=False)
        if hit is not None:
            self._set_selected(hit)
            return True
        if self.selected_gate_path is not None:
            self._set_selected(None)
        return False

    def on_drag(self, ev) -> bool:
        if ev.button() != QtCore.Qt.LeftButton:
            return False
        if ev.isStart():
            start_pos = self._data_pos(ev.buttonDownPos())
            force_draw = bool(ev.modifiers() & QtCore.Qt.ShiftModifier)
            if not force_draw:
                handle = self._handle_at(start_pos)
                if handle is not None and self.selected_gate_path is not None:
                    self._drag_handle = handle
                    self._drag_gate = self.selected_gate_path
                    self._drag_last = (float(start_pos.x()), float(start_pos.y()))
                    return True
                hit = self._gate_at(start_pos, outline_only=False)
                if hit is not None:
                    self._set_selected(hit)
                    self._drag_gate = hit
                    self._drag_handle = None
                    self._drag_last = (float(start_pos.x()), float(start_pos.y()))
                    return True
            if self.tool == GateTool.POLYGON:
                return False
            return self._shape_drag(ev)
        if self._drag_gate is not None:
            return self._move_drag(ev)
        if self._drawing:
            return self._shape_drag(ev)
        return False

    def _set_selected(self, full_path: tuple[str, ...] | None, emit: bool = True) -> None:
        self.selected_gate_path = tuple(full_path) if full_path else None
        if emit:
            self.gate_selected.emit(self.selected_gate_path)
        self._render_gates()
        self._update_highlight()

    def _request_child_gate(self, full_path: tuple[str, ...]) -> None:
        self._set_selected(full_path)
        self.gate_child_requested.emit(full_path)

    def _move_drag(self, ev) -> bool:
        if self._drag_gate is None:
            return False
        gate = self.session.gate_by_path(self._drag_gate)
        if gate is None:
            self._clear_drag()
            return False
        pos = self._data_pos(ev.pos())
        if self._drag_handle is not None:
            set_gate_vertex(gate, self._drag_handle, float(pos.x()), float(pos.y()))
            self._render_gates()
        elif self._drag_last is not None:
            dx = float(pos.x()) - self._drag_last[0]
            dy = float(pos.y()) - self._drag_last[1]
            if dx or dy:
                translate_gate(gate, dx, dy)
                self._drag_last = (float(pos.x()), float(pos.y()))
                self._render_gates()
        if ev.isFinish():
            path = self._drag_gate
            self._clear_drag()
            self.gate_changed.emit(path)
        return True

    def _shape_drag(self, ev) -> bool:
        if ev.isStart():
            self.cancel_drawing()
            self._drawing = True
            self._draw_start = self._data_pos(ev.buttonDownPos())
            self._draw_current = self._data_pos(ev.pos())
            self._preview_item = QtWidgets.QGraphicsPathItem()
            self._preview_item.setPen(
                pg.mkPen("#d62728", width=1.5, style=QtCore.Qt.DashLine)
            )
            self._preview_item.setBrush(pg.mkBrush(214, 39, 40, 20))
            self._preview_item.setZValue(25)
            self.plot_item.addItem(self._preview_item, ignoreBounds=True)
            return True
        if not self._drawing or self._draw_start is None:
            return False
        self._draw_current = self._data_pos(ev.pos())
        self._update_preview()
        if ev.isFinish():
            start = self._draw_start
            end = self._data_pos(ev.pos())
            self.cancel_drawing()
            if start is not None:
                self._emit_shape_gate(start, end)
            return True
        return True

    def _update_preview(self) -> None:
        if (
            self._preview_item is None
            or self._draw_start is None
            or self._draw_current is None
        ):
            return
        path = QtGui.QPainterPath()
        start = self._draw_start
        end = self._draw_current
        if self.tool == GateTool.RECTANGLE:
            path.addRect(QtCore.QRectF(start, end).normalized())
        elif self.tool == GateTool.RANGE:
            y0, y1 = self.plot_item.vb.viewRange()[1]
            path.addRect(
                QtCore.QRectF(
                    start.x(), y0, end.x() - start.x(), y1 - y0
                ).normalized()
            )
        elif self.tool == GateTool.ELLIPSE:
            rect = QtCore.QRectF(start, end).normalized()
            path.addEllipse(rect)
        self._preview_item.setPath(path)

    def _emit_shape_gate(self, start: QtCore.QPointF, end: QtCore.QPointF) -> None:
        if not self.x_channel or not self.y_channel:
            return
        x0, x1 = sorted((float(start.x()), float(end.x())))
        y0, y1 = sorted((float(start.y()), float(end.y())))
        if self.tool == GateTool.RANGE:
            if abs(x1 - x0) < 1e-9:
                return
            self.gate_drawn.emit(
                {
                    "type": "range",
                    "channels": [self.x_channel],
                    "range": [x0, x1],
                }
            )
            return
        if abs(x1 - x0) < 1e-9 or abs(y1 - y0) < 1e-9:
            return
        if self.tool == GateTool.RECTANGLE:
            self.gate_drawn.emit(
                {
                    "type": "rectangle",
                    "channels": [self.x_channel, self.y_channel],
                    "ranges": [[x0, x1], [y0, y1]],
                }
            )
        elif self.tool == GateTool.ELLIPSE:
            self.gate_drawn.emit(
                {
                    "type": "ellipse",
                    "channels": [self.x_channel, self.y_channel],
                    "center": [(x0 + x1) / 2.0, (y0 + y1) / 2.0],
                    "half_width": (x1 - x0) / 2.0,
                    "half_height": (y1 - y0) / 2.0,
                }
            )

    def _update_polygon_preview(self) -> None:
        if not self._polygon_points:
            if self._preview_item is not None:
                self.plot_item.removeItem(self._preview_item)
                self._preview_item = None
            return
        if self._preview_item is None:
            self._preview_item = QtWidgets.QGraphicsPathItem()
            self._preview_item.setPen(
                pg.mkPen("#d62728", width=1.5, style=QtCore.Qt.DashLine)
            )
            self._preview_item.setBrush(pg.mkBrush(214, 39, 40, 20))
            self._preview_item.setZValue(25)
            self.plot_item.addItem(self._preview_item, ignoreBounds=True)
        path = QtGui.QPainterPath()
        first = self._polygon_points[0]
        path.moveTo(first[0], first[1])
        for x, y in self._polygon_points[1:]:
            path.lineTo(x, y)
        if len(self._polygon_points) >= 3:
            path.closeSubpath()
        self._preview_item.setPath(path)

    def _finish_polygon(self) -> None:
        points = list(self._polygon_points)
        self.cancel_drawing()
        if len(points) >= 3 and self.x_channel and self.y_channel:
            self.gate_drawn.emit(
                {
                    "type": "polygon",
                    "channels": [self.x_channel, self.y_channel],
                    "vertices": points,
                }
            )

    def cancel_drawing(self) -> None:
        self._drawing = False
        self._draw_start = None
        self._draw_current = None
        self._polygon_points = []
        if self._preview_item is not None:
            try:
                self.plot_item.removeItem(self._preview_item)
            except Exception:
                pass
            self._preview_item = None

    def _clear_drag(self) -> None:
        self._drag_gate = None
        self._drag_handle = None
        self._drag_last = None

    def _handle_at(self, pos: QtCore.QPointF) -> int | None:
        if not self._handle_items:
            return None
        pixel = self.plot_item.vb.viewPixelSize()
        tol_x = max(abs(pixel[0]) * 9.0, 1e-9)
        tol_y = max(abs(pixel[1]) * 9.0, 1e-9)
        for index, handle in self._handle_items:
            hp = handle.pos()
            if abs(pos.x() - hp.x()) <= tol_x and abs(pos.y() - hp.y()) <= tol_y:
                return index
        return None

    def _gate_at(
        self, pos: QtCore.QPointF, outline_only: bool = False
    ) -> tuple[str, ...] | None:
        pixel = self.plot_item.vb.viewPixelSize()
        tol = max(abs(pixel[0]), abs(pixel[1]), 1e-9) * 7.0
        point = QtCore.QPointF(float(pos.x()), float(pos.y()))
        for full_path, item in reversed(list(self._gate_items.items())):
            path = item.path()
            stroker = QtGui.QPainterPathStroker()
            stroker.setWidth(tol * 2.0)
            if stroker.createStroke(path).contains(point):
                return full_path
            if not outline_only and path.contains(point):
                return full_path
        return None

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self.cancel_drawing()
            self.set_tool(GateTool.RANGE if self.mode == "histogram" else GateTool.ELLIPSE)
            return
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if self._polygon_points:
                self._finish_polygon()
                return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    def eventFilter(self, obj, event) -> bool:
        if obj is self.plot_widget.viewport() and event.type() == QtCore.QEvent.MouseButtonDblClick:
            if event.button() == QtCore.Qt.LeftButton and not self._polygon_points:
                scene_pos = self.plot_widget.mapToScene(event.position().toPoint())
                if self.plot_item.vb.sceneBoundingRect().contains(scene_pos):
                    view_pos = self.plot_item.vb.mapSceneToView(scene_pos)
                    hit = self._gate_at(view_pos, outline_only=False)
                    if hit is not None:
                        self._emit_active()
                        self._request_child_gate(hit)
                        return True
        return super().eventFilter(obj, event)

    def _on_scene_mouse_moved(self, scene_pos: QtCore.QPointF) -> None:
        if not self.plot_item.sceneBoundingRect().contains(scene_pos):
            return
        view_pos = self.plot_item.vb.mapSceneToView(scene_pos)
        if self._polygon_points:
            self._update_polygon_preview_to(view_pos)
        if self.x_channel and self.y_channel:
            xspec = self.session.get_transform(self.x_channel)
            yspec = self.session.get_transform(self.y_channel)
            self.cursor_moved.emit(
                f"X: {nice_number(xspec.inverse_scalar(view_pos.x()))}"
                f"    Y: {nice_number(yspec.inverse_scalar(view_pos.y()))}"
            )

    def _update_polygon_preview_to(self, view_pos: QtCore.QPointF) -> None:
        if not self._polygon_points or self._preview_item is None:
            return
        path = QtGui.QPainterPath()
        first = self._polygon_points[0]
        path.moveTo(first[0], first[1])
        for x, y in self._polygon_points[1:]:
            path.lineTo(x, y)
        path.lineTo(float(view_pos.x()), float(view_pos.y()))
        if len(self._polygon_points) >= 3:
            path.closeSubpath()
        self._preview_item.setPath(path)

    def _emit_active(self) -> None:
        self.activated.emit(self)
