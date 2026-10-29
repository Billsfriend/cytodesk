"""Population statistics table."""

from __future__ import annotations

import csv

from PySide6 import QtCore, QtWidgets

from ..core.session import AnalysisSession, PopulationStat
from .labels import gate_type_label


class _NumericItem(QtWidgets.QTableWidgetItem):
    def __init__(self, value: float, text: str):
        super().__init__(text)
        self._value = value
        self.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

    def __lt__(self, other):
        if isinstance(other, _NumericItem):
            return self._value < other._value
        return super().__lt__(other)


class StatsTable(QtWidgets.QWidget):
    gate_selected = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session: AnalysisSession | None = None
        self._stats: list[PopulationStat] = []
        self._x_channel: str | None = None
        self._y_channel: str | None = None

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["群体", "类型", "计数", "%Parent", "%Total"]
        )
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for i in range(1, 5):
            header.setSectionResizeMode(i, QtWidgets.QHeaderView.ResizeToContents)
        self.table.cellClicked.connect(self._on_cell_clicked)

        title = QtWidgets.QLabel("群体统计")
        title.setStyleSheet("font-weight: 600; padding: 4px 6px; color: #333;")
        self.info_label = QtWidgets.QLabel("")
        self.info_label.setStyleSheet("color:#777; padding-right:6px;")
        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.info_label)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)

    def set_session(self, session: AnalysisSession | None) -> None:
        self.session = session

    def set_stats(
        self,
        stats: list[PopulationStat],
        x_channel: str | None = None,
        y_channel: str | None = None,
        total_events: int | None = None,
        x_name: str | None = None,
        y_name: str | None = None,
    ) -> None:
        self._stats = list(stats)
        self._x_channel = x_channel
        self._y_channel = y_channel

        x_label = x_name or x_channel or "X"
        y_label = y_name or y_channel or "Y"

        columns = ["群体", "类型", "计数", "%Parent", "%Total"]
        if x_channel:
            columns.append(f"{x_label} 中位数")
        if y_channel:
            columns.append(f"{y_label} 中位数")
        self.table.setSortingEnabled(False)
        self.table.clear()
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for i in range(1, len(columns)):
            header.setSectionResizeMode(i, QtWidgets.QHeaderView.ResizeToContents)

        self.table.setRowCount(len(self._stats))
        for row, stat in enumerate(self._stats):
            name_item = QtWidgets.QTableWidgetItem(
                "    " * max(0, len(stat.full_path) - 2) + stat.gate_name
            )
            name_item.setData(QtCore.Qt.UserRole, stat.full_path)
            name_item.setToolTip(" / ".join(stat.full_path[1:]))
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(gate_type_label(stat.gate_type)))
            self.table.setItem(row, 2, _NumericItem(float(stat.count), f"{stat.count:,}"))
            self.table.setItem(
                row, 3, _NumericItem(stat.percent_parent, f"{stat.percent_parent:.2f}%")
            )
            self.table.setItem(
                row, 4, _NumericItem(stat.percent_total, f"{stat.percent_total:.2f}%")
            )
            col = 5
            if x_channel:
                raw = self._to_raw(x_channel, stat.medians.get(x_channel))
                self.table.setItem(
                    row, col, _NumericItem(raw if raw is not None else float("nan"),
                                           self._fmt(raw))
                )
                col += 1
            if y_channel:
                raw = self._to_raw(y_channel, stat.medians.get(y_channel))
                self.table.setItem(
                    row, col, _NumericItem(raw if raw is not None else float("nan"),
                                           self._fmt(raw))
                )

        self.table.setSortingEnabled(True)
        gate_count = len(self._stats)
        if total_events:
            self.info_label.setText(
                f"{gate_count} 个群体 · 样本总事件 {total_events:,}"
            )
        else:
            self.info_label.setText(f"{gate_count} 个群体")

    def _to_raw(self, channel: str, value: float | None) -> float | None:
        if value is None or self.session is None:
            return None
        try:
            return self.session.get_transform(channel).inverse_scalar(value)
        except Exception:
            return value

    @staticmethod
    def _fmt(value: float | None) -> str:
        if value is None:
            return ""
        try:
            if abs(value) >= 1e5:
                return f"{value:,.0f}"
            if abs(value) >= 100:
                return f"{value:,.1f}"
            return f"{value:,.3g}"
        except Exception:
            return ""

    def _on_cell_clicked(self, row: int, _column: int) -> None:
        item = self.table.item(row, 0)
        if item is not None:
            path = item.data(QtCore.Qt.UserRole)
            if path:
                self.gate_selected.emit(path)

    def export_csv(self, path: str) -> None:
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            headers = [
                self.table.horizontalHeaderItem(i).text()
                for i in range(self.table.columnCount())
            ]
            writer.writerow(headers)
            for row in range(self.table.rowCount()):
                row_values = []
                for col in range(self.table.columnCount()):
                    item = self.table.item(row, col)
                    if col == 0 and item is not None:
                        path_data = item.data(QtCore.Qt.UserRole)
                        row_values.append(" / ".join(path_data[1:]) if path_data else item.text().strip())
                    else:
                        row_values.append(item.text() if item is not None else "")
                writer.writerow(row_values)
