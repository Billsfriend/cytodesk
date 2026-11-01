"""Grid container for one or four plot panels."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..core.session import AnalysisSession, SampleRecord
from .plot_panel import GateTool, PlotPanel


class PlotGrid(QtWidgets.QWidget):
    active_changed = QtCore.Signal(object)
    gate_drawn = QtCore.Signal(dict)
    gate_selected = QtCore.Signal(object)
    gate_changed = QtCore.Signal(object)
    gate_menu_requested = QtCore.Signal(object, object)
    gate_child_requested = QtCore.Signal(object)
    cursor_moved = QtCore.Signal(str)

    def __init__(self, session: AnalysisSession, parent=None):
        super().__init__(parent)
        self.session = session
        self.panels = [PlotPanel(session, self) for _ in range(4)]
        for panel in self.panels:
            panel.activated.connect(self.set_active_panel)
            panel.gate_drawn.connect(self.gate_drawn)
            panel.gate_selected.connect(self.gate_selected)
            panel.gate_changed.connect(self.gate_changed)
            panel.gate_menu_requested.connect(self.gate_menu_requested)
            panel.gate_child_requested.connect(self.gate_child_requested)
            panel.cursor_moved.connect(self.cursor_moved)
        self.active_panel = self.panels[0]
        self._record: SampleRecord | None = None
        self._population_mask: np.ndarray | None = None
        self._population_path: tuple[str, ...] | None = None
        self._population_label: str = ""
        self._overlay_records: list[SampleRecord] | None = None

        self._layout = QtWidgets.QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(3)
        self._columns = 1
        self.set_layout(1)
        self.set_active_panel(self.panels[0])

    # ------------------------------------------------------------------
    def set_layout(self, count: int) -> None:
        count = 4 if count == 4 else 1
        for panel in self.panels:
            panel.setVisible(False)
            self._layout.removeWidget(panel)
        visible = self.panels if count == 4 else self.panels[:1]
        columns = 2 if count == 4 else 1
        for i, panel in enumerate(visible):
            self._layout.addWidget(panel, i // columns, i % columns)
            panel.setVisible(True)
            if self._record is not None:
                self._ensure_channel_pair(panel, i)
                panel.set_population_context(
                    self._population_mask,
                    self._population_path,
                    self._population_label,
                    reload=False,
                )
                panel.set_record(self._record)
                if self._overlay_records:
                    panel.set_overlay_records(self._overlay_records)
        self._columns = columns
        if self.active_panel not in visible:
            self.set_active_panel(visible[0])

    @property
    def layout_count(self) -> int:
        return 4 if self._columns == 2 else 1

    def set_active_panel(self, panel: PlotPanel) -> None:
        if panel not in self.panels or panel.isHidden():
            return
        self.active_panel = panel
        for p in self.panels:
            p.setStyleSheet(
                "border: 2px solid #2d6cdf;" if p is panel else "border: 1px solid #d5d9e0;"
            )
        self.active_changed.emit(panel)

    # ------------------------------------------------------------------
    def set_session(self, session: AnalysisSession) -> None:
        self.session = session
        self._record = None
        self._population_mask = None
        self._population_path = None
        self._population_label = ""
        self._overlay_records = None
        for panel in self.panels:
            panel.session = session
            panel.record = None
            panel.selected_gate_path = None
            panel.clear()

    def set_population_context(
        self,
        mask,
        path: tuple[str, ...] | None,
        label: str,
        reload: bool = True,
    ) -> None:
        self._population_mask = mask
        self._population_path = path
        self._population_label = label
        for panel in self.panels:
            panel.set_population_context(mask, path, label, reload=reload)

    def set_overlay(self, records) -> None:
        """Enable or disable multi-sample overlay on every panel."""

        records = list(records) if records else None
        if records is not None and len(records) < 2:
            records = None
        self._overlay_records = records
        for panel in self.panels:
            panel.set_overlay_records(records)

    def set_record(self, record: SampleRecord | None) -> None:
        self._record = record
        for i, panel in enumerate(self.panels):
            if record is None or panel.isHidden():
                panel.record = None
                panel.set_record(None)
                continue
            self._ensure_channel_pair(panel, i)
            panel.set_record(record)
            if self._overlay_records:
                panel.set_overlay_records(self._overlay_records)

    def _ensure_channel_pair(self, panel: PlotPanel, index: int, force: bool = False) -> None:
        if self._record is None:
            return
        candidates = [c for c in self._record.transform_candidates()]
        if len(candidates) < 2:
            return
        names = [c.pnn for c in candidates]
        if not force and panel.x_channel in names and panel.y_channel in names:
            return

        scatter = [c.pnn for c in candidates if c.is_scatter]
        fluoro = [c.pnn for c in candidates if not c.is_scatter and not c.is_time]
        fsc = next((name for name in names if "FSC" in name.upper() and name.upper().endswith("-A")), None)
        ssc = next((name for name in names if "SSC" in name.upper() and name.upper().endswith("-A")), None)
        if fsc is None and scatter:
            fsc = scatter[0]
        if ssc is None and len(scatter) > 1:
            ssc = scatter[1]
        fallback = names

        pairs = [
            (fsc or fallback[0], ssc or (fluoro[0] if fluoro else fallback[1])),
            (fsc or fallback[0], fluoro[0] if fluoro else fallback[1]),
            (fluoro[0] if fluoro else fallback[0], fluoro[1] if len(fluoro) > 1 else fallback[1]),
            (fluoro[1] if len(fluoro) > 1 else fallback[0], fluoro[2] if len(fluoro) > 2 else fallback[1]),
        ]
        x_channel, y_channel = pairs[min(index, len(pairs) - 1)]
        if x_channel == y_channel:
            other = next((name for name in names if name != x_channel), None)
            if other:
                y_channel = other
        panel.x_channel = x_channel
        panel.y_channel = y_channel

    def active(self) -> PlotPanel:
        return self.active_panel

    def set_mode(self, mode: str) -> None:
        for panel in self.panels:
            panel.set_mode(mode)

    def set_show_contours(self, show: bool) -> None:
        for panel in self.panels:
            panel.set_show_contours(show)

    def set_tool(self, tool: GateTool | str) -> None:
        for panel in self.panels:
            panel.set_tool(tool)

    def refresh_events(self) -> None:
        for panel in self.panels:
            panel.refresh_events()

    def refresh_gates(self) -> None:
        for panel in self.panels:
            panel.refresh_gates()

    def set_selected_gate(self, full_path: tuple[str, ...] | None) -> None:
        for panel in self.panels:
            panel.set_selected_gate(full_path)

    def set_gate_percentages(self, percentages: dict[str, float]) -> None:
        for panel in self.panels:
            panel.set_gate_percentages(percentages)
