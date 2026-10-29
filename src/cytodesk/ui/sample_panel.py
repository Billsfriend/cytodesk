"""Sample browser panel."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..core.session import AnalysisSession


class SamplePanel(QtWidgets.QWidget):
    open_files_requested = QtCore.Signal()
    open_folder_requested = QtCore.Signal()
    sample_activated = QtCore.Signal(str)
    remove_requested = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session: AnalysisSession | None = None
        self._active_id: str | None = None

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setUniformItemSizes(False)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)

        open_file = QtWidgets.QToolButton()
        open_file.setText("打开 FCS")
        open_file.setToolTip("打开一个或多个 FCS 文件 (Ctrl+O)")
        open_file.clicked.connect(self.open_files_requested)
        open_folder = QtWidgets.QToolButton()
        open_folder.setText("打开文件夹")
        open_folder.setToolTip("加载文件夹中的所有 FCS 文件")
        open_folder.clicked.connect(self.open_folder_requested)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setContentsMargins(4, 4, 4, 0)
        button_row.addWidget(open_file)
        button_row.addWidget(open_folder)
        button_row.addStretch(1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(button_row)
        layout.addWidget(self.list_widget, 1)

    def set_session(self, session: AnalysisSession | None, active_id: str | None = None) -> None:
        self.session = session
        self._active_id = active_id
        self.refresh()

    def refresh(self, keep_active: bool = True) -> None:
        current = self._active_id if keep_active else None
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        if self.session is not None:
            for record in self.session.samples.values():
                item = QtWidgets.QListWidgetItem(
                    f"{record.id}\n{record.event_count:,} 事件 · {len(record.channels)} 通道"
                )
                item.setData(QtCore.Qt.UserRole, record.id)
                item.setToolTip(str(record.path))
                font = item.font()
                font.setPointSizeF(max(8.0, font.pointSizeF() + 0.5))
                item.setFont(font)
                self.list_widget.addItem(item)
                if record.id == current:
                    self.list_widget.setCurrentItem(item)
        self.list_widget.blockSignals(False)
        if current:
            self.set_active(current)

    def set_active(self, sample_id: str | None) -> None:
        self._active_id = sample_id
        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(QtCore.Qt.UserRole) == sample_id:
                self.list_widget.setCurrentItem(item)
                break
        else:
            self.list_widget.clearSelection()
        self.list_widget.blockSignals(False)

    def _on_item_clicked(self, item: QtWidgets.QListWidgetItem) -> None:
        sample_id = item.data(QtCore.Qt.UserRole)
        if sample_id:
            self.sample_activated.emit(sample_id)

    def _on_item_double_clicked(self, item: QtWidgets.QListWidgetItem) -> None:
        sample_id = item.data(QtCore.Qt.UserRole)
        if sample_id:
            self.sample_activated.emit(sample_id)

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        item = self.list_widget.itemAt(self.list_widget.mapFromGlobal(event.globalPos()))
        if item is None:
            return
        sample_id = item.data(QtCore.Qt.UserRole)
        menu = QtWidgets.QMenu(self)
        remove_action = menu.addAction("从工作区移除")
        action = menu.exec(event.globalPos())
        if action is remove_action and sample_id:
            self.remove_requested.emit(sample_id)
