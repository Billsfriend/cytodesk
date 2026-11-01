"""Combined sample and gate hierarchy tree.

The tree mirrors FlowJo's workspace: ``Root`` contains every loaded sample and
each sample contains the shared gate hierarchy.  Population counts are shown
per sample, which makes batch-gating results directly comparable.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..core.session import AnalysisSession, SampleRecord
from .labels import gate_type_label


KIND_ROLE = QtCore.Qt.UserRole + 1
PATH_ROLE = QtCore.Qt.UserRole
SAMPLE_ROLE = QtCore.Qt.UserRole + 2


class GateTree(QtWidgets.QWidget):
    open_files_requested = QtCore.Signal()
    open_folder_requested = QtCore.Signal()
    sample_activated = QtCore.Signal(str)
    remove_requested = QtCore.Signal(str)
    gate_selected = QtCore.Signal(object)
    population_open_requested = QtCore.Signal(object)
    rename_requested = QtCore.Signal(object)
    delete_requested = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session: AnalysisSession | None = None
        self.active_sample_id: str | None = None
        self._counts_by_sample: dict[str, dict[str, int]] = {}
        self._status_by_id: dict[str, str] = {}
        self._selected: tuple[str, ...] | None = None
        self._population: tuple[str, ...] | None = None
        self._item_map: dict[tuple[str, tuple[str, ...]], QtWidgets.QTreeWidgetItem] = {}
        self._sample_items: dict[str, QtWidgets.QTreeWidgetItem] = {}
        self._root_item: QtWidgets.QTreeWidgetItem | None = None
        self._last_selected_emitted: tuple[str, tuple[str, ...]] | None = None

        open_files = QtWidgets.QToolButton()
        open_files.setText("打开 FCS")
        open_files.setToolTip("打开一个或多个 FCS 文件 (Ctrl+O)")
        open_files.clicked.connect(self.open_files_requested)
        open_folder = QtWidgets.QToolButton()
        open_folder.setText("打开文件夹")
        open_folder.setToolTip("加载文件夹中的所有 FCS 文件")
        open_folder.clicked.connect(self.open_folder_requested)
        self.sample_count_label = QtWidgets.QLabel("")
        self.sample_count_label.setStyleSheet("color:#777; padding-left:4px;")

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setContentsMargins(4, 4, 4, 0)
        toolbar.setSpacing(4)
        toolbar.addWidget(open_files)
        toolbar.addWidget(open_folder)
        toolbar.addStretch(1)
        toolbar.addWidget(self.sample_count_label)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["群体", "类型", "计数"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setTextElideMode(QtCore.Qt.ElideMiddle)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_menu)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(toolbar)
        layout.addWidget(self.tree, 1)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def set_session(self, session: AnalysisSession | None) -> None:
        self.session = session
        self.active_sample_id = None
        self._selected = None
        self._population = None
        self._counts_by_sample = {}
        self._status_by_id = {}
        self.refresh()

    def set_active_sample(self, sample_id: str | None) -> None:
        self.active_sample_id = sample_id
        self._selected = None
        self._population = None
        self.refresh()

    def set_sample(self, record: SampleRecord | None) -> None:
        """Backwards-compatible alias used by older call sites/tests."""

        self.set_active_sample(record.id if record is not None else None)

    def set_statuses(self, statuses: dict[str, str]) -> None:
        self._status_by_id = dict(statuses)
        self._apply_statuses()

    def set_counts(self, counts: dict[str, int], sample_id: str | None = None) -> None:
        sample_id = sample_id or self.active_sample_id
        if not sample_id:
            return
        self._counts_by_sample[sample_id] = dict(counts)
        self._apply_counts()

    def set_counts_by_sample(self, counts: dict[str, dict[str, int]]) -> None:
        self._counts_by_sample = {
            sample_id: dict(values) for sample_id, values in counts.items()
        }
        self._apply_counts()

    def _apply_statuses(self) -> None:
        for sample_id, item in self._sample_items.items():
            status = self._status_by_id.get(sample_id, "")
            item.setText(1, f"样本 · {status}" if status else "样本")
            item.setToolTip(1, status)

    def _apply_counts(self) -> None:
        for sample_id, item in self._sample_items.items():
            record = self.session.samples.get(sample_id) if self.session else None
            if record is not None:
                item.setText(2, f"{record.event_count:,}" if record.event_count else "")
        for (sample_id, path), item in self._item_map.items():
            count = self._counts_by_sample.get(sample_id, {}).get("/".join(path))
            item.setText(2, f"{count:,}" if count is not None else "")

    # ------------------------------------------------------------------
    # Tree construction
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        self._item_map = {}
        self._sample_items = {}
        self._last_selected_emitted = None

        samples = list(self.session.samples.values()) if self.session else []
        total_events = sum(record.event_count for record in samples)
        root_item = QtWidgets.QTreeWidgetItem(
            ["Root", "全部样本", f"{total_events:,}" if total_events else ""]
        )
        root_item.setData(0, KIND_ROLE, "root")
        root_font = root_item.font(0)
        root_font.setBold(True)
        for col in range(3):
            root_item.setFont(col, root_font)
        root_item.setToolTip(
            0,
            f"{len(samples)} 个样本 · 共 {total_events:,} 事件"
            if samples
            else "尚未加载样本",
        )
        root_item.setExpanded(True)
        self.tree.addTopLevelItem(root_item)
        self._root_item = root_item

        gate_nodes = list(self.session.iter_gate_nodes()) if self.session else []
        expand_samples = len(samples) * max(1, len(gate_nodes)) <= 200
        for record in samples:
            status = self._status_by_id.get(record.id, "")
            sample_item = QtWidgets.QTreeWidgetItem(
                [
                    record.id,
                    f"样本 · {status}" if status else "样本",
                    f"{record.event_count:,}",
                ]
            )
            sample_item.setData(0, KIND_ROLE, "sample")
            sample_item.setData(0, SAMPLE_ROLE, record.id)
            sample_item.setToolTip(0, str(record.path))
            sample_font = sample_item.font(0)
            sample_font.setBold(True)
            for col in range(3):
                sample_item.setFont(col, sample_font)
            if status:
                sample_item.setToolTip(1, status)
            root_item.addChild(sample_item)
            self._sample_items[record.id] = sample_item
            sample_item.setExpanded(expand_samples or record.id == self.active_sample_id)

            for node, full_path in gate_nodes:
                parent_path = full_path[:-1]
                if parent_path == ("root",):
                    parent_item = sample_item
                else:
                    parent_item = self._item_map.get((record.id, parent_path))
                item = QtWidgets.QTreeWidgetItem()
                item.setText(0, full_path[-1])
                item.setText(1, gate_type_label(node.gate_type))
                item.setData(0, KIND_ROLE, "gate")
                item.setData(0, PATH_ROLE, full_path)
                item.setData(0, SAMPLE_ROLE, record.id)
                item.setToolTip(
                    0,
                    f"{record.id} / {' / '.join(full_path[1:])}\n双击打开该群体",
                )
                if parent_item is None:
                    sample_item.addChild(item)
                else:
                    parent_item.addChild(item)
                if expand_samples or record.id == self.active_sample_id:
                    item.setExpanded(True)
                self._item_map[(record.id, full_path)] = item

        self._apply_counts()
        self._apply_statuses()
        count = len(samples)
        self.sample_count_label.setText(f"{count} 个样本" if count else "")
        self.tree.blockSignals(False)

        if self.active_sample_id is None and samples:
            self.active_sample_id = samples[0].id
        self._select_current()
        self._update_population_style()

    def _select_current(self) -> None:
        item = None
        if self.active_sample_id is not None:
            if self._population is not None:
                item = self._item_map.get((self.active_sample_id, self._population))
            if item is None and self._selected is not None:
                item = self._item_map.get((self.active_sample_id, self._selected))
            if item is None:
                item = self._sample_items.get(self.active_sample_id)
        elif self._root_item is not None:
            item = self._root_item
        self.tree.blockSignals(True)
        if item is not None:
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)
        else:
            self.tree.clearSelection()
        self.tree.blockSignals(False)

    def _update_population_style(self) -> None:
        for (sample_id, path), item in self._item_map.items():
            font = item.font(0)
            font.setBold(sample_id == self.active_sample_id and path == self._population)
            item.setFont(0, font)
        for sample_id, item in self._sample_items.items():
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------
    def select_path(
        self, full_path: tuple[str, ...] | None, emit: bool = True
    ) -> None:
        self._selected = tuple(full_path) if full_path else None
        item = (
            self._item_map.get((self.active_sample_id, self._selected))
            if self.active_sample_id is not None and self._selected is not None
            else None
        )
        self.tree.blockSignals(not emit)
        if item is not None:
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)
        else:
            self.tree.clearSelection()
        self.tree.blockSignals(False)

    def select_population(
        self, path: tuple[str, ...] | None, emit: bool = False
    ) -> None:
        self._population = tuple(path) if path else None
        item = None
        if self.active_sample_id is not None:
            if self._population is None:
                item = self._sample_items.get(self.active_sample_id)
            else:
                item = self._item_map.get((self.active_sample_id, self._population))
        self.tree.blockSignals(not emit)
        if item is not None:
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)
        self.tree.blockSignals(False)
        self._update_population_style()

    def selected_path(self) -> tuple[str, ...] | None:
        item = self.tree.currentItem()
        if item is None or item.data(0, KIND_ROLE) != "gate":
            return None
        if item.data(0, SAMPLE_ROLE) != self.active_sample_id:
            return None
        return item.data(0, PATH_ROLE)

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def _on_selection_changed(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        if item.data(0, KIND_ROLE) != "gate":
            return
        sample_id = item.data(0, SAMPLE_ROLE)
        if sample_id != self.active_sample_id:
            return
        path = item.data(0, PATH_ROLE)
        self._selected = path
        self._last_selected_emitted = (sample_id, path)
        self.gate_selected.emit(path)

    def _activate_sample(self, sample_id: str) -> None:
        if sample_id and sample_id != self.active_sample_id:
            self.sample_activated.emit(sample_id)

    def _on_item_clicked(self, item: QtWidgets.QTreeWidgetItem, _column: int) -> None:
        kind = item.data(0, KIND_ROLE)
        if kind == "root":
            if self.active_sample_id is not None:
                self.population_open_requested.emit(None)
            return
        if kind == "sample":
            sample_id = item.data(0, SAMPLE_ROLE)
            # Activating the already-active sample returns to its root view.
            self.sample_activated.emit(sample_id)
            return
        if kind != "gate":
            return
        sample_id = item.data(0, SAMPLE_ROLE)
        path = item.data(0, PATH_ROLE)
        if sample_id != self.active_sample_id:
            self._activate_sample(sample_id)
            # The tree was rebuilt by MainWindow.activate_sample, so emit the
            # gate selection explicitly for the newly active sample.
        self._selected = path
        if self._last_selected_emitted == (sample_id, path):
            self._last_selected_emitted = None
        else:
            self.gate_selected.emit(path)

    def _on_item_double_clicked(
        self, item: QtWidgets.QTreeWidgetItem, _column: int
    ) -> None:
        kind = item.data(0, KIND_ROLE)
        if kind == "root":
            if self.active_sample_id is not None:
                self.population_open_requested.emit(None)
            return
        if kind == "sample":
            self.sample_activated.emit(item.data(0, SAMPLE_ROLE))
            return
        if kind != "gate":
            return
        sample_id = item.data(0, SAMPLE_ROLE)
        path = item.data(0, PATH_ROLE)
        if sample_id != self.active_sample_id:
            self._activate_sample(sample_id)
        self.population_open_requested.emit(path)

    def _show_menu(self, pos: QtCore.QPoint) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        kind = item.data(0, KIND_ROLE)
        self.tree.setCurrentItem(item)
        menu = QtWidgets.QMenu(self)
        if kind == "root":
            open_action = menu.addAction("返回当前样本全视图")
            action = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if action is open_action and self.active_sample_id is not None:
                self.population_open_requested.emit(None)
            return
        if kind == "sample":
            sample_id = item.data(0, SAMPLE_ROLE)
            activate_action = menu.addAction("设为当前样本")
            menu.addSeparator()
            remove_action = menu.addAction("从工作区移除")
            action = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if action is activate_action:
                self.sample_activated.emit(sample_id)
            elif action is remove_action:
                self.remove_requested.emit(sample_id)
            return
        if kind != "gate":
            return

        sample_id = item.data(0, SAMPLE_ROLE)
        path = item.data(0, PATH_ROLE)
        open_action = menu.addAction("打开此门并圈门")
        menu.addSeparator()
        rename_action = menu.addAction("重命名…")
        menu.addSeparator()
        delete_action = menu.addAction("删除门")
        action = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if action in (open_action, rename_action, delete_action):
            self._activate_sample(sample_id)
        if action is open_action:
            self.population_open_requested.emit(path)
        elif action is rename_action:
            self.rename_requested.emit(path)
        elif action is delete_action:
            self.delete_requested.emit(path)
