"""Gate hierarchy tree with Root and sample nodes."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..core.session import AnalysisSession, SampleRecord
from .labels import gate_type_label


KIND_ROLE = QtCore.Qt.UserRole + 1


class GateTree(QtWidgets.QWidget):
    gate_selected = QtCore.Signal(object)
    population_open_requested = QtCore.Signal(object)
    rename_requested = QtCore.Signal(object)
    delete_requested = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session: AnalysisSession | None = None
        self.sample_record: SampleRecord | None = None
        self._counts: dict[str, int] = {}
        self._selected: tuple[str, ...] | None = None
        self._population: tuple[str, ...] | None = None
        self._item_map: dict[tuple[str, ...], QtWidgets.QTreeWidgetItem] = {}
        self._root_item: QtWidgets.QTreeWidgetItem | None = None
        self._sample_item: QtWidgets.QTreeWidgetItem | None = None

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["群体", "类型", "计数"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
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
        layout.addWidget(self.tree)

    # ------------------------------------------------------------------
    def set_session(self, session: AnalysisSession | None) -> None:
        self.session = session
        self.sample_record = None
        self._selected = None
        self._population = None
        self.refresh()

    def set_sample(self, record: SampleRecord | None) -> None:
        self.sample_record = record
        self._selected = None
        self._population = None
        self.refresh()

    def set_counts(self, counts: dict[str, int]) -> None:
        self._counts = counts
        self._apply_counts()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        self._item_map = {}

        total = self.sample_record.event_count if self.sample_record else 0
        root_item = QtWidgets.QTreeWidgetItem(["Root", "根", f"{total:,}"])
        root_item.setData(0, KIND_ROLE, "root")
        root_item.setData(0, QtCore.Qt.UserRole, ("root",))
        root_item.setToolTip(0, "全部事件")
        root_font = root_item.font(0)
        root_font.setBold(True)
        for col in range(3):
            root_item.setFont(col, root_font)
        root_item.setExpanded(True)
        self.tree.addTopLevelItem(root_item)
        self._root_item = root_item

        sample_name = self.sample_record.id if self.sample_record else "（未加载样本）"
        sample_item = QtWidgets.QTreeWidgetItem([sample_name, "样本", f"{total:,}"])
        sample_item.setData(0, KIND_ROLE, "sample")
        sample_item.setFont(0, root_font)
        sample_item.setToolTip(0, str(self.sample_record.path) if self.sample_record else "")
        sample_item.setExpanded(True)
        root_item.addChild(sample_item)
        self._sample_item = sample_item

        if self.session is not None:
            for node, full_path in self.session.iter_gate_nodes():
                parent_path = full_path[:-1]
                if parent_path == ("root",):
                    parent_item = sample_item
                else:
                    parent_item = self._item_map.get(parent_path)
                item = QtWidgets.QTreeWidgetItem()
                item.setText(0, full_path[-1])
                item.setText(1, gate_type_label(node.gate_type))
                item.setData(0, QtCore.Qt.UserRole, full_path)
                item.setData(0, KIND_ROLE, "gate")
                item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                item.setToolTip(0, " / ".join(full_path[1:]) + "（双击打开群体）")
                if parent_item is None:
                    sample_item.addChild(item)
                else:
                    parent_item.addChild(item)
                self._item_map[full_path] = item

        self.tree.expandAll()
        self._apply_counts()
        self.tree.blockSignals(False)

        if self._population is not None and self._population in self._item_map:
            self.select_population(self._population, emit=False)
        elif self._selected and self._selected in self._item_map:
            self.select_path(self._selected, emit=False)
        elif self._sample_item is not None:
            self.tree.setCurrentItem(self._sample_item)
        self._update_population_style()

    def _apply_counts(self) -> None:
        total = self.sample_record.event_count if self.sample_record else 0
        if self._root_item is not None:
            self._root_item.setText(2, f"{total:,}" if total else "")
        if self._sample_item is not None:
            self._sample_item.setText(2, f"{total:,}" if total else "")
        for path, item in self._item_map.items():
            count = self._counts.get("/".join(path))
            item.setText(2, f"{count:,}" if count is not None else "")

    # ------------------------------------------------------------------
    def select_path(self, full_path: tuple[str, ...] | None, emit: bool = True) -> None:
        self._selected = tuple(full_path) if full_path else None
        item = self._item_map.get(self._selected) if self._selected else None
        self.tree.blockSignals(not emit)
        if item is not None:
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)
        else:
            self.tree.clearSelection()
        self.tree.blockSignals(False)

    def select_population(self, path: tuple[str, ...] | None, emit: bool = False) -> None:
        self._population = tuple(path) if path else None
        if self._population is None:
            item = self._sample_item
        else:
            item = self._item_map.get(self._population)
        self.tree.blockSignals(not emit)
        if item is not None:
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)
        self.tree.blockSignals(False)
        self._update_population_style()

    def _update_population_style(self) -> None:
        for path, item in self._item_map.items():
            font = item.font(0)
            font.setBold(path == self._population)
            item.setFont(0, font)
        if self._sample_item is not None:
            font = self._sample_item.font(0)
            font.setBold(True)
            self._sample_item.setFont(0, font)
        if self._root_item is not None:
            font = self._root_item.font(0)
            font.setBold(True)
            self._root_item.setFont(0, font)

    def selected_path(self) -> tuple[str, ...] | None:
        item = self.tree.currentItem()
        if item is None or item.data(0, KIND_ROLE) != "gate":
            return None
        return item.data(0, QtCore.Qt.UserRole)

    def _on_selection_changed(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        if item.data(0, KIND_ROLE) != "gate":
            return
        path = item.data(0, QtCore.Qt.UserRole)
        self._selected = path
        self.gate_selected.emit(path)

    def _on_item_clicked(self, item: QtWidgets.QTreeWidgetItem, _column: int) -> None:
        if item.data(0, KIND_ROLE) in {"root", "sample"}:
            self.population_open_requested.emit(None)

    def _on_item_double_clicked(self, item: QtWidgets.QTreeWidgetItem, _column: int) -> None:
        kind = item.data(0, KIND_ROLE)
        if kind == "gate":
            self.population_open_requested.emit(item.data(0, QtCore.Qt.UserRole))
        elif kind in {"root", "sample"}:
            self.population_open_requested.emit(None)

    def _show_menu(self, pos: QtCore.QPoint) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        kind = item.data(0, KIND_ROLE)
        self.tree.setCurrentItem(item)
        menu = QtWidgets.QMenu(self)
        if kind in {"root", "sample"}:
            open_action = menu.addAction("返回全样本视图")
            action = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if action is open_action:
                self.population_open_requested.emit(None)
            return
        if kind != "gate":
            return
        path = item.data(0, QtCore.Qt.UserRole)
        open_action = menu.addAction("打开此门并圈门")
        menu.addSeparator()
        rename_action = menu.addAction("重命名…")
        menu.addSeparator()
        delete_action = menu.addAction("删除门")
        action = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if action is open_action:
            self.population_open_requested.emit(path)
        elif action is rename_action:
            self.rename_requested.emit(path)
        elif action is delete_action:
            self.delete_requested.emit(path)
