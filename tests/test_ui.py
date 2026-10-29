from pathlib import Path

import pytest

from cytodesk.core.session import load_sample_file
from cytodesk.ui.main_window import MainWindow


LEARN_DIR = Path(__file__).resolve().parents[2]
FCS_PATH = LEARN_DIR / "FlowKit-master" / "data" / "test_data_2d_01.fcs"
COMP_FCS_PATH = (
    LEARN_DIR
    / "FlowKit-master"
    / "data"
    / "8_color_data_set"
    / "fcs_files"
    / "101_DEN084Y5_15_E01_008_clean.fcs"
)


@pytest.fixture(autouse=True)
def _no_close_prompt(monkeypatch):
    monkeypatch.setattr(MainWindow, "_maybe_save_changes", lambda self: True)


def test_imports():
    from cytodesk import __version__

    assert __version__


def test_main_window_smoke(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window.windowTitle().endswith("CytoDesk")


@pytest.mark.skipif(not FCS_PATH.exists(), reason="FlowKit example data not available")
def test_window_activate_sample(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    record = load_sample_file(FCS_PATH)
    window.session.add_record(record)
    window.sample_panel.refresh()
    window.activate_sample(record.id)
    assert window.current_sample_id == record.id
    assert window.plot_grid.active().x_channel is not None
    assert window.plot_grid.active().y_channel is not None


@pytest.mark.skipif(not FCS_PATH.exists(), reason="FlowKit example data not available")
def test_draw_rectangle_and_parent_logic(qtbot, monkeypatch):
    from PySide6 import QtCore, QtTest

    from cytodesk.plotting import GateTool
    from cytodesk.ui import main_window as mw

    names = iter(["RectA", "RectB"])
    monkeypatch.setattr(
        mw.QtWidgets.QInputDialog,
        "getText",
        staticmethod(lambda *args, **kwargs: (next(names), True)),
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    record = load_sample_file(FCS_PATH)
    window.session.add_record(record)
    window.activate_sample(record.id)
    panel = window.plot_grid.active()
    window.set_gate_tool(GateTool.RECTANGLE)
    viewport = panel.plot_widget.viewport()
    vb = panel.plot_item.vb

    def point(fx, fy):
        xr = vb.viewRange()
        x = xr[0][0] + (xr[0][1] - xr[0][0]) * fx
        y = xr[1][0] + (xr[1][1] - xr[1][0]) * fy
        scene_pos = vb.mapToScene(vb.mapFromView(QtCore.QPointF(x, y)))
        return panel.plot_widget.mapFromScene(scene_pos)

    def draw(fx0, fy0, fx1, fy1):
        window.set_gate_tool(GateTool.RECTANGLE)
        QtTest.QTest.mousePress(
            viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point(fx0, fy0)
        )
        QtTest.QTest.mouseMove(viewport, point(fx1, fy1), 20)
        QtTest.QTest.mouseRelease(
            viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point(fx1, fy1)
        )

    draw(0.25, 0.25, 0.55, 0.55)
    assert window.session.gate_full_paths() == [("root", "RectA")]
    # After creating a gate, the next gate is a sibling by default.
    draw(0.62, 0.62, 0.82, 0.82)
    assert window.session.gate_full_paths() == [
        ("root", "RectA"),
        ("root", "RectB"),
    ]
    assert window.stats_table.table.rowCount() == 2
    window._set_dirty(False)


@pytest.mark.skipif(not FCS_PATH.exists(), reason="FlowKit example data not available")
def test_window_gate_and_layout(qtbot):
    import numpy as np

    from cytodesk.core.gates import make_rectangle_gate

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    record = load_sample_file(FCS_PATH)
    window.session.add_record(record)
    window.activate_sample(record.id)
    panel = window.plot_grid.active()
    x_channel, y_channel = panel.x_channel, panel.y_channel
    matrix, _ = window.session.transformed_matrix(record, [x_channel, y_channel])
    x0, x1 = np.percentile(matrix[x_channel], [30, 70])
    y0, y1 = np.percentile(matrix[y_channel], [30, 70])
    gate = make_rectangle_gate(
        "G1",
        x_channel,
        y_channel,
        (float(x0), float(x1)),
        (float(y0), float(y1)),
        window.session.get_transform_id(x_channel),
        window.session.get_transform_id(y_channel),
    )
    path = window.session.add_gate(gate, ("root",))
    window._run_gating(record)
    window.gate_tree.refresh()
    window.plot_grid.refresh_gates()
    window.select_gate(path)
    window._refresh_stats()
    assert len(panel._gate_items) == 1
    assert window.stats_table.table.rowCount() == 1
    assert any("% parent" in label.toPlainText() for label in panel._gate_labels)

    window.set_layout(4)
    assert window.plot_grid.layout_count == 4
    assert sum(not p.isHidden() for p in window.plot_grid.panels) == 4
    window.set_layout(1)
    assert sum(not p.isHidden() for p in window.plot_grid.panels) == 1
    window._set_dirty(False)


@pytest.mark.skipif(not FCS_PATH.exists(), reason="FlowKit example data not available")
def test_histogram_mode(qtbot):
    from cytodesk.plotting import GateTool

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    record = load_sample_file(FCS_PATH)
    window.session.add_record(record)
    window.activate_sample(record.id)
    panel = window.plot_grid.active()
    assert panel.tool == GateTool.ELLIPSE
    window.set_mode("histogram")
    assert panel.mode == "histogram"
    assert panel._hist_counts is not None
    assert panel.tool == GateTool.RANGE
    assert not window.tool_actions[GateTool.RECTANGLE].isEnabled()
    assert window.tool_actions[GateTool.RANGE].isEnabled()
    window.set_mode("scatter")
    assert window.tool_actions[GateTool.RECTANGLE].isEnabled()
    assert panel.tool == GateTool.ELLIPSE
    window._set_dirty(False)


@pytest.mark.skipif(not FCS_PATH.exists(), reason="FlowKit example data not available")
def test_density_contours_toggle(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    record = load_sample_file(FCS_PATH)
    window.session.add_record(record)
    window.activate_sample(record.id)
    window.set_mode("density")
    panel = window.plot_grid.active()
    assert panel._contour_items
    window.set_contours(False)
    assert not panel._contour_items
    window.set_contours(True)
    assert panel._contour_items
    window._set_dirty(False)


@pytest.mark.skipif(not FCS_PATH.exists(), reason="FlowKit example data not available")
def test_double_click_child_gate(qtbot, monkeypatch):
    import numpy as np
    from PySide6 import QtCore, QtTest

    from cytodesk.core.gates import make_rectangle_gate
    from cytodesk.ui import main_window as mw

    monkeypatch.setattr(
        mw.QtWidgets.QInputDialog,
        "getText",
        staticmethod(lambda *args, **kwargs: ("Child", True)),
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    record = load_sample_file(FCS_PATH)
    window.session.add_record(record)
    window.activate_sample(record.id)
    panel = window.plot_grid.active()
    x_channel, y_channel = panel.x_channel, panel.y_channel
    matrix, _ = window.session.transformed_matrix(record, [x_channel, y_channel])
    x0, x1 = np.percentile(matrix[x_channel], [20, 80])
    y0, y1 = np.percentile(matrix[y_channel], [20, 80])
    parent_gate = make_rectangle_gate(
        "Parent",
        x_channel,
        y_channel,
        (float(x0), float(x1)),
        (float(y0), float(y1)),
        window.session.get_transform_id(x_channel),
        window.session.get_transform_id(y_channel),
    )
    parent_path = window.session.add_gate(parent_gate, ("root",))
    window._run_gating(record)
    window.gate_tree.refresh()
    window.plot_grid.refresh_gates()
    window._refresh_stats()

    viewport = panel.plot_widget.viewport()
    vb = panel.plot_item.vb

    def point(x, y):
        scene_pos = vb.mapToScene(vb.mapFromView(QtCore.QPointF(x, y)))
        return panel.plot_widget.mapFromScene(scene_pos)

    center = point((x0 + x1) / 2, (y0 + y1) / 2)
    QtTest.QTest.mouseDClick(
        viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, center
    )
    assert window.gate_parent_path == parent_path

    # Draw the child ellipse inside the parent.
    child_start = point(x0 + (x1 - x0) * 0.3, y0 + (y1 - y0) * 0.3)
    child_end = point(x0 + (x1 - x0) * 0.6, y0 + (y1 - y0) * 0.6)
    QtTest.QTest.mousePress(
        viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, child_start
    )
    QtTest.QTest.mouseMove(viewport, child_end, 20)
    QtTest.QTest.mouseRelease(
        viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, child_end
    )
    assert ("root", "Parent", "Child") in window.session.gate_full_paths()
    window._set_dirty(False)


@pytest.mark.skipif(not FCS_PATH.exists(), reason="FlowKit example data not available")
def test_drag_inside_gate_moves_it(qtbot):
    import numpy as np
    from PySide6 import QtCore, QtTest

    from cytodesk.core.gates import gate_bounds, make_rectangle_gate

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    record = load_sample_file(FCS_PATH)
    window.session.add_record(record)
    window.activate_sample(record.id)
    panel = window.plot_grid.active()
    x_channel, y_channel = panel.x_channel, panel.y_channel
    matrix, _ = window.session.transformed_matrix(record, [x_channel, y_channel])
    x0, x1 = np.percentile(matrix[x_channel], [30, 70])
    y0, y1 = np.percentile(matrix[y_channel], [30, 70])
    gate = make_rectangle_gate(
        "MoveMe",
        x_channel,
        y_channel,
        (float(x0), float(x1)),
        (float(y0), float(y1)),
        window.session.get_transform_id(x_channel),
        window.session.get_transform_id(y_channel),
    )
    path = window.session.add_gate(gate, ("root",))
    window._run_gating(record)
    window.plot_grid.refresh_gates()
    old_bounds = gate_bounds(gate)

    vb = panel.plot_item.vb

    def point(x, y):
        scene_pos = vb.mapToScene(vb.mapFromView(QtCore.QPointF(x, y)))
        return panel.plot_widget.mapFromScene(scene_pos)

    center = point((x0 + x1) / 2, (y0 + y1) / 2)
    shifted = point((x0 + x1) / 2 + (x1 - x0) * 0.15, (y0 + y1) / 2)
    viewport = panel.plot_widget.viewport()
    QtTest.QTest.mousePress(
        viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, center
    )
    QtTest.QTest.mouseMove(viewport, shifted, 20)
    QtTest.QTest.mouseRelease(
        viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, shifted
    )

    new_bounds = gate_bounds(gate)
    assert new_bounds[0] > old_bounds[0]
    assert window.session.gate_full_paths() == [path]
    window._set_dirty(False)


@pytest.mark.skipif(not FCS_PATH.exists(), reason="FlowKit example data not available")
def test_population_view_filters_and_rescales(qtbot):
    import numpy as np

    from cytodesk.core.gates import make_rectangle_gate

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    record = load_sample_file(FCS_PATH)
    window.session.add_record(record)
    window.activate_sample(record.id)
    panel = window.plot_grid.active()
    x_channel, y_channel = panel.x_channel, panel.y_channel
    matrix, _ = window.session.transformed_matrix(record, [x_channel, y_channel])
    x0, x1 = np.percentile(matrix[x_channel], [20, 80])
    y0, y1 = np.percentile(matrix[y_channel], [20, 80])
    parent = make_rectangle_gate(
        "Parent",
        x_channel,
        y_channel,
        (float(x0), float(x1)),
        (float(y0), float(y1)),
        window.session.get_transform_id(x_channel),
        window.session.get_transform_id(y_channel),
    )
    parent_path = window.session.add_gate(parent, ("root",))
    window._run_gating(record)
    window.gate_tree.refresh()
    window.plot_grid.refresh_gates()
    window._refresh_stats()

    root = window.gate_tree.tree.topLevelItem(0)
    assert root.text(0) == "Root"
    assert root.childCount() == 1
    sample_item = root.child(0)
    assert sample_item.text(0) == record.id

    membership = window.session.gate_membership(record, parent_path)
    window.open_population(parent_path)
    assert window.current_population_path == parent_path
    assert len(panel._x) == int(membership.sum())
    assert len(panel._x) < record.event_count
    assert panel._population_path == parent_path
    assert panel._gate_items == {}

    window.open_population(None)
    assert window.current_population_path is None
    assert len(panel._x) == record.event_count
    assert ("root", "Parent") in panel._gate_items
    window._set_dirty(False)


@pytest.mark.skipif(not FCS_PATH.exists(), reason="FlowKit example data not available")
def test_switch_to_density_clears_gate_highlight(qtbot):
    import numpy as np

    from cytodesk.core.gates import make_rectangle_gate

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    record = load_sample_file(FCS_PATH)
    window.session.add_record(record)
    window.activate_sample(record.id)
    panel = window.plot_grid.active()
    x_channel, y_channel = panel.x_channel, panel.y_channel
    matrix, _ = window.session.transformed_matrix(record, [x_channel, y_channel])
    x0, x1 = np.percentile(matrix[x_channel], [30, 70])
    y0, y1 = np.percentile(matrix[y_channel], [30, 70])
    gate = make_rectangle_gate(
        "G1",
        x_channel,
        y_channel,
        (float(x0), float(x1)),
        (float(y0), float(y1)),
        window.session.get_transform_id(x_channel),
        window.session.get_transform_id(y_channel),
    )
    path = window.session.add_gate(gate, ("root",))
    window._run_gating(record)
    window.plot_grid.refresh_gates()
    window.select_gate(path)
    assert len(panel._highlight.data) > 0
    window.set_mode("density")
    assert len(panel._highlight.data) == 0
    window.select_gate(path)
    assert len(panel._highlight.data) == 0  # density view stays clean
    window._set_dirty(False)


@pytest.mark.skipif(not FCS_PATH.exists(), reason="FlowKit example data not available")
def test_channel_combo_selection_keeps_popup_stable(qtbot):
    from PySide6 import QtCore, QtTest

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    record = load_sample_file(FCS_PATH)
    window.session.add_record(record)
    window.activate_sample(record.id)
    panel = window.plot_grid.active()
    combo = window.inspector.x_combo
    signature = list(window.inspector._channel_signature)
    item_count = combo.count()

    combo.showPopup()
    view = combo.view()
    item_rect = view.visualRect(combo.model().index(1, 0))
    QtTest.QTest.mouseClick(
        view.viewport(),
        QtCore.Qt.LeftButton,
        QtCore.Qt.NoModifier,
        item_rect.center(),
    )
    qtbot.wait(50)
    assert combo.currentIndex() == 1
    assert panel.x_channel == combo.itemData(1)
    assert combo.count() == item_count
    assert window.inspector._channel_signature == signature
    assert not view.isVisible()

    # A subsequent selection must update the axis and must not rebuild the
    # combo model (which used to leave the popup in a stale state).
    combo.setCurrentIndex(0)
    combo.activated.emit(0)
    qtbot.wait(50)
    assert panel.x_channel == combo.itemData(0)
    assert combo.count() == item_count
    assert window.inspector._channel_signature == signature

    mode_combo = window.inspector.mode_combo
    mode_combo.showPopup()
    mode_view = mode_combo.view()
    mode_rect = mode_view.visualRect(mode_combo.model().index(1, 0))
    QtTest.QTest.mouseClick(
        mode_view.viewport(),
        QtCore.Qt.LeftButton,
        QtCore.Qt.NoModifier,
        mode_rect.center(),
    )
    qtbot.wait(50)
    assert panel.mode == "density"
    assert not mode_view.isVisible()

    layout_combo = window.inspector.layout_combo
    layout_combo.showPopup()
    layout_view = layout_combo.view()
    layout_rect = layout_view.visualRect(layout_combo.model().index(1, 0))
    QtTest.QTest.mouseClick(
        layout_view.viewport(),
        QtCore.Qt.LeftButton,
        QtCore.Qt.NoModifier,
        layout_rect.center(),
    )
    qtbot.wait(50)
    assert window.plot_grid.layout_count == 4
    assert not layout_view.isVisible()
    window._set_dirty(False)


@pytest.mark.skipif(not FCS_PATH.exists(), reason="FlowKit example data not available")
def test_right_click_gate_opens_menu_signal(qtbot, monkeypatch):
    import numpy as np
    from PySide6 import QtCore, QtTest

    from cytodesk.core.gates import make_rectangle_gate

    menus: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        MainWindow,
        "show_gate_menu",
        lambda self, path, pos: menus.append(tuple(path)),
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    record = load_sample_file(FCS_PATH)
    window.session.add_record(record)
    window.activate_sample(record.id)
    panel = window.plot_grid.active()
    x_channel, y_channel = panel.x_channel, panel.y_channel
    matrix, _ = window.session.transformed_matrix(record, [x_channel, y_channel])
    x0, x1 = np.percentile(matrix[x_channel], [30, 70])
    y0, y1 = np.percentile(matrix[y_channel], [30, 70])
    gate = make_rectangle_gate(
        "MenuGate",
        x_channel,
        y_channel,
        (float(x0), float(x1)),
        (float(y0), float(y1)),
        window.session.get_transform_id(x_channel),
        window.session.get_transform_id(y_channel),
    )
    path = window.session.add_gate(gate, ("root",))
    window._run_gating(record)
    window.plot_grid.refresh_gates()

    vb = panel.plot_item.vb
    scene_pos = vb.mapToScene(
        vb.mapFromView(QtCore.QPointF((x0 + x1) / 2, (y0 + y1) / 2))
    )
    viewport_pos = panel.plot_widget.mapFromScene(scene_pos)
    QtTest.QTest.mouseClick(
        panel.plot_widget.viewport(),
        QtCore.Qt.RightButton,
        QtCore.Qt.NoModifier,
        viewport_pos,
    )
    assert menus == [path]
    window._set_dirty(False)


@pytest.mark.skipif(not COMP_FCS_PATH.exists(), reason="FlowKit example data not available")
def test_density_image_orientation(qtbot):
    import numpy as np

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    record = load_sample_file(COMP_FCS_PATH)
    window.session.add_record(record)
    window.activate_sample(record.id)
    panel = window.plot_grid.active()
    panel.set_channels("FSC-A", "CD4 PE-Cy7 FLR-A")
    window.set_mode("density")
    image = panel._density.image
    assert image is not None

    hist, _, _ = np.histogram2d(
        panel._x,
        panel._y,
        bins=200,
        range=[list(panel._x_bounds), list(panel._y_bounds)],
    )
    # ImageItem uses col-major order: image[x, y].
    assert image.shape == (200, 200)
    image_x_mode = int(np.argmax(image.sum(axis=1)))
    image_y_mode = int(np.argmax(image.sum(axis=0)))
    data_x_mode = int(np.argmax(hist.sum(axis=1)))
    data_y_mode = int(np.argmax(hist.sum(axis=0)))
    assert abs(image_x_mode - data_x_mode) <= 4
    assert abs(image_y_mode - data_y_mode) <= 4
    window._set_dirty(False)

