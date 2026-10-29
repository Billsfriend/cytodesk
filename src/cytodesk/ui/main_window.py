"""Main application window."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from pyqtgraph.exporters import ImageExporter
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtSvg import QSvgGenerator

from ..core.gates import (
    gate_dimension_ids,
    make_ellipse_gate,
    make_polygon_gate,
    make_range_gate,
    make_rectangle_gate,
)
from ..core.project import load_project, save_project
from ..core.session import AnalysisSession, SampleRecord, load_sample_file
from ..plotting import GateTool, PlotGrid
from .gate_tree import GateTree
from .inspector import InspectorPanel
from .sample_panel import SamplePanel
from .stats_table import StatsTable


class _LoadSignals(QtCore.QObject):
    loaded = QtCore.Signal(object)
    failed = QtCore.Signal(str, str)
    progress = QtCore.Signal(str)
    finished = QtCore.Signal()


class _LoadWorker(QtCore.QRunnable):
    def __init__(self, paths: list[Path], repair: bool = False):
        super().__init__()
        self.paths = list(paths)
        self.repair = repair
        self.signals = _LoadSignals()

    def run(self) -> None:
        for path in self.paths:
            self.signals.progress.emit(f"正在加载 {path.name}…")
            try:
                record = load_sample_file(path, repair=self.repair)
            except Exception as exc:  # pragma: no cover - file dependent
                self.signals.failed.emit(str(path), str(exc))
            else:
                self.signals.loaded.emit(record)
        self.signals.finished.emit()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = AnalysisSession()
        self.project_path: Path | None = None
        self.current_sample_id: str | None = None
        self.current_population_path: tuple[str, ...] | None = None
        self.selected_gate_path: tuple[str, ...] | None = None
        self.gate_parent_path: tuple[str, ...] = ("root",)
        self._dirty = False
        self._workers: list[_LoadWorker] = []
        self._settings = QtCore.QSettings("CytoDesk", "CytoDesk")
        self._last_dir = self._settings.value("last_dir", str(Path.home()))

        self.setWindowTitle("CytoDesk")
        self.resize(1480, 920)
        self.setMinimumSize(1040, 680)
        self.setAcceptDrops(True)

        self._build_ui()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._connect_signals()
        self._update_actions()
        self._update_title()
        self._update_status("就绪")

        esc_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Esc"), self)
        esc_shortcut.activated.connect(self._cancel_gate_tool)

        geometry = self._settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.sample_panel = SamplePanel()
        self.gate_tree = GateTree()
        self.plot_grid = PlotGrid(self.session)
        self.stats_table = StatsTable()
        self.stats_table.set_session(self.session)
        self.inspector = InspectorPanel()
        self.inspector.set_session(self.session)
        self.sample_panel.set_session(self.session)
        self.gate_tree.set_session(self.session)

        sample_box = QtWidgets.QGroupBox("样本")
        sample_layout = QtWidgets.QVBoxLayout(sample_box)
        sample_layout.setContentsMargins(2, 6, 2, 2)
        sample_layout.addWidget(self.sample_panel)

        gate_box = QtWidgets.QGroupBox("门树")
        gate_layout = QtWidgets.QVBoxLayout(gate_box)
        gate_layout.setContentsMargins(2, 6, 2, 2)
        gate_layout.addWidget(self.gate_tree)

        left_split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        left_split.addWidget(sample_box)
        left_split.addWidget(gate_box)
        left_split.setSizes([260, 520])

        right_split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        right_split.addWidget(self.plot_grid)
        right_split.addWidget(self.stats_table)
        right_split.setSizes([620, 260])

        main_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        main_split.addWidget(left_split)
        main_split.addWidget(right_split)
        main_split.addWidget(self.inspector)
        main_split.setSizes([270, 900, 320])
        main_split.setStretchFactor(1, 1)
        self.setCentralWidget(main_split)

        self.status_label = QtWidgets.QLabel("就绪")
        self.cursor_label = QtWidgets.QLabel("")
        self.parent_status_label = QtWidgets.QLabel("当前群体: Root")
        self.sample_status_label = QtWidgets.QLabel("")
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.parent_status_label)
        self.statusBar().addPermanentWidget(self.cursor_label)
        self.statusBar().addPermanentWidget(self.sample_status_label)
        self.statusBar().setSizeGripEnabled(False)

    def _build_actions(self) -> None:
        self.action_open = self._action(
            "打开 FCS…", self.open_files, "Ctrl+O", "打开一个或多个 FCS 文件"
        )
        self.action_open_folder = self._action(
            "打开文件夹…", self.open_folder, "Ctrl+Shift+O", "加载文件夹中的所有 FCS 文件"
        )
        self.action_open_project = self._action(
            "打开项目…", self.open_project, "Ctrl+Alt+O", "打开 .cytodesk 项目"
        )
        self.action_save = self._action("保存项目", self.save_project, "Ctrl+S")
        self.action_save_as = self._action(
            "项目另存为…", self.save_project_as, "Ctrl+Shift+S"
        )
        self.action_export_image = self._action(
            "导出当前图…", self.export_image, "Ctrl+E"
        )
        self.action_export_stats = self._action(
            "导出统计 CSV…", self.export_stats, "Ctrl+Shift+E"
        )
        self.action_quit = self._action("退出", self.close, "Ctrl+Q")

        self.tool_group = QtGui.QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_actions: dict[GateTool, QtGui.QAction] = {}
        tool_specs = [
            (GateTool.RECTANGLE, "矩形", "Ctrl+1", "拖拽绘制矩形门"),
            (GateTool.POLYGON, "多边形", "Ctrl+2", "单击添加顶点，双击或右键结束"),
            (GateTool.ELLIPSE, "椭圆", "Ctrl+3", "拖拽绘制椭圆门（默认）"),
            (GateTool.RANGE, "范围", "Ctrl+4", "在 X 轴上拖拽绘制一维范围门"),
        ]
        for tool, text, shortcut, tip in tool_specs:
            action = self._action(text, None, shortcut, tip, checkable=True)
            action.setData(tool)
            action.triggered.connect(lambda _checked=False, t=tool: self.set_gate_tool(t))
            self.tool_group.addAction(action)
            self.tool_actions[tool] = action
        self.tool_actions[GateTool.ELLIPSE].setChecked(True)

        self.action_delete_gate = self._action(
            "删除门", self.delete_selected_gate, "Ctrl+Delete"
        )
        self.action_reset_view = self._action(
            "重置视图", self.reset_view, "Ctrl+0", "恢复当前图的自动范围"
        )
        self.action_about = self._action("关于 CytoDesk", self.show_about)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItem("散点图", "scatter")
        self.mode_combo.addItem("密度图", "density")
        self.mode_combo.addItem("直方图", "histogram")
        self.mode_combo.setToolTip("切换散点/密度显示")
        self.mode_combo.activated.connect(self._on_mode_combo)

        self.layout_combo = QtWidgets.QComboBox()
        self.layout_combo.addItem("1 × 1", 1)
        self.layout_combo.addItem("2 × 2", 4)
        self.layout_combo.setToolTip("切换绘图布局")
        self.layout_combo.activated.connect(self._on_layout_combo)

    def _action(
        self,
        text: str,
        slot=None,
        shortcut: str | None = None,
        tooltip: str | None = None,
        checkable: bool = False,
    ) -> QtGui.QAction:
        action = QtGui.QAction(text, self)
        if shortcut:
            action.setShortcut(QtGui.QKeySequence(shortcut))
        if tooltip:
            action.setToolTip(tooltip)
            action.setStatusTip(tooltip)
        action.setCheckable(checkable)
        if slot is not None:
            action.triggered.connect(slot)
        return action

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("文件")
        file_menu.addAction(self.action_open)
        file_menu.addAction(self.action_open_folder)
        file_menu.addSeparator()
        file_menu.addAction(self.action_open_project)
        file_menu.addAction(self.action_save)
        file_menu.addAction(self.action_save_as)
        file_menu.addSeparator()
        file_menu.addAction(self.action_export_image)
        file_menu.addAction(self.action_export_stats)
        file_menu.addSeparator()
        file_menu.addAction(self.action_quit)

        edit_menu = self.menuBar().addMenu("编辑")
        edit_menu.addAction(self.action_delete_gate)
        edit_menu.addSeparator()
        edit_menu.addAction(self.action_reset_view)

        view_menu = self.menuBar().addMenu("门控")
        for action in self.tool_actions.values():
            view_menu.addAction(action)
        view_menu.addSeparator()
        view_menu.addAction(self.action_delete_gate)

        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction(self.action_about)

    def _build_toolbar(self) -> None:
        toolbar = QtWidgets.QToolBar("主工具栏")
        toolbar.setObjectName("MainToolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QtCore.QSize(18, 18))
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.addToolBar(toolbar)

        toolbar.addAction(self.action_open)
        toolbar.addAction(self.action_open_folder)
        toolbar.addAction(self.action_save)
        toolbar.addSeparator()
        for action in self.tool_actions.values():
            toolbar.addAction(action)
        toolbar.addAction(self.action_delete_gate)
        toolbar.addSeparator()
        toolbar.addAction(self.action_reset_view)
        toolbar.addSeparator()

        mode_label = QtWidgets.QLabel(" 显示 ")
        toolbar.addWidget(mode_label)
        toolbar.addWidget(self.mode_combo)
        layout_label = QtWidgets.QLabel(" 布局 ")
        toolbar.addWidget(layout_label)
        toolbar.addWidget(self.layout_combo)
        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        toolbar.addAction(self.action_export_image)

    def _connect_signals(self) -> None:
        self.sample_panel.open_files_requested.connect(self.open_files)
        self.sample_panel.open_folder_requested.connect(self.open_folder)
        self.sample_panel.sample_activated.connect(self.activate_sample)
        self.sample_panel.remove_requested.connect(self.remove_sample)

        self.gate_tree.gate_selected.connect(self.select_gate)
        self.gate_tree.population_open_requested.connect(self.open_population)
        self.gate_tree.rename_requested.connect(self.rename_gate)
        self.gate_tree.delete_requested.connect(self._delete_gate_path)

        self.plot_grid.active_changed.connect(self._on_active_plot_changed)
        self.plot_grid.gate_drawn.connect(self.on_gate_drawn)
        self.plot_grid.gate_selected.connect(self.select_gate)
        self.plot_grid.gate_changed.connect(self.on_gate_changed)
        self.plot_grid.gate_menu_requested.connect(self.show_gate_menu)
        self.plot_grid.gate_child_requested.connect(self.open_population)
        self.plot_grid.cursor_moved.connect(self.cursor_label.setText)

        self.stats_table.gate_selected.connect(self.select_gate)

        self.inspector.axis_changed.connect(self._schedule_axis_change)
        self.inspector.transform_applied.connect(self.on_transform_applied)
        self.inspector.mode_changed.connect(self._schedule_mode_change)
        self.inspector.layout_changed.connect(self._schedule_layout_change)
        self.inspector.contours_changed.connect(self.set_contours)
        self.inspector.gate_name_changed.connect(self._rename_from_inspector)
        self.inspector.gate_color_changed.connect(self.on_gate_color_changed)

    # ------------------------------------------------------------------
    # Loading samples
    # ------------------------------------------------------------------
    def open_files(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "打开 FCS 文件",
            self._last_dir,
            "FCS 文件 (*.fcs *.FCS);;所有文件 (*)",
        )
        if paths:
            self._last_dir = str(Path(paths[0]).parent)
            self._load_paths([Path(p) for p in paths])

    def open_folder(self) -> None:
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择包含 FCS 文件的文件夹", self._last_dir
        )
        if not directory:
            return
        self._last_dir = directory
        paths = sorted(
            p for p in Path(directory).iterdir() if p.is_file() and p.suffix.lower() == ".fcs"
        )
        if not paths:
            QtWidgets.QMessageBox.information(self, "没有 FCS 文件", "所选文件夹中没有 .fcs 文件。")
            return
        self._load_paths(paths)

    def load_paths(self, paths: list[str | Path]) -> None:
        """Public entry point used for files passed on the command line."""

        self._load_paths([Path(p) for p in paths])

    def _load_paths(self, paths: list[Path], repair: bool = False) -> None:
        if not paths:
            return
        self._update_status(f"正在加载 {len(paths)} 个 FCS 文件…")
        worker = _LoadWorker(paths, repair=repair)
        worker.signals.progress.connect(self._update_status)
        worker.signals.loaded.connect(self._on_sample_loaded)
        worker.signals.failed.connect(self._on_load_failed)
        worker.signals.finished.connect(lambda w=worker: self._on_load_finished(w))
        self._workers.append(worker)
        QtCore.QThreadPool.globalInstance().start(worker)

    def _on_sample_loaded(self, record: SampleRecord) -> None:
        # Ignore duplicates by path.
        for existing in self.session.samples.values():
            if existing.path.resolve() == record.path.resolve():
                self._update_status(f"{record.path.name} 已在工作区中")
                return
        try:
            self.session.add_record(record)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "加载失败", str(exc))
            return
        self.sample_panel.refresh()
        self.gate_tree.refresh()
        self._set_dirty()
        if self.current_sample_id is None:
            self.activate_sample(record.id)
        else:
            self._update_status(f"已加载 {record.id}")
        self._update_actions()

    def _on_load_failed(self, path: str, message: str) -> None:
        answer = QtWidgets.QMessageBox.question(
            self,
            "无法读取 FCS 文件",
            f"{path}\n\n{message}\n\n是否尝试兼容模式重新加载？\n"
            "（忽略字节偏移错误，部分不规范文件需要此选项）",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )
        if answer == QtWidgets.QMessageBox.Yes:
            self._load_paths([Path(path)], repair=True)
        else:
            self._update_status(f"加载失败: {Path(path).name}")

    def _on_load_finished(self, worker: _LoadWorker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        if self.current_sample_id is None and self.session.samples:
            self.activate_sample(next(iter(self.session.samples)))
        self._update_actions()
        if not self._workers:
            self._update_status("就绪")

    def remove_sample(self, sample_id: str) -> None:
        record = self.session.get_sample(sample_id)
        if record is None:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "移除样本",
            f"从工作区移除样本 {record.id}？\n原始 FCS 文件不会被删除。",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self.session.remove_sample(sample_id)
        self.sample_panel.refresh()
        self._set_dirty()
        if self.current_sample_id == sample_id:
            self.current_sample_id = None
            if self.session.samples:
                self.activate_sample(next(iter(self.session.samples)))
            else:
                self._clear_view()
        self._update_actions()

    # ------------------------------------------------------------------
    # Sample activation and gating
    # ------------------------------------------------------------------
    def activate_sample(self, sample_id: str) -> None:
        record = self.session.get_sample(sample_id)
        if record is None:
            return
        self.current_sample_id = sample_id
        self.current_population_path = None
        self.selected_gate_path = None
        self.gate_parent_path = ("root",)
        self.sample_panel.set_active(sample_id)

        self._run_gating(record)
        self.plot_grid.set_population_context(None, None, record.id, reload=False)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.plot_grid.set_record(record)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        self.plot_grid.set_selected_gate(None)
        self.gate_tree.set_sample(record)
        self.gate_tree.select_population(None, emit=False)
        active = self.plot_grid.active()
        self.inspector.set_session(self.session)
        self.inspector.set_record(record, active.x_channel, active.y_channel)
        self.inspector.set_mode(active.mode)
        self.inspector.set_layout(self.plot_grid.layout_count)
        self.inspector.set_contours(active.show_contours)
        self.inspector.set_gate(None, None, None)
        self._sync_mode_combo(active.mode)
        self._sync_layout_combo(self.plot_grid.layout_count)
        self.set_gate_tool(self._default_tool())
        self._refresh_stats()
        self._update_sample_status()
        self._update_parent_status()
        self._update_actions()

    def _run_gating(self, record: SampleRecord, force: bool = False) -> bool:
        if not self.session.has_gates():
            self.session.invalidate_results()
            return True
        if not force and self.session.results_for(record) is not None:
            return True
        missing = self.session.missing_gate_channels(record)
        if missing:
            self.session.invalidate_results()
            preview = ", ".join(missing[:5])
            if len(missing) > 5:
                preview += " …"
            self._update_status(f"样本 {record.id} 缺少门所需通道：{preview}")
            return False
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.session.gate_sample(record)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "门控失败",
                f"样本 {record.id} 门控时发生错误：\n\n{exc}",
            )
            return False
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        return True

    def _refresh_stats(self) -> None:
        record = self.session.get_sample(self.current_sample_id)
        if record is None:
            self.stats_table.set_stats([], total_events=0)
            self.gate_tree.set_counts({})
            self.plot_grid.set_gate_percentages({})
            return
        panel = self.plot_grid.active()
        x_channel, y_channel = panel.x_channel, panel.y_channel
        if panel.mode == "histogram":
            y_channel = None
        stat_channels = [channel for channel in (x_channel, y_channel) if channel]
        stats = self.session.population_stats(record, stat_channels)
        counts = {"/".join(stat.full_path): stat.count for stat in stats}
        percentages = {
            "/".join(stat.full_path): stat.percent_parent for stat in stats
        }
        x_name = record.channel(x_channel).short_label if x_channel and record.channel(x_channel) else x_channel
        y_name = record.channel(y_channel).short_label if y_channel and record.channel(y_channel) else y_channel
        self.stats_table.set_stats(
            stats,
            x_channel,
            y_channel,
            total_events=record.event_count,
            x_name=x_name,
            y_name=y_name,
        )
        self.gate_tree.set_counts(counts)
        self.plot_grid.set_gate_percentages(percentages)
        self._update_sample_status()
        self._update_actions()

    def _clear_view(self) -> None:
        self.current_sample_id = None
        self.current_population_path = None
        self.selected_gate_path = None
        self.gate_parent_path = ("root",)
        self._update_parent_status()
        self.plot_grid.set_population_context(None, None, "", reload=False)
        self.plot_grid.set_record(None)
        self.gate_tree.set_session(self.session)
        self.gate_tree.set_sample(None)
        self.inspector.set_record(None, None, None)
        self.inspector.set_gate(None, None, None)
        self.stats_table.set_stats([], total_events=0)
        self._update_sample_status()
        self._update_actions()

    # ------------------------------------------------------------------
    # Gate editing
    # ------------------------------------------------------------------
    def set_gate_tool(self, tool: GateTool | str) -> None:
        if isinstance(tool, str):
            try:
                tool = GateTool(tool)
            except ValueError:
                tool = self._default_tool()
        self.plot_grid.set_tool(tool)
        action = self.tool_actions.get(tool)
        if action is not None and not action.isChecked():
            action.setChecked(True)

    def open_population(self, full_path: tuple[str, ...] | None) -> None:
        record = self.session.get_sample(self.current_sample_id)
        if record is None:
            return
        full_path = tuple(full_path) if full_path else None
        mask = None
        if full_path is not None:
            gate = self.session.gate_by_path(full_path)
            if gate is None:
                return
            if not self._run_gating(record):
                return
            try:
                mask = self.session.gate_membership(record, full_path)
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "打开群体失败", str(exc))
                return
            if mask is None or not bool(np.any(mask)):
                QtWidgets.QMessageBox.information(
                    self, "空群体", f"门 {' / '.join(full_path[1:])} 内没有事件。"
                )
                return

            panel = self.plot_grid.active()
            ids = gate_dimension_ids(gate)
            available = {channel.pnn for channel in record.channels}
            channels_available = bool(ids) and ids[0] in available and (
                len(ids) < 2 or ids[1] in available
            )
            if not channels_available:
                QtWidgets.QMessageBox.warning(
                    self,
                    "通道不匹配",
                    f"样本 {record.id} 不包含门 {full_path[-1]} 使用的通道。",
                )
                return
            if len(ids) == 1:
                y_channel = panel.y_channel
                if panel.mode != "histogram":
                    self.set_mode("histogram")
                panel = self.plot_grid.active()
                if y_channel is None:
                    candidates = [c.pnn for c in record.transform_candidates()]
                    y_channel = candidates[1] if len(candidates) > 1 else ids[0]
                panel.set_channels(ids[0], y_channel)
                self.inspector.set_record(record, ids[0], y_channel)
            elif len(ids) >= 2:
                if panel.mode == "histogram":
                    self.set_mode("scatter")
                    panel = self.plot_grid.active()
                panel.set_channels(ids[0], ids[1])
                self.inspector.set_record(record, ids[0], ids[1])

        label = record.id
        if full_path is not None:
            label = f"{record.id} / {' / '.join(full_path[1:])}"
        self.current_population_path = full_path
        self.gate_parent_path = full_path or ("root",)
        self.plot_grid.set_population_context(mask, full_path, label)
        self.gate_tree.select_population(full_path, emit=False)
        self.selected_gate_path = None
        self.plot_grid.set_selected_gate(None)
        self.inspector.set_gate(None, None, None)
        self._update_parent_status()
        self._refresh_stats()
        self._update_sample_status()
        self.set_gate_tool(self._default_tool())
        count = int(np.count_nonzero(mask)) if mask is not None else record.event_count
        view_name = "Root / " + record.id if full_path is None else " / ".join(full_path[1:])
        self._update_status(f"已打开群体 {view_name} · {count:,} 事件")

    def show_gate_menu(self, full_path: tuple[str, ...], global_pos) -> None:
        gate = self.session.gate_by_path(full_path)
        if gate is None:
            return
        self.select_gate(full_path)
        menu = QtWidgets.QMenu(self)
        child_action = menu.addAction("打开此门并圈门")
        rename_action = menu.addAction("重命名…")
        menu.addSeparator()
        delete_action = menu.addAction("删除门")
        action = menu.exec(global_pos)
        if action is child_action:
            self.open_population(full_path)
        elif action is rename_action:
            self.rename_gate(full_path)
        elif action is delete_action:
            self._delete_gate_path(full_path)

    def _default_tool(self) -> GateTool:
        return (
            GateTool.RANGE
            if self.plot_grid.active().mode == "histogram"
            else GateTool.ELLIPSE
        )

    def _cancel_gate_tool(self) -> None:
        for panel in self.plot_grid.panels:
            panel.cancel_drawing()
        self.set_gate_tool(self._default_tool())

    def on_gate_drawn(self, draft: dict) -> None:
        record = self.session.get_sample(self.current_sample_id)
        if record is None:
            return
        parent_path = self.current_population_path or ("root",)
        if parent_path != ("root",) and self.session.gate_by_path(parent_path) is None:
            parent_path = ("root",)
        self.gate_parent_path = parent_path
        default_name = self.session.unique_gate_name(parent_path)
        name, ok = QtWidgets.QInputDialog.getText(
            self, "新建门", "门名称：", text=default_name
        )
        if not ok:
            self.set_gate_tool(self._default_tool())
            return
        name = name.strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "无效名称", "门名称不能为空。")
            return
        if name in self.session.child_names(parent_path):
            QtWidgets.QMessageBox.warning(
                self, "名称重复", f"同一层级已存在名为 {name!r} 的门。"
            )
            return
        try:
            gate = self._build_gate_from_draft(name, draft)
            full_path = self.session.add_gate(gate, parent_path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "创建门失败", str(exc))
            self.set_gate_tool(self._default_tool())
            return

        self.set_gate_tool(self._default_tool())
        self._run_gating(record)
        self.gate_tree.refresh()
        self.plot_grid.refresh_gates()
        self.select_gate(full_path)
        self._update_parent_status()
        self._refresh_stats()
        self._set_dirty()
        self._update_status(f"已创建门 {full_path[-1]}（父级: {' / '.join(parent_path[1:]) or '根'}）")

    def _build_gate_from_draft(self, name: str, draft: dict):
        channels = list(draft["channels"])
        xf = {ch: self.session.get_transform_id(ch) for ch in channels}
        gtype = draft["type"]
        if gtype == "rectangle":
            ranges = draft["ranges"]
            return make_rectangle_gate(
                name,
                channels[0],
                channels[1],
                tuple(ranges[0]),
                tuple(ranges[1]),
                xf[channels[0]],
                xf[channels[1]],
            )
        if gtype == "range":
            return make_range_gate(
                name, channels[0], tuple(draft["range"]), xf[channels[0]]
            )
        if gtype == "polygon":
            return make_polygon_gate(
                name,
                channels[0],
                channels[1],
                draft["vertices"],
                xf[channels[0]],
                xf[channels[1]],
            )
        if gtype == "ellipse":
            center = draft["center"]
            return make_ellipse_gate(
                name,
                channels[0],
                channels[1],
                (center[0], center[1]),
                draft["half_width"],
                draft["half_height"],
                xf[channels[0]],
                xf[channels[1]],
            )
        raise ValueError(f"不支持的门类型: {gtype}")

    def select_gate(self, full_path: tuple[str, ...] | None) -> None:
        if full_path is not None:
            gate = self.session.gate_by_path(full_path)
            if gate is None:
                full_path = None
        else:
            gate = None
        self.selected_gate_path = tuple(full_path) if full_path else None
        self.plot_grid.set_selected_gate(self.selected_gate_path)
        if self.selected_gate_path is None:
            self.gate_tree.select_population(self.current_population_path, emit=False)
        else:
            self.gate_tree.select_path(self.selected_gate_path, emit=False)
        if self.selected_gate_path is not None and gate is not None:
            color = self.session.gate_color(self.selected_gate_path)
            self.inspector.set_gate(self.selected_gate_path, gate, color)
        else:
            self.inspector.set_gate(None, None, None)
        self._update_parent_status()
        self._update_actions()

    def on_gate_changed(self, full_path: tuple[str, ...]) -> None:
        record = self.session.get_sample(self.current_sample_id)
        if record is None:
            return
        self.session.invalidate_results()
        self._run_gating(record)
        self._refresh_population_view()
        self.plot_grid.refresh_gates()
        self.plot_grid.set_selected_gate(self.selected_gate_path)
        self._refresh_stats()
        self._set_dirty()
        self._update_status(f"已更新门 {full_path[-1]}")

    def rename_gate(self, full_path: tuple[str, ...]) -> None:
        gate = self.session.gate_by_path(full_path)
        if gate is None:
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self, "重命名门", "新名称：", text=full_path[-1]
        )
        if not ok:
            return
        name = name.strip()
        if not name or name == full_path[-1]:
            return
        try:
            new_path = self.session.rename_gate(full_path, name)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "重命名失败", str(exc))
            return
        record = self.session.get_sample(self.current_sample_id)
        if record is not None:
            self._run_gating(record)
        self._remap_parent_after_rename(full_path, new_path)
        self.gate_tree.refresh()
        self._refresh_population_view()
        self.select_gate(new_path)
        self._refresh_stats()
        self._set_dirty()

    def _rename_from_inspector(self, full_path: tuple[str, ...], name: str) -> None:
        try:
            new_path = self.session.rename_gate(full_path, name)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "重命名失败", str(exc))
            return
        record = self.session.get_sample(self.current_sample_id)
        if record is not None:
            self._run_gating(record)
        self._remap_parent_after_rename(full_path, new_path)
        self.gate_tree.refresh()
        self._refresh_population_view()
        self.select_gate(new_path)
        self._refresh_stats()
        self._set_dirty()

    def _remap_parent_after_rename(
        self, old_path: tuple[str, ...], new_path: tuple[str, ...]
    ) -> None:
        def remap(path):
            if path is None:
                return None
            if path == old_path:
                return new_path
            if path[: len(old_path)] == old_path:
                return new_path + path[len(old_path):]
            return path

        self.gate_parent_path = remap(self.gate_parent_path) or ("root",)
        self.current_population_path = remap(self.current_population_path)
        self._update_parent_status()

    def on_gate_color_changed(self, full_path: tuple[str, ...], color: str) -> None:
        self.session.set_gate_color(full_path, color)
        self.plot_grid.refresh_gates()
        if self.selected_gate_path == full_path:
            gate = self.session.gate_by_path(full_path)
            self.inspector.set_gate(full_path, gate, color)
        self._set_dirty()

    def delete_selected_gate(self) -> None:
        if self.selected_gate_path is not None:
            self._delete_gate_path(self.selected_gate_path)
        else:
            self._update_status("请先选择一个门")

    def _delete_gate_path(self, full_path: tuple[str, ...]) -> None:
        if self.session.gate_by_path(full_path) is None:
            return
        descendants = [
            path
            for path in self.session.gate_full_paths()
            if path[:-1] == tuple(full_path)
        ]
        extra = f"\n\n该门还有 {len(descendants)} 个子门，将一并删除。" if descendants else ""
        answer = QtWidgets.QMessageBox.question(
            self,
            "删除门",
            f"删除门 {' / '.join(full_path[1:])}？{extra}",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        parent_path = full_path[:-1]
        affected = self.current_population_path is not None and (
            self.current_population_path == full_path
            or self.current_population_path[: len(full_path)] == full_path
        )
        self.session.remove_gate(full_path)
        self.selected_gate_path = None
        record = self.session.get_sample(self.current_sample_id)
        if record is not None:
            self._run_gating(record)
        self.gate_tree.refresh()
        self.plot_grid.set_selected_gate(None)
        self.inspector.set_gate(None, None, None)
        if affected:
            fallback = parent_path
            while fallback != ("root",) and self.session.gate_by_path(fallback) is None:
                fallback = fallback[:-1]
            self.open_population(None if fallback == ("root",) else fallback)
        else:
            self._refresh_population_view()
            self.plot_grid.refresh_gates()
        self._refresh_stats()
        self._set_dirty()

    # ------------------------------------------------------------------
    # Inspector / plot settings
    # ------------------------------------------------------------------
    def on_axis_changed(self, x_channel: str, y_channel: str) -> None:
        panel = self.plot_grid.active()
        panel.set_channels(x_channel, y_channel)
        record = self.session.get_sample(self.current_sample_id)
        if record is not None:
            self.inspector.set_record(record, x_channel, y_channel)
        self._refresh_stats()
        self._set_dirty()

    def on_transform_applied(self, channel: str, spec) -> None:
        record = self.session.get_sample(self.current_sample_id)
        if record is None:
            return
        self.session.set_transform(channel, spec)
        self._run_gating(record)
        if self.current_population_path is None:
            self.plot_grid.refresh_events()
            self.plot_grid.refresh_gates()
        else:
            self._refresh_population_view()
        panel = self.plot_grid.active()
        self.inspector.set_record(record, panel.x_channel, panel.y_channel)
        self._refresh_stats()
        self._set_dirty()
        self._update_status(f"已更新通道 {channel} 的变换")

    def set_mode(self, mode: str) -> None:
        if mode not in {"scatter", "density", "histogram"}:
            return
        self.plot_grid.set_mode(mode)
        self.inspector.set_mode(mode)
        self._sync_mode_combo(mode)
        self.set_gate_tool(self._default_tool())
        self._update_tool_availability()
        self._refresh_stats()
        self._set_dirty()

    def _on_mode_combo(self, _index: int) -> None:
        self._schedule_mode_change(self.mode_combo.currentData())

    def _schedule_mode_change(self, mode: str) -> None:
        QtCore.QTimer.singleShot(0, lambda: self.set_mode(mode))

    def _on_layout_combo(self, _index: int) -> None:
        self._schedule_layout_change(int(self.layout_combo.currentData()))

    def _schedule_layout_change(self, count: int) -> None:
        QtCore.QTimer.singleShot(0, lambda: self.set_layout(int(count)))

    def _schedule_axis_change(self, x_channel: str, y_channel: str) -> None:
        QtCore.QTimer.singleShot(
            0, lambda: self.on_axis_changed(x_channel, y_channel)
        )

    def _sync_mode_combo(self, mode: str) -> None:
        self.mode_combo.blockSignals(True)
        idx = self.mode_combo.findData(mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        self.mode_combo.blockSignals(False)
        self.inspector.set_mode(mode)

    def set_layout(self, count: int) -> None:
        count = 4 if int(count) == 4 else 1
        self.plot_grid.set_layout(count)
        self.inspector.set_layout(count)
        self._sync_layout_combo(count)
        active = self.plot_grid.active()
        record = self.session.get_sample(self.current_sample_id)
        if record is not None:
            self.inspector.set_record(record, active.x_channel, active.y_channel)
        self.plot_grid.set_selected_gate(self.selected_gate_path)
        self._refresh_stats()
        self._set_dirty()

    def set_contours(self, show: bool) -> None:
        self.plot_grid.set_show_contours(show)
        self.inspector.set_contours(show)
        self._set_dirty()

    def _sync_layout_combo(self, count: int) -> None:
        self.layout_combo.blockSignals(True)
        idx = self.layout_combo.findData(count)
        if idx >= 0:
            self.layout_combo.setCurrentIndex(idx)
        self.layout_combo.blockSignals(False)
        self.inspector.set_layout(count)

    def _on_active_plot_changed(self, panel) -> None:
        record = self.session.get_sample(self.current_sample_id)
        if record is not None:
            self.inspector.set_record(record, panel.x_channel, panel.y_channel)
        self._sync_mode_combo(panel.mode)
        self.inspector.set_contours(panel.show_contours)
        self._refresh_stats()

    def reset_view(self) -> None:
        self.plot_grid.active().reset_view()

    # ------------------------------------------------------------------
    # Project file handling
    # ------------------------------------------------------------------
    def open_project(self) -> None:
        if not self._maybe_save_changes():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "打开 CytoDesk 项目",
            self._last_dir,
            "CytoDesk 项目 (*.cytodesk);;所有文件 (*)",
        )
        if not path:
            return
        self._last_dir = str(Path(path).parent)
        try:
            loaded = load_project(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "打开项目失败", str(exc))
            return

        self._install_loaded_project(loaded)
        self._update_status(f"已打开项目 {Path(path).name}")

        messages = []
        if loaded.missing_files:
            messages.append("以下 FCS 文件不存在：\n" + "\n".join(loaded.missing_files[:10]))
        if loaded.errors:
            messages.append("加载时出现问题：\n" + "\n".join(loaded.errors[:10]))
        if messages:
            QtWidgets.QMessageBox.warning(
                self, "项目已打开，但有警告", "\n\n".join(messages)
            )

    def _install_loaded_project(self, loaded) -> None:
        self.session = loaded.session
        self.project_path = loaded.path
        self.current_sample_id = None
        self.current_population_path = None
        self.selected_gate_path = None
        self.gate_parent_path = ("root",)
        self.sample_panel.set_session(self.session)
        self.plot_grid.set_session(self.session)
        self.gate_tree.set_session(self.session)
        self.stats_table.set_session(self.session)
        self.inspector.set_session(self.session)
        self._apply_layout(loaded.layout)
        if loaded.active_sample_id:
            self.activate_sample(loaded.active_sample_id)
        else:
            self._clear_view()
        self._set_dirty(False)
        self._update_actions()

    def save_project(self) -> None:
        if self.project_path is None:
            self.save_project_as()
            return
        try:
            save_project(
                self.project_path,
                self.session,
                active_sample_id=self.current_sample_id,
                layout=self._collect_layout(),
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "保存项目失败", str(exc))
            return
        self._set_dirty(False)
        self._update_status(f"已保存 {self.project_path.name}")

    def save_project_as(self) -> None:
        default = (
            str(self.project_path)
            if self.project_path
            else str(Path(self._last_dir) / "analysis.cytodesk")
        )
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "项目另存为",
            default,
            "CytoDesk 项目 (*.cytodesk)",
        )
        if not path:
            return
        if not path.lower().endswith(".cytodesk"):
            path += ".cytodesk"
        self.project_path = Path(path)
        self._last_dir = str(self.project_path.parent)
        self.save_project()

    def _collect_layout(self) -> dict:
        return {
            "layout": self.plot_grid.layout_count,
            "mode": self.plot_grid.active().mode,
            "active": self.plot_grid.panels.index(self.plot_grid.active()),
            "contours": self.plot_grid.active().show_contours,
            "panels": [
                {"x": panel.x_channel, "y": panel.y_channel}
                for panel in self.plot_grid.panels
            ],
        }

    def _apply_layout(self, layout: dict) -> None:
        if not layout:
            return
        count = 4 if int(layout.get("layout", 1)) == 4 else 1
        self.plot_grid.set_layout(count)
        mode = layout.get("mode", "scatter")
        self.plot_grid.set_mode(mode)
        panels = layout.get("panels") or []
        for i, data in enumerate(panels[:4]):
            panel = self.plot_grid.panels[i]
            panel.x_channel = data.get("x")
            panel.y_channel = data.get("y")
        contours = bool(layout.get("contours", True))
        self.plot_grid.set_show_contours(contours)
        self.inspector.set_contours(contours)
        self._sync_mode_combo(mode)
        self._sync_layout_combo(count)
        active_index = int(layout.get("active", 0))
        if 0 <= active_index < len(self.plot_grid.panels):
            panel = self.plot_grid.panels[active_index]
            if not panel.isHidden():
                self.plot_grid.set_active_panel(panel)

    def _maybe_save_changes(self) -> bool:
        if not self._dirty or not self.session.samples:
            return True
        answer = QtWidgets.QMessageBox.warning(
            self,
            "未保存的更改",
            "当前项目有未保存的更改。是否保存？",
            QtWidgets.QMessageBox.Save
            | QtWidgets.QMessageBox.Discard
            | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Save,
        )
        if answer == QtWidgets.QMessageBox.Cancel:
            return False
        if answer == QtWidgets.QMessageBox.Save:
            self.save_project()
            return not self._dirty
        return True

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_image(self) -> None:
        panel = self.plot_grid.active()
        if panel.record is None:
            return
        default_name = f"{panel.record.id}_{panel.x_channel or 'x'}_{panel.y_channel or 'y'}"
        path, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出当前图",
            str(Path(self._last_dir) / f"{default_name}.png"),
            "PNG 图像 (*.png);;SVG 矢量图 (*.svg)",
        )
        if not path:
            return
        self._last_dir = str(Path(path).parent)
        try:
            if path.lower().endswith(".svg") or "SVG" in selected_filter:
                if not path.lower().endswith(".svg"):
                    path += ".svg"
                generator = QSvgGenerator()
                generator.setFileName(path)
                size = panel.plot_widget.size()
                generator.setSize(size)
                generator.setViewBox(QtCore.QRect(0, 0, size.width(), size.height()))
                generator.setTitle(f"CytoDesk - {panel.record.id}")
                generator.setDescription(
                    f"{panel.x_channel} vs {panel.y_channel} ({panel.mode})"
                )
                painter = QtGui.QPainter(generator)
                export_state = panel.begin_vector_export()
                try:
                    panel.plot_widget.render(painter)
                finally:
                    painter.end()
                    panel.end_vector_export(export_state)
            else:
                if not path.lower().endswith(".png"):
                    path += ".png"
                exporter = ImageExporter(panel.plot_item)
                params = exporter.parameters()
                params["width"] = 1600
                exporter.export(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "导出图像失败", str(exc))
            return
        self._update_status(f"已导出图像 {Path(path).name}")

    def export_stats(self) -> None:
        if self.stats_table.table.rowCount() == 0:
            QtWidgets.QMessageBox.information(self, "没有统计数据", "当前还没有群体统计可导出。")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出统计 CSV",
            str(Path(self._last_dir) / "statistics.csv"),
            "CSV 文件 (*.csv)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        self._last_dir = str(Path(path).parent)
        try:
            self.stats_table.export_csv(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "导出失败", str(exc))
            return
        self._update_status(f"已导出统计 {Path(path).name}")

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    def show_about(self) -> None:
        QtWidgets.QMessageBox.about(
            self,
            "关于 CytoDesk",
            "<h3>CytoDesk 0.1.0</h3>"
            "<p>桌面流式细胞数据分析软件。</p>"
            "<p>分析内核：<a href='https://github.com/whitews/flowkit'>FlowKit</a>；"
            "界面：PySide6 + pyqtgraph。</p>"
            "<p>当前版本支持单样本 FCS 分析、手动门控、统计与项目保存。</p>",
        )

    def _set_dirty(self, dirty: bool = True) -> None:
        if dirty and not self.session.samples:
            return
        self._dirty = bool(dirty)
        self._update_title()

    def _update_title(self) -> None:
        name = self.project_path.name if self.project_path else "未命名项目"
        star = " *" if self._dirty else ""
        self.setWindowTitle(f"{name}{star} — CytoDesk")

    def _update_actions(self) -> None:
        has_samples = bool(self.session.samples)
        has_active = self.session.get_sample(self.current_sample_id) is not None
        has_stats = self.stats_table.table.rowCount() > 0
        for action in (
            self.action_save,
            self.action_save_as,
            self.action_open_project,
        ):
            action.setEnabled(True)
        self.action_export_stats.setEnabled(has_stats)
        self.action_export_image.setEnabled(has_active)
        self.action_reset_view.setEnabled(has_active)
        self.action_delete_gate.setEnabled(has_active and self.selected_gate_path is not None)
        self._update_tool_availability()
        if not has_samples:
            self.action_export_stats.setEnabled(False)
            self.action_export_image.setEnabled(False)
            self.action_reset_view.setEnabled(False)
            self.action_delete_gate.setEnabled(False)

    def _update_tool_availability(self) -> None:
        has_active = self.session.get_sample(self.current_sample_id) is not None
        histogram = has_active and self.plot_grid.active().mode == "histogram"
        for tool, action in self.tool_actions.items():
            action.setEnabled(has_active and (not histogram or tool == GateTool.RANGE))
        if histogram and self.plot_grid.active().tool != GateTool.RANGE:
            self.set_gate_tool(GateTool.RANGE)

    def _update_sample_status(self) -> None:
        record = self.session.get_sample(self.current_sample_id)
        if record is None:
            self.sample_status_label.setText("")
            return
        gates = len(self.session.gate_full_paths())
        self.sample_status_label.setText(
            f"{record.id} · {record.event_count:,} 事件 · {gates} 门"
        )

    def _update_status(self, message: str) -> None:
        self.status_label.setText(message)

    def _update_parent_status(self) -> None:
        record = self.session.get_sample(self.current_sample_id)
        parts = ["Root"]
        if record is not None:
            parts.append(record.id)
        if self.current_population_path is not None:
            parts.extend(self.current_population_path[1:])
        self.parent_status_label.setText("当前群体: " + " / ".join(parts))

    def _refresh_population_view(self) -> None:
        record = self.session.get_sample(self.current_sample_id)
        path = self.current_population_path
        if record is None or path is None:
            return
        if self.session.gate_by_path(path) is None:
            parent = path[:-1]
            if parent == ("root",):
                self.open_population(None)
            else:
                self.open_population(parent)
            return
        try:
            mask = self.session.gate_membership(record, path)
        except Exception:
            return
        if mask is None or not bool(np.any(mask)):
            parent = path[:-1]
            if parent == ("root",):
                self.open_population(None)
            else:
                self.open_population(parent)
            return
        label = f"{record.id} / {' / '.join(path[1:])}"
        self.gate_parent_path = path
        self.selected_gate_path = None
        self.plot_grid.set_population_context(mask, path, label)
        self.plot_grid.set_selected_gate(None)
        self.inspector.set_gate(None, None, None)
        self.gate_tree.select_population(path, emit=False)
        self._update_parent_status()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if not self._maybe_save_changes():
            event.ignore()
            return
        self._settings.setValue("geometry", self.saveGeometry())
        self._settings.setValue("last_dir", self._last_dir)
        event.accept()

    # ------------------------------------------------------------------
    # Drag & drop
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
            if any(
                path.suffix.lower() in {".fcs", ".cytodesk"} or path.is_dir()
                for path in paths
            ):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
        projects = [path for path in paths if path.suffix.lower() == ".cytodesk"]
        fcs_files = [path for path in paths if path.suffix.lower() == ".fcs"]
        for directory in (path for path in paths if path.is_dir()):
            fcs_files.extend(
                sorted(
                    child
                    for child in directory.iterdir()
                    if child.is_file() and child.suffix.lower() == ".fcs"
                )
            )
        if projects:
            if not self._maybe_save_changes():
                return
            try:
                loaded = load_project(projects[0])
            except Exception as exc:
                QtWidgets.QMessageBox.critical(self, "打开项目失败", str(exc))
                return
            self._install_loaded_project(loaded)
            event.acceptProposedAction()
            return
        if fcs_files:
            self._load_paths(fcs_files)
            event.acceptProposedAction()
            return
        event.ignore()
