"""Compensation matrix editor dialog."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from flowkit import Matrix
from flowutils import compensate as flow_compensate
from PySide6 import QtCore, QtGui, QtWidgets

from ..core.compensation import (
    align_matrix_to_detectors,
    clear_compensation,
    compensation_info,
    embedded_spill_text,
    fluorochrome_labels,
    read_matrix_file,
)
from ..core.session import AnalysisSession, SampleRecord


PREVIEW_EVENTS = 4_000
PREVIEW_CELL_SIZE = 78
PREVIEW_LABEL_WIDTH = 92
PREVIEW_HEADER_HEIGHT = 36


class _MatrixDelegate(QtWidgets.QStyledItemDelegate):
    """Numeric editor used by the compensation table."""

    def createEditor(self, parent, option, index):
        editor = QtWidgets.QDoubleSpinBox(parent)
        editor.setFrame(False)
        editor.setRange(-100.0, 100.0)
        editor.setDecimals(6)
        editor.setSingleStep(0.001)
        editor.setKeyboardTracking(False)
        return editor

    def setEditorData(self, editor, index):
        try:
            value = float(index.data(QtCore.Qt.EditRole))
        except (TypeError, ValueError):
            value = 0.0
        editor.setValue(value)

    def setModelData(self, editor, model, index):
        value = float(editor.value())
        model.setData(index, f"{value:.8g}", QtCore.Qt.EditRole)


class _PreviewCell(QtWidgets.QFrame):
    """One tiny plot in the compensation preview matrix."""

    def __init__(self, kind: str = "scatter", parent=None):
        super().__init__(parent)
        self.kind = kind
        self._has_range = False
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setObjectName("PreviewCell")
        self.setStyleSheet(
            "QFrame#PreviewCell { background: #ffffff; border: 1px solid #e2e6ec; }"
        )
        self.setFixedSize(PREVIEW_CELL_SIZE, PREVIEW_CELL_SIZE)

        self.plot = pg.PlotWidget(background="w")
        self.plot.setMenuEnabled(False)
        self.plot.getPlotItem().hideButtons()
        self.plot.getPlotItem().setMouseEnabled(False, False)
        self.plot.getPlotItem().setContentsMargins(0, 0, 0, 0)
        for axis_name in ("bottom", "left"):
            self.plot.getPlotItem().getAxis(axis_name).hide()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.addWidget(self.plot)

        if kind == "scatter":
            self.raw_item = pg.ScatterPlotItem(
                size=1.2,
                pen=None,
                brush=pg.mkBrush(145, 145, 145, 35),
                pxMode=True,
                antialias=False,
            )
            self.comp_item = pg.ScatterPlotItem(
                size=1.4,
                pen=None,
                brush=pg.mkBrush(45, 108, 223, 105),
                pxMode=True,
                antialias=False,
            )
            self.plot.addItem(self.raw_item)
            self.plot.addItem(self.comp_item)
        else:
            self.raw_item = pg.PlotDataItem(
                stepMode="center",
                fillLevel=0,
                brush=pg.mkBrush(145, 145, 145, 35),
                pen=None,
            )
            self.comp_item = pg.PlotDataItem(
                stepMode="center",
                fillLevel=0,
                brush=pg.mkBrush(45, 108, 223, 80),
                pen=pg.mkPen((45, 108, 223, 110)),
            )
            self.plot.addItem(self.raw_item)
            self.plot.addItem(self.comp_item)

    @staticmethod
    def _bounds(values: np.ndarray) -> tuple[float, float]:
        finite = np.asarray(values, dtype=float)
        finite = finite[np.isfinite(finite)]
        if len(finite) == 0:
            return 0.0, 1.0
        lo, hi = np.percentile(finite, [0.2, 99.8])
        if lo == hi:
            lo, hi = float(finite.min()), float(finite.max())
        if lo == hi:
            lo, hi = lo - 0.5, hi + 0.5
        pad = (hi - lo) * 0.05
        return float(lo - pad), float(hi + pad)

    def set_scatter(
        self,
        raw_x: np.ndarray,
        raw_y: np.ndarray,
        comp_x: np.ndarray,
        comp_y: np.ndarray,
        show_raw: bool,
        auto_range: bool,
    ) -> None:
        if show_raw and len(raw_x):
            self.raw_item.setData(x=raw_x, y=raw_y)
        else:
            self.raw_item.setData([], [])
        self.comp_item.setData(x=comp_x, y=comp_y)
        if auto_range or not self._has_range:
            x0, x1 = self._bounds(
                np.concatenate([comp_x, raw_x]) if show_raw and len(raw_x) else comp_x
            )
            y0, y1 = self._bounds(
                np.concatenate([comp_y, raw_y]) if show_raw and len(raw_y) else comp_y
            )
            self.plot.setXRange(x0, x1, padding=0)
            self.plot.setYRange(y0, y1, padding=0)
            self._has_range = True

    def set_hist(
        self,
        raw_values: np.ndarray,
        comp_values: np.ndarray,
        show_raw: bool,
        auto_range: bool,
    ) -> None:
        comp_values = np.asarray(comp_values, dtype=float)
        comp_values = comp_values[np.isfinite(comp_values)]
        if len(comp_values) == 0:
            self.raw_item.setData([], [])
            self.comp_item.setData([], [])
            return
        if show_raw:
            raw_values = np.asarray(raw_values, dtype=float)
            raw_values = raw_values[np.isfinite(raw_values)]
        else:
            raw_values = np.empty(0)

        finite = comp_values if len(raw_values) == 0 else np.concatenate([comp_values, raw_values])
        lo, hi = self._bounds(finite)
        edges = np.linspace(lo, hi, 37)
        comp_counts, _ = np.histogram(comp_values, bins=edges)
        self.comp_item.setData(
            x=edges, y=comp_counts, stepMode="center", fillLevel=0
        )
        raw_counts = None
        if show_raw and len(raw_values):
            raw_counts, _ = np.histogram(raw_values, bins=edges)
            self.raw_item.setData(
                x=edges, y=raw_counts, stepMode="center", fillLevel=0
            )
        else:
            self.raw_item.setData([], [])
        peak = max(
            int(comp_counts.max()) if len(comp_counts) else 0,
            int(raw_counts.max()) if raw_counts is not None and len(raw_counts) else 0,
        )
        if auto_range or not self._has_range:
            self.plot.setXRange(lo, hi, padding=0)
            self.plot.setYRange(0, max(peak * 1.1, 1.0), padding=0)
            self._has_range = True


class CompensationDialog(QtWidgets.QDialog):
    """Edit and apply a spillover matrix for one or more samples."""

    def __init__(
        self,
        session: AnalysisSession,
        record: SampleRecord,
        parent=None,
    ):
        super().__init__(parent)
        self.session = session
        self.record = record
        self.applied = False
        self.detectors, self.fluorochromes = fluorochrome_labels(record)

        self.setWindowTitle("补偿矩阵")
        self.resize(1540, 900)
        self.setModal(True)

        self._preview_raw: np.ndarray | None = None
        self._preview_indices: list[int] = []
        self._preview_raw_transformed: dict[str, np.ndarray] = {}
        self._preview_comp_cache: dict[str, np.ndarray] = {}
        self._preview_scatter_cells: dict[tuple[int, int], _PreviewCell] = {}
        self._preview_hist_cells: dict[int, _PreviewCell] = {}
        self._preview_force_all = True
        self._preview_timer = QtCore.QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(100)
        self._preview_timer.timeout.connect(self._update_preview)

        self._build_ui()
        self._load_current_matrix()
        self._update_source_label()
        self._prepare_preview_events()
        self._update_preview(force_all=True)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QtWidgets.QLabel(
            f"<b>{self.record.id}</b> · {self.record.event_count:,} 事件 · "
            f"{len(self.detectors)} 个荧光通道"
        )
        layout.addWidget(title)

        self.source_label = QtWidgets.QLabel("—")
        self.source_label.setStyleSheet("color:#555;")
        layout.addWidget(self.source_label)

        hint = QtWidgets.QLabel(
            "双击单元格编辑溢出系数；右侧按矩阵行/列顺序显示三角预览。"
            "对角线固定为 1；缺失的通道保持未补偿（单位矩阵）。"
        )
        hint.setStyleSheet("color:#777; font-size:9pt;")
        hint.setWordWrap(True)

        self.table = QtWidgets.QTableWidget()
        self.table.setItemDelegate(_MatrixDelegate(self.table))
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setMinimumSectionSize(64)
        self.table.itemChanged.connect(self._on_table_item_changed)

        self.all_check = QtWidgets.QCheckBox()
        self.all_check.setToolTip(
            "对工作区中所有拥有相同荧光通道的样本应用该矩阵"
        )
        matching = self._matching_records()
        self.all_check.setText(
            f"应用到所有通道匹配的样本（{len(matching)} 个）"
        )
        self.all_check.setVisible(len(matching) > 1)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        left_layout.addWidget(hint)
        left_layout.addWidget(self.table, 1)
        left_layout.addWidget(self.all_check)

        preview = self._build_preview_panel()

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(preview)
        split.setSizes([740, 780])
        layout.addWidget(split, 1)

        buttons = QtWidgets.QHBoxLayout()
        self.fcs_button = QtWidgets.QPushButton("从 FCS 载入")
        self.fcs_button.setToolTip("读取 FCS 文件内嵌的 $SPILLOVER / $SPILL 矩阵")
        self.import_button = QtWidgets.QPushButton("导入 CSV/TSV…")
        self.identity_button = QtWidgets.QPushButton("单位矩阵")
        self.clear_button = QtWidgets.QPushButton("移除补偿")
        self.apply_button = QtWidgets.QPushButton("应用")
        self.apply_button.setDefault(True)
        close_button = QtWidgets.QPushButton("关闭")

        self.fcs_button.clicked.connect(self._load_from_fcs)
        self.import_button.clicked.connect(self._import_matrix)
        self.identity_button.clicked.connect(self._set_identity)
        self.clear_button.clicked.connect(self._clear_compensation)
        self.apply_button.clicked.connect(self._apply_matrix)
        close_button.clicked.connect(self.accept)

        buttons.addWidget(self.fcs_button)
        buttons.addWidget(self.import_button)
        buttons.addWidget(self.identity_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color:#555;")
        layout.addWidget(self.status_label)

        if not self.detectors:
            self.table.setEnabled(False)
            for button in (
                self.fcs_button,
                self.import_button,
                self.identity_button,
                self.clear_button,
                self.apply_button,
            ):
                button.setEnabled(False)
            self.status_label.setText("该样本没有可用于补偿的荧光通道。")

    def _build_preview_panel(self) -> QtWidgets.QWidget:
        group = QtWidgets.QGroupBox("三角矩阵预览")
        layout = QtWidgets.QVBoxLayout(group)
        layout.setContentsMargins(6, 8, 6, 6)
        layout.setSpacing(4)

        options = QtWidgets.QHBoxLayout()
        options.setContentsMargins(0, 0, 0, 0)
        options.setSpacing(8)
        self.preview_show_raw = QtWidgets.QCheckBox("叠加未补偿事件")
        self.preview_show_raw.setChecked(True)
        self.preview_show_raw.setToolTip("灰色为未补偿事件，彩色为当前矩阵补偿后事件")
        self.preview_show_raw.toggled.connect(self._on_preview_option_changed)
        self.preview_auto_range = QtWidgets.QCheckBox("自动范围")
        self.preview_auto_range.setChecked(True)
        self.preview_auto_range.toggled.connect(self._on_preview_option_changed)
        options.addWidget(self.preview_show_raw)
        options.addWidget(self.preview_auto_range)
        options.addStretch(1)
        order_hint = QtWidgets.QLabel("列 = X，行 = Y · 仅显示下三角")
        order_hint.setStyleSheet("color:#777; font-size:8pt;")
        options.addWidget(order_hint)
        layout.addLayout(options)

        content = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(content)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setSpacing(2)

        def channel_text(detector: str) -> str:
            channel = self.record.channel(detector)
            return channel.short_label if channel is not None else detector

        for col, detector in enumerate(self.detectors):
            channel = self.record.channel(detector)
            text = channel_text(detector)
            label = QtWidgets.QLabel(text)
            label.setAlignment(QtCore.Qt.AlignCenter)
            label.setWordWrap(True)
            label.setFixedSize(PREVIEW_CELL_SIZE, PREVIEW_HEADER_HEIGHT)
            label.setStyleSheet("color:#555; font-size:8pt;")
            label.setToolTip((channel.label if channel is not None else detector) + "\n" + detector)
            grid.addWidget(label, 0, col + 1)

        for row, detector in enumerate(self.detectors):
            channel = self.record.channel(detector)
            text = channel_text(detector)
            row_label = QtWidgets.QLabel(text)
            row_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            row_label.setWordWrap(True)
            row_label.setFixedWidth(PREVIEW_LABEL_WIDTH)
            row_label.setStyleSheet("color:#555; font-size:8pt;")
            row_label.setToolTip((channel.label if channel is not None else detector) + "\n" + detector)
            grid.addWidget(row_label, row + 1, 0)

            for col in range(row + 1):
                x_detector = self.detectors[col]
                if col == row:
                    cell = _PreviewCell("hist")
                    cell.setToolTip(f"通道分布：{text}")
                    self._preview_hist_cells[row] = cell
                else:
                    cell = _PreviewCell("scatter")
                    cell.setToolTip(
                        f"X: {channel_text(x_detector)}\nY: {text}"
                    )
                    self._preview_scatter_cells[(row, col)] = cell
                grid.addWidget(cell, row + 1, col + 1)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        self.preview_info = QtWidgets.QLabel("")
        self.preview_info.setStyleSheet("color:#777; font-size:9pt;")
        self.preview_info.setWordWrap(True)
        layout.addWidget(self.preview_info)

        if len(self.detectors) < 2:
            self.preview_show_raw.setEnabled(False)
            self.preview_auto_range.setEnabled(False)
            self.preview_info.setText("至少需要两个荧光通道才能显示矩阵预览。")
        return group

    def _matching_records(self) -> list[SampleRecord]:
        wanted = set(self.detectors)
        result = []
        for record in self.session.samples.values():
            if wanted.issubset(set(fluorochrome_labels(record)[0])):
                result.append(record)
        return result

    # ------------------------------------------------------------------
    # Matrix table
    # ------------------------------------------------------------------
    def _build_table(self, size: int) -> None:
        self.table.clear()
        self.table.setRowCount(size)
        self.table.setColumnCount(size)
        row_labels = []
        col_labels = []
        for detector in self.detectors[:size]:
            channel = self.record.channel(detector)
            label = channel.short_label if channel else detector
            row_labels.append(label)
            col_labels.append(label)
        self.table.setVerticalHeaderLabels(row_labels)
        self.table.setHorizontalHeaderLabels(col_labels)
        for index, detector in enumerate(self.detectors[:size]):
            channel = self.record.channel(detector)
            tooltip = (
                f"{channel.label}\n{detector}"
                if channel is not None
                else detector
            )
            horizontal = self.table.horizontalHeaderItem(index)
            vertical = self.table.verticalHeaderItem(index)
            if horizontal is not None:
                horizontal.setToolTip(tooltip)
            if vertical is not None:
                vertical.setToolTip(tooltip)

    def _set_matrix(self, matrix: np.ndarray) -> None:
        values = np.array(matrix, dtype=float, copy=True)
        size = len(self.detectors)
        if values.shape != (size, size):
            values = align_matrix_to_detectors(None, values, self.detectors)
        np.fill_diagonal(values, 1.0)
        self.table.blockSignals(True)
        self._build_table(size)
        for row in range(size):
            for col in range(size):
                item = QtWidgets.QTableWidgetItem(f"{values[row, col]:.8g}")
                item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                if row == col:
                    item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                    item.setBackground(QtGui.QColor("#eef2f7"))
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)
        self._preview_force_all = True
        self._schedule_preview()

    def _matrix_from_table(self) -> np.ndarray:
        size = self.table.rowCount()
        values = np.empty((size, size), dtype=float)
        for row in range(size):
            for col in range(size):
                item = self.table.item(row, col)
                try:
                    values[row, col] = float(item.text()) if item else 0.0
                except (AttributeError, TypeError, ValueError) as exc:
                    raise ValueError(f"第 {row + 1} 行第 {col + 1} 列不是有效数值") from exc
        return values

    # ------------------------------------------------------------------
    # Live preview
    # ------------------------------------------------------------------
    def _prepare_preview_events(self) -> None:
        self._preview_raw = None
        self._preview_indices = []
        self._preview_raw_transformed = {}
        self._preview_comp_cache = {}
        self._preview_force_all = True
        if not self.detectors:
            return
        try:
            raw = np.asarray(self.record.sample.get_events(source="raw"), dtype=float)
            indices = [
                int(self.record.sample.get_channel_index(detector))
                for detector in self.detectors
            ]
        except Exception as exc:
            self.preview_info.setText(f"无法读取预览事件：{exc}")
            return
        if raw.ndim != 2 or len(raw) == 0 or any(index < 0 for index in indices):
            self.preview_info.setText("样本没有可用于预览的事件。")
            return
        total = len(raw)
        if total > PREVIEW_EVENTS:
            rng = np.random.default_rng(23)
            selection = np.sort(rng.choice(total, size=PREVIEW_EVENTS, replace=False))
            raw = raw[selection]
        self._preview_raw = raw
        self._preview_indices = indices
        try:
            for detector, index in zip(self.detectors, indices):
                spec = self.session.get_transform(detector)
                self._preview_raw_transformed[detector] = spec.apply(raw[:, index])
        except Exception as exc:
            self.preview_info.setText(f"无法计算预览变换：{exc}")
            return
        if len(self.detectors) < 2:
            self.preview_info.setText("至少需要两个荧光通道才能显示矩阵预览。")
        else:
            self.preview_info.setText(
                f"预览使用 {len(raw):,} / {total:,} 事件（已降采样）"
            )

    def _on_table_item_changed(self, _item) -> None:
        self._preview_force_all = False
        self._schedule_preview()

    def _on_preview_option_changed(self, _checked: bool) -> None:
        self._preview_force_all = True
        self._schedule_preview()

    def _schedule_preview(self, *_args) -> None:
        if self._preview_raw is None:
            return
        self._preview_timer.start()

    def _update_preview(self, force_all: bool = False) -> None:
        if (
            self._preview_raw is None
            or not self._preview_indices
            or not self._preview_scatter_cells
        ):
            return
        force_all = force_all or self._preview_force_all
        try:
            values = self._matrix_from_table()
            comp = flow_compensate.compensate(
                self._preview_raw.copy(), values, self._preview_indices
            )
            comp_transformed: dict[str, np.ndarray] = {}
            for detector, index in zip(self.detectors, self._preview_indices):
                spec = self.session.get_transform(detector)
                comp_transformed[detector] = spec.apply(comp[:, index])
        except Exception as exc:
            self.preview_info.setText(f"预览失败：{exc}")
            return

        changed = {
            detector
            for detector, values_ in comp_transformed.items()
            if force_all
            or detector not in self._preview_comp_cache
            or not np.array_equal(self._preview_comp_cache[detector], values_)
        }

        show_raw = self.preview_show_raw.isChecked()
        auto_range = self.preview_auto_range.isChecked()
        for (row, col), cell in self._preview_scatter_cells.items():
            x_detector = self.detectors[col]
            y_detector = self.detectors[row]
            if force_all or x_detector in changed or y_detector in changed:
                cell.set_scatter(
                    self._preview_raw_transformed[x_detector],
                    self._preview_raw_transformed[y_detector],
                    comp_transformed[x_detector],
                    comp_transformed[y_detector],
                    show_raw,
                    auto_range,
                )
        for row, cell in self._preview_hist_cells.items():
            detector = self.detectors[row]
            if force_all or detector in changed:
                cell.set_hist(
                    self._preview_raw_transformed[detector],
                    comp_transformed[detector],
                    show_raw,
                    auto_range,
                )

        self._preview_comp_cache = comp_transformed
        self._preview_force_all = False
        self.preview_info.setText(
            f"预览 {len(self._preview_raw):,} / {self.record.event_count:,} 事件 · "
            "编辑中的矩阵（尚未应用）"
        )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def _load_current_matrix(self) -> None:
        info = compensation_info(self.record)
        size = len(self.detectors)
        if info is None:
            self._set_matrix(np.eye(size))
            return
        values = align_matrix_to_detectors(
            info.detectors, info.matrix, self.detectors
        )
        self._set_matrix(values)

    def _update_source_label(self) -> None:
        if self.record.compensation_error:
            self.source_label.setText(
                f"当前状态：<span style='color:#b42318'>补偿失败</span> · "
                f"{self.record.compensation_error}"
            )
            return
        info = compensation_info(self.record)
        if info is None:
            self.source_label.setText("当前状态：未补偿")
        elif self.record.compensation_source == "custom":
            self.source_label.setText("当前状态：自定义补偿矩阵")
        else:
            self.source_label.setText("当前状态：FCS 自动补偿（$SPILLOVER / $SPILL）")

    def _set_status(self, message: str, error: bool = False) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            "color:#b42318;" if error else "color:#1a7f37;"
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _load_from_fcs(self) -> None:
        spill = embedded_spill_text(self.record.sample)
        if not spill:
            self._set_status("FCS 文件中没有 $SPILLOVER / $SPILL 补偿矩阵。", error=True)
            return
        try:
            matrix = Matrix(
                spill,
                self.detectors,
                self.fluorochromes,
                null_channels=getattr(self.record.sample, "null_channels", None),
            )
            values = align_matrix_to_detectors(
                matrix.detectors, matrix.matrix, self.detectors
            )
        except Exception as exc:
            self._set_status(f"无法读取 FCS 补偿矩阵：{exc}", error=True)
            return
        self._set_matrix(values)
        self._set_status("已从 FCS 读取矩阵，点击“应用”使其生效。")

    def _import_matrix(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "导入补偿矩阵",
            str(Path.home()),
            "补偿矩阵 (*.csv *.tsv *.txt);;所有文件 (*)",
        )
        if not path:
            return
        try:
            labels, matrix = read_matrix_file(path)
            aliases = {
                detector: [fluorochrome]
                for detector, fluorochrome in zip(
                    self.detectors, self.fluorochromes
                )
                if fluorochrome and fluorochrome != detector
            }
            values = align_matrix_to_detectors(
                labels, matrix, self.detectors, aliases=aliases
            )
        except Exception as exc:
            self._set_status(f"导入失败：{exc}", error=True)
            return
        self._set_matrix(values)
        self._set_status(
            f"已导入 {Path(path).name}（{matrix.shape[0]} × {matrix.shape[0]}），"
            "点击“应用”使其生效。"
        )

    def _set_identity(self) -> None:
        self._set_matrix(np.eye(len(self.detectors)))
        self._set_status("已重置为单位矩阵，点击“应用”使其生效。")

    def _target_records(self) -> list[SampleRecord]:
        if self.all_check.isChecked():
            return self._matching_records()
        return [self.record]

    def _clear_compensation(self) -> None:
        targets = self._target_records()
        for record in targets:
            clear_compensation(record)
            self.session.invalidate_results()
        self.applied = True
        self._set_matrix(np.eye(len(self.detectors)))
        self._update_source_label()
        names = "、".join(record.id for record in targets[:3])
        if len(targets) > 3:
            names += " …"
        self._set_status(f"已移除 {len(targets)} 个样本的补偿（{names}）。")

    def _apply_matrix(self) -> None:
        if not self.detectors:
            return
        try:
            values = self._matrix_from_table()
        except ValueError as exc:
            self._set_status(str(exc), error=True)
            return
        targets = self._target_records()
        applied = 0
        failures: list[str] = []
        wanted = set(self.detectors)
        for record in targets:
            record_detectors = [
                detector
                for detector in fluorochrome_labels(record)[0]
                if detector in wanted
            ]
            if not record_detectors:
                failures.append(f"{record.id}: 没有匹配的荧光通道")
                continue
            try:
                aligned = align_matrix_to_detectors(
                    self.detectors, values, record_detectors
                )
                self.session.apply_compensation(
                    record, record_detectors, aligned
                )
                applied += 1
            except Exception as exc:
                failures.append(f"{record.id}: {exc}")
        self.applied = self.applied or applied > 0
        self._update_source_label()
        if failures:
            self._set_status(
                f"已应用 {applied} 个样本；{len(failures)} 个失败：" + "；".join(failures[:3]),
                error=True,
            )
        else:
            self._set_status(
                f"已应用补偿矩阵到 {applied} 个样本。转置/门控结果将在关闭对话框后重算。"
            )
