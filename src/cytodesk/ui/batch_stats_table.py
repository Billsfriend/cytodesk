"""Cross-sample statistics table for batch gating."""

from __future__ import annotations

import csv

from PySide6 import QtCore, QtWidgets

from ..core.session import AnalysisSession, BatchPopulationStat
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


class BatchStatsTable(QtWidgets.QWidget):
    """Population statistics across all gated samples."""

    result_activated = QtCore.Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session: AnalysisSession | None = None
        self._stats: list[BatchPopulationStat] = []

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["样本", "群体", "类型", "计数", "%Parent", "%Total"]
        )
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        for i in range(2, 6):
            header.setSectionResizeMode(i, QtWidgets.QHeaderView.ResizeToContents)
        self.table.cellClicked.connect(self._on_cell_clicked)

        title = QtWidgets.QLabel("跨样本统计")
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
        stats: list[BatchPopulationStat],
        x_channel: str | None = None,
        y_channel: str | None = None,
        x_name: str | None = None,
        y_name: str | None = None,
        gated_samples: int | None = None,
        total_samples: int | None = None,
    ) -> None:
        self._stats = list(stats)

        x_label = x_name or x_channel or "X"
        y_label = y_name or y_channel or "Y"
        columns = ["样本", "群体", "类型", "计数", "%Parent", "%Total"]
        if x_channel:
            columns.append(f"{x_label} 中位数")
        if y_channel:
            columns.append(f"{y_label} 中位数")

        self.table.setSortingEnabled(False)
        self.table.clear()
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        for i in range(2, len(columns)):
            header.setSectionResizeMode(i, QtWidgets.QHeaderView.ResizeToContents)

        # Show sample cells only on the first row of each sample to make the
        # table easier to scan while keeping every row sortable/exportable.
        previous_sample: str | None = None
        self.table.setRowCount(len(self._stats))
        for row, stat in enumerate(self._stats):
            sample_item = QtWidgets.QTableWidgetItem(stat.sample_id)
            sample_item.setData(
                QtCore.Qt.UserRole, (stat.sample_id, stat.full_path)
            )
            sample_item.setToolTip(
                f"{stat.sample_id} · {stat.sample_total:,} 事件"
            )
            if previous_sample == stat.sample_id:
                sample_item.setText("")
            else:
                font = sample_item.font()
                font.setBold(True)
                sample_item.setFont(font)
                previous_sample = stat.sample_id
            self.table.setItem(row, 0, sample_item)

            name_item = QtWidgets.QTableWidgetItem(
                "    " * max(0, len(stat.full_path) - 2) + stat.gate_name
            )
            name_item.setData(
                QtCore.Qt.UserRole, (stat.sample_id, stat.full_path)
            )
            name_item.setToolTip(" / ".join(stat.full_path[1:]))
            self.table.setItem(row, 1, name_item)
            self.table.setItem(
                row, 2, QtWidgets.QTableWidgetItem(gate_type_label(stat.gate_type))
            )
            self.table.setItem(row, 3, _NumericItem(float(stat.count), f"{stat.count:,}"))
            self.table.setItem(
                row,
                4,
                _NumericItem(stat.percent_parent, f"{stat.percent_parent:.2f}%"),
            )
            self.table.setItem(
                row,
                5,
                _NumericItem(stat.percent_total, f"{stat.percent_total:.2f}%"),
            )

            col = 6
            for channel, name in ((x_channel, x_name), (y_channel, y_name)):
                if not channel:
                    continue
                raw = self._to_raw(channel, stat.medians.get(channel))
                self.table.setItem(
                    row,
                    col,
                    _NumericItem(
                        raw if raw is not None else float("nan"),
                        self._fmt(raw),
                    ),
                )
                col += 1

        self.table.setSortingEnabled(True)

        gate_count = len(self._stats)
        sample_count = len({stat.sample_id for stat in self._stats})
        if total_samples is not None:
            gated = gated_samples if gated_samples is not None else sample_count
            text = f"{gated}/{total_samples} 个样本已门控 · {gate_count} 行"
        elif sample_count:
            text = f"{sample_count} 个样本 · {gate_count} 行"
        else:
            text = "尚未运行批量门控"
        self.info_label.setText(text)

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
        item = self.table.item(row, 0) or self.table.item(row, 1)
        if item is None:
            return
        data = item.data(QtCore.Qt.UserRole)
        if data:
            sample_id, path = data
            self.result_activated.emit(sample_id, path)

    def export_csv(self, path: str) -> None:
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            headers = [
                self.table.horizontalHeaderItem(i).text()
                for i in range(self.table.columnCount())
            ]
            writer.writerow(headers)
            for row in range(self.table.rowCount()):
                values = []
                for col in range(self.table.columnCount()):
                    item = self.table.item(row, col)
                    if col == 1 and item is not None:
                        data = item.data(QtCore.Qt.UserRole)
                        values.append(
                            " / ".join(data[1][1:]) if data else item.text().strip()
                        )
                    elif col == 0 and item is not None:
                        data = item.data(QtCore.Qt.UserRole)
                        values.append(data[0] if data else item.text().strip())
                    else:
                        values.append(item.text() if item is not None else "")
                writer.writerow(values)
