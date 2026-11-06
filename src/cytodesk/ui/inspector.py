"""Right-hand inspector panel."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..core.session import AnalysisSession, SampleRecord
from ..core.transforms import (
    KIND_LABELS,
    TransformSpec,
    default_spec_for_kind,
)
from .labels import gate_type_label


class TransformEditor(QtWidgets.QGroupBox):
    apply_requested = QtCore.Signal(str, object)

    def __init__(self, title: str, parent=None):
        super().__init__(title, parent)
        self._channel: str | None = None
        self._channel_info = None
        self._updating = False

        self.channel_label = QtWidgets.QLabel("—")
        self.channel_label.setStyleSheet("color:#666;")
        self.kind_combo = QtWidgets.QComboBox()
        for kind, label in KIND_LABELS.items():
            self.kind_combo.addItem(label, kind)
        self.kind_combo.activated.connect(self._on_kind_changed)

        self.param_widgets: dict[str, QtWidgets.QDoubleSpinBox] = {}
        self._form = QtWidgets.QFormLayout()
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setSpacing(4)

        self.spin_t = self._make_spin(0.0, 1e9, 2, 262144.0)
        self.spin_w = self._make_spin(0.0, 20.0, 3, 0.5)
        self.spin_m = self._make_spin(0.0, 20.0, 3, 4.5)
        self.spin_a = self._make_spin(-1e6, 1e6, 3, 0.0)
        self.param_widgets = {"t": self.spin_t, "w": self.spin_w, "m": self.spin_m, "a": self.spin_a}
        self._param_labels: dict[str, QtWidgets.QLabel] = {}
        param_titles = {
            "t": "T (top of scale)",
            "w": "W (linear decades)",
            "m": "M (decades)",
            "a": "A (negative decades)",
        }
        for key in ("t", "w", "m", "a"):
            label = QtWidgets.QLabel(param_titles[key])
            self._param_labels[key] = label
            self._form.addRow(label, self.param_widgets[key])

        self.apply_button = QtWidgets.QPushButton("应用变换")
        self.apply_button.clicked.connect(self._on_apply)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        layout.addWidget(self.channel_label)
        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("类型", self.kind_combo)
        layout.addLayout(form)
        layout.addLayout(self._form)
        layout.addWidget(self.apply_button)

    @staticmethod
    def _make_spin(lo: float, hi: float, decimals: int, value: float) -> QtWidgets.QDoubleSpinBox:
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setDecimals(decimals)
        spin.setValue(value)
        spin.setSingleStep(0.1)
        spin.setKeyboardTracking(False)
        return spin

    def set_channel(self, channel: str | None, info, spec: TransformSpec | None) -> None:
        self._channel = channel
        self._channel_info = info
        self._updating = True
        if channel:
            self.channel_label.setText(info.label if info else channel)
            self.setEnabled(True)
        else:
            self.channel_label.setText("—")
            self.setEnabled(False)
        if spec is not None:
            index = self.kind_combo.findData(spec.kind)
            if index >= 0:
                self.kind_combo.setCurrentIndex(index)
            for key, spin in self.param_widgets.items():
                if key in spec.params:
                    spin.setValue(float(spec.params[key]))
        self._update_param_visibility()
        self._updating = False

    def _on_kind_changed(self, _index: int) -> None:
        if self._updating or not self._channel or self._channel_info is None:
            return
        kind = self.kind_combo.currentData()
        spec = default_spec_for_kind(kind, self._channel_info)
        self._updating = True
        for key, spin in self.param_widgets.items():
            if key in spec.params:
                spin.setValue(float(spec.params[key]))
        self._update_param_visibility()
        self._updating = False

    def _update_param_visibility(self) -> None:
        kind = self.kind_combo.currentData()
        order = ["t", "w", "m", "a"]
        if kind == "linear":
            order = ["t", "a"]
        elif kind == "log":
            order = ["t", "m"]
        elif kind == "asinh":
            order = ["t", "m", "a"]
        for key in ("t", "w", "m", "a"):
            visible = key in order
            self._param_labels[key].setVisible(visible)
            self.param_widgets[key].setVisible(visible)

    def _on_apply(self) -> None:
        if not self._channel:
            return
        spec = self.current_spec()
        self.apply_requested.emit(self._channel, spec)

    def current_spec(self) -> TransformSpec:
        kind = self.kind_combo.currentData()
        params = {}
        for key, spin in self.param_widgets.items():
            params[key] = float(spin.value())
        return TransformSpec(kind=kind, params=params)


class InspectorPanel(QtWidgets.QWidget):
    axis_changed = QtCore.Signal(str, str)
    transform_applied = QtCore.Signal(str, object)
    mode_changed = QtCore.Signal(str)
    layout_changed = QtCore.Signal(int)
    contours_changed = QtCore.Signal(bool)
    gate_name_changed = QtCore.Signal(object, str)
    gate_color_changed = QtCore.Signal(object, str)
    compensation_edit_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session: AnalysisSession | None = None
        self.record: SampleRecord | None = None
        self._gate_path: tuple[str, ...] | None = None
        self._gate_color: str | None = None
        self._channel_signature: list[tuple[str, str]] = []
        self._updating = False

        content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Sample info --------------------------------------------------
        sample_group = QtWidgets.QGroupBox("样本")
        sample_form = QtWidgets.QFormLayout(sample_group)
        self.sample_label = QtWidgets.QLabel("—")
        self.events_label = QtWidgets.QLabel("—")
        self.comp_label = QtWidgets.QLabel("—")
        self.path_label = QtWidgets.QLabel("—")
        self.path_label.setWordWrap(True)
        self.path_label.setStyleSheet("color:#666; font-size:9pt;")
        self.comp_button = QtWidgets.QPushButton("编辑…")
        self.comp_button.setToolTip("查看、导入或编辑补偿矩阵")
        self.comp_button.clicked.connect(self.compensation_edit_requested)
        comp_row = QtWidgets.QHBoxLayout()
        comp_row.setContentsMargins(0, 0, 0, 0)
        comp_row.setSpacing(4)
        comp_row.addWidget(self.comp_label, 1)
        comp_row.addWidget(self.comp_button)
        sample_form.addRow("名称", self.sample_label)
        sample_form.addRow("事件数", self.events_label)
        sample_form.addRow("补偿", comp_row)
        sample_form.addRow("文件", self.path_label)
        layout.addWidget(sample_group)

        # Plot settings ------------------------------------------------
        plot_group = QtWidgets.QGroupBox("绘图")
        plot_form = QtWidgets.QFormLayout(plot_group)
        self.x_combo = QtWidgets.QComboBox()
        self.y_combo = QtWidgets.QComboBox()
        self.x_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.y_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.x_combo.activated.connect(self._on_axis_changed)
        self.y_combo.activated.connect(self._on_axis_changed)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItem("散点图", "scatter")
        self.mode_combo.addItem("密度图", "density")
        self.mode_combo.addItem("直方图", "histogram")
        self.mode_combo.activated.connect(self._on_mode_changed)
        self.layout_combo = QtWidgets.QComboBox()
        self.layout_combo.addItem("单图 1 × 1", 1)
        self.layout_combo.addItem("四图 2 × 2", 4)
        self.layout_combo.activated.connect(self._on_layout_changed)
        self.contour_check = QtWidgets.QCheckBox("显示等高线")
        self.contour_check.setChecked(True)
        self.contour_check.setToolTip("仅在密度图模式下生效")
        self.contour_check.toggled.connect(self._on_contours_changed)
        plot_form.addRow("X 通道", self.x_combo)
        plot_form.addRow("Y 通道", self.y_combo)
        plot_form.addRow("显示模式", self.mode_combo)
        plot_form.addRow("图布局", self.layout_combo)
        plot_form.addRow("密度图", self.contour_check)
        layout.addWidget(plot_group)

        # Transforms ---------------------------------------------------
        self.x_transform = TransformEditor("X 通道变换")
        self.y_transform = TransformEditor("Y 通道变换")
        self.x_transform.apply_requested.connect(self.transform_applied)
        self.y_transform.apply_requested.connect(self.transform_applied)
        layout.addWidget(self.x_transform)
        layout.addWidget(self.y_transform)

        # Gate properties ---------------------------------------------
        self.gate_group = QtWidgets.QGroupBox("门属性")
        gate_form = QtWidgets.QFormLayout(self.gate_group)
        self.gate_path_label = QtWidgets.QLabel("未选择")
        self.gate_path_label.setWordWrap(True)
        self.gate_type_label = QtWidgets.QLabel("—")
        self.gate_name_edit = QtWidgets.QLineEdit()
        self.gate_name_edit.returnPressed.connect(self._apply_gate_name)
        self.gate_name_button = QtWidgets.QPushButton("重命名")
        self.gate_name_button.clicked.connect(self._apply_gate_name)
        self.gate_color_button = QtWidgets.QPushButton("选择颜色…")
        self.gate_color_button.clicked.connect(self._choose_gate_color)
        name_row = QtWidgets.QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.addWidget(self.gate_name_edit, 1)
        name_row.addWidget(self.gate_name_button)
        gate_form.addRow("路径", self.gate_path_label)
        gate_form.addRow("类型", self.gate_type_label)
        gate_form.addRow("名称", name_row)
        gate_form.addRow("", self.gate_color_button)
        layout.addWidget(self.gate_group)
        layout.addStretch(1)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(content)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.set_gate(None, None, None)
        self.setEnabled(False)

    # ------------------------------------------------------------------
    def set_session(self, session: AnalysisSession | None) -> None:
        self.session = session
        self.setEnabled(session is not None and bool(session.samples))

    def set_record(self, record: SampleRecord | None, x_channel: str | None, y_channel: str | None) -> None:
        self.record = record
        self._updating = True
        if record is None:
            self.sample_label.setText("—")
            self.events_label.setText("—")
            self.comp_label.setText("—")
            self.comp_label.setStyleSheet("")
            self.comp_label.setToolTip("")
            self.path_label.setText("—")
            self.x_combo.clear()
            self.y_combo.clear()
            self._channel_signature = []
            self.x_transform.set_channel(None, None, None)
            self.y_transform.set_channel(None, None, None)
            self._updating = False
            return

        self.sample_label.setText(record.id)
        self.events_label.setText(f"{record.event_count:,}")
        if getattr(record.sample, "compensation", None) is not None:
            if record.compensation_source == "custom":
                self.comp_label.setText("自定义矩阵")
                self.comp_label.setToolTip("已应用用户编辑的补偿矩阵")
            else:
                self.comp_label.setText("FCS 自动补偿")
                self.comp_label.setToolTip("已应用 FCS 文件内嵌的 $SPILLOVER 矩阵")
            self.comp_label.setStyleSheet("color:#1a7f37;")
        elif record.compensation_error:
            self.comp_label.setText("FCS 补偿失败")
            self.comp_label.setStyleSheet("color:#b42318;")
            self.comp_label.setToolTip(record.compensation_error)
        else:
            self.comp_label.setText("未补偿")
            self.comp_label.setStyleSheet("color:#777;")
            self.comp_label.setToolTip("FCS 文件中没有 $SPILLOVER/$SPILL 矩阵")
        self.path_label.setText(str(record.path))

        channels = record.transform_candidates()
        signature = [(channel.pnn, channel.label) for channel in channels]
        if signature != self._channel_signature:
            for combo in (self.x_combo, self.y_combo):
                combo.clear()
                for channel in channels:
                    combo.addItem(channel.label, channel.pnn)
            self._channel_signature = signature
        for combo, selected in ((self.x_combo, x_channel), (self.y_combo, y_channel)):
            idx = combo.findData(selected)
            if idx >= 0 and combo.currentIndex() != idx:
                combo.setCurrentIndex(idx)
        self._update_transform_editors()
        self._updating = False
        self.setEnabled(True)

    def _update_transform_editors(self) -> None:
        if self.record is None or self.session is None:
            return
        x = self.x_combo.currentData()
        y = self.y_combo.currentData()
        if x:
            self.x_transform.set_channel(
                x, self.record.channel(x), self.session.get_transform(x)
            )
        else:
            self.x_transform.set_channel(None, None, None)
        if y:
            self.y_transform.set_channel(
                y, self.record.channel(y), self.session.get_transform(y)
            )
        else:
            self.y_transform.set_channel(None, None, None)

    def _on_axis_changed(self, _index: int) -> None:
        if self._updating:
            return
        self._update_transform_editors()
        x = self.x_combo.currentData()
        y = self.y_combo.currentData()
        if x and y:
            self.axis_changed.emit(x, y)

    def _on_mode_changed(self, _index: int) -> None:
        if not self._updating:
            self.mode_changed.emit(self.mode_combo.currentData())

    def _on_layout_changed(self, _index: int) -> None:
        if not self._updating:
            self.layout_changed.emit(int(self.layout_combo.currentData()))

    def _on_contours_changed(self, checked: bool) -> None:
        if not self._updating:
            self.contours_changed.emit(bool(checked))

    def set_mode(self, mode: str) -> None:
        self._updating = True
        idx = self.mode_combo.findData(mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        self.contour_check.setEnabled(mode == "density")
        self._updating = False

    def set_contours(self, show: bool) -> None:
        self._updating = True
        self.contour_check.setChecked(bool(show))
        self._updating = False

    def set_layout(self, count: int) -> None:
        self._updating = True
        idx = self.layout_combo.findData(count)
        if idx >= 0:
            self.layout_combo.setCurrentIndex(idx)
        self._updating = False

    # ------------------------------------------------------------------
    def set_gate(
        self,
        path: tuple[str, ...] | None,
        gate,
        color: str | None,
    ) -> None:
        self._gate_path = tuple(path) if path else None
        self._gate_color = color
        enabled = gate is not None
        self.gate_group.setEnabled(enabled)
        if not enabled:
            self.gate_path_label.setText("未选择")
            self.gate_type_label.setText("—")
            self.gate_name_edit.clear()
            self.gate_color_button.setStyleSheet("")
            return
        self.gate_path_label.setText(" / ".join(path[1:]) if path else "")
        self.gate_type_label.setText(gate_type_label(getattr(gate, "gate_type", type(gate).__name__)))
        self.gate_name_edit.setText(getattr(gate, "gate_name", ""))
        if color:
            self.gate_color_button.setStyleSheet(
                f"background-color: {color}; color: white; border-radius: 3px; padding: 3px 8px;"
            )

    def _apply_gate_name(self) -> None:
        if self._gate_path is None:
            return
        name = self.gate_name_edit.text().strip()
        if name and name != self._gate_path[-1]:
            self.gate_name_changed.emit(self._gate_path, name)

    def _choose_gate_color(self) -> None:
        if self._gate_path is None:
            return
        initial = QtGui.QColor(self._gate_color) if self._gate_color else QtGui.QColor("#2d6cdf")
        color = QtWidgets.QColorDialog.getColor(
            initial, self, "选择门颜色", QtWidgets.QColorDialog.ShowAlphaChannel
        )
        if color.isValid():
            self.gate_color_changed.emit(self._gate_path, color.name())
