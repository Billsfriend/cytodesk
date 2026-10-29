from pathlib import Path

import numpy as np
import pytest

from cytodesk.core.gates import (
    gate_bounds,
    gate_handle_points,
    make_ellipse_gate,
    make_polygon_gate,
    make_range_gate,
    make_rectangle_gate,
    set_gate_vertex,
    translate_gate,
)
from cytodesk.core.project import load_project, save_project
from cytodesk.core.session import AnalysisSession, load_sample_file


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


@pytest.fixture(scope="module")
def record():
    if not FCS_PATH.exists():
        pytest.skip("FlowKit example data not available")
    return load_sample_file(FCS_PATH)


@pytest.fixture()
def session(record):
    sess = AnalysisSession()
    rec = sess.add_record(record)
    return sess, rec


def _two_channels(rec):
    channels = [c.pnn for c in rec.transform_candidates()]
    assert len(channels) >= 2
    return channels[0], channels[1]


def test_load_sample(record):
    assert record.event_count > 0
    assert record.channels
    assert record.sample.pnn_labels


@pytest.mark.skipif(not COMP_FCS_PATH.exists(), reason="FlowKit example data not available")
def test_auto_compensation():
    record = load_sample_file(COMP_FCS_PATH)
    assert record.compensation_error is None
    assert record.sample.compensation is not None
    assert record.sample.get_events(source="comp").shape[0] == record.event_count


@pytest.mark.skipif(not COMP_FCS_PATH.exists(), reason="FlowKit example data not available")
def test_compensated_gate_uses_fcs_matrix():
    record = load_sample_file(COMP_FCS_PATH)
    sess = AnalysisSession()
    rec = sess.add_record(record)
    x_channel, y_channel = _two_channels(rec)
    matrix, _ = sess.transformed_matrix(rec, [x_channel, y_channel])
    x0, x1 = np.percentile(matrix[x_channel], [40, 60])
    y0, y1 = np.percentile(matrix[y_channel], [40, 60])
    gate = make_rectangle_gate(
        "CompGate",
        x_channel,
        y_channel,
        (float(x0), float(x1)),
        (float(y0), float(y1)),
        sess.get_transform_id(x_channel),
        sess.get_transform_id(y_channel),
    )
    assert gate.dimensions[0].compensation_ref == "FCS"
    sess.add_gate(gate, ("root",))
    results = sess.gate_sample(rec)
    count = results.get_gate_count("CompGate", ("root",))
    assert count is not None and count > 0
    stats = sess.population_stats(rec, [x_channel, y_channel])
    assert stats and stats[0].count == count


def test_transforms_round_trip(session):
    sess, rec = session
    x_channel, y_channel = _two_channels(rec)
    values = np.array([-50.0, 0.0, 10.0, 1000.0, 100000.0])
    for channel in (x_channel, y_channel):
        spec = sess.get_transform(channel)
        back = spec.inverse(spec.apply(values))
        np.testing.assert_allclose(back, values, rtol=1e-5, atol=1e-3)


def test_rectangle_gate_and_stats(session):
    sess, rec = session
    x_channel, y_channel = _two_channels(rec)
    matrix, _ = sess.transformed_matrix(rec, [x_channel, y_channel])
    x0, x1 = np.percentile(matrix[x_channel], [30, 70])
    y0, y1 = np.percentile(matrix[y_channel], [30, 70])
    gate = make_rectangle_gate(
        "G1",
        x_channel,
        y_channel,
        (float(x0), float(x1)),
        (float(y0), float(y1)),
        sess.get_transform_id(x_channel),
        sess.get_transform_id(y_channel),
    )
    path = sess.add_gate(gate, ("root",))
    assert path == ("root", "G1")
    results = sess.gate_sample(rec)
    count = results.get_gate_count("G1", ("root",))
    assert count is not None and 0 < count < rec.event_count
    assert len(results.get_gate_membership("G1", ("root",))) == rec.event_count

    stats = sess.population_stats(rec, [x_channel, y_channel])
    assert len(stats) == 1
    assert stats[0].count == count
    assert x_channel in stats[0].medians
    assert y_channel in stats[0].medians


def test_nested_gate(session):
    sess, rec = session
    x_channel, y_channel = _two_channels(rec)
    matrix, _ = sess.transformed_matrix(rec, [x_channel, y_channel])
    x0, x1 = np.percentile(matrix[x_channel], [20, 80])
    y0, y1 = np.percentile(matrix[y_channel], [20, 80])
    parent = make_rectangle_gate(
        "Parent",
        x_channel,
        y_channel,
        (float(x0), float(x1)),
        (float(y0), float(y1)),
        sess.get_transform_id(x_channel),
        sess.get_transform_id(y_channel),
    )
    sess.add_gate(parent, ("root",))
    child = make_polygon_gate(
        "Child",
        x_channel,
        y_channel,
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        sess.get_transform_id(x_channel),
        sess.get_transform_id(y_channel),
    )
    sess.add_gate(child, ("root", "Parent"))
    results = sess.gate_sample(rec)
    parent_count = results.get_gate_count("Parent", ("root",))
    child_count = results.get_gate_count("Child", ("root", "Parent"))
    assert parent_count is not None
    assert child_count is not None
    assert child_count <= parent_count
    assert sess.rename_gate(("root", "Parent"), "P2") == ("root", "P2")
    assert sess.gate_by_path(("root", "P2")) is not None
    sess.remove_gate(("root", "P2"))
    assert not sess.gate_full_paths()


def test_gate_geometry_helpers():
    gate = make_ellipse_gate(
        "E", "X", "Y", (0.5, 0.5), 0.2, 0.1, "xf", "yf"
    )
    x0, x1, y0, y1 = gate_bounds(gate)
    np.testing.assert_allclose([x0, x1, y0, y1], [0.3, 0.7, 0.4, 0.6], atol=1e-9)
    handles = gate_handle_points(gate)
    assert len(handles) == 5
    # Move the center.
    set_gate_vertex(gate, 0, 0.6, 0.6)
    x0, x1, y0, y1 = gate_bounds(gate)
    np.testing.assert_allclose([x0, x1, y0, y1], [0.4, 0.8, 0.5, 0.7], atol=1e-9)
    translate_gate(gate, -0.1, 0.2)
    x0, x1, y0, y1 = gate_bounds(gate)
    np.testing.assert_allclose([x0, x1, y0, y1], [0.3, 0.7, 0.7, 0.9], atol=1e-9)

    rect = make_rectangle_gate(
        "R", "X", "Y", (0.1, 0.4), (0.2, 0.5), "xf", "yf"
    )
    translate_gate(rect, 0.2, -0.1)
    np.testing.assert_allclose(gate_bounds(rect), (0.3, 0.6, 0.1, 0.4))
    range_gate = make_range_gate("RG", "X", (0.1, 0.3), "xf")
    translate_gate(range_gate, 0.2, 0.0)
    assert range_gate.dimensions[0].min == pytest.approx(0.3)
    assert range_gate.dimensions[0].max == pytest.approx(0.5)


def test_project_round_trip(session, tmp_path):
    sess, rec = session
    x_channel, y_channel = _two_channels(rec)
    matrix, _ = sess.transformed_matrix(rec, [x_channel, y_channel])
    x0, x1 = np.percentile(matrix[x_channel], [35, 65])
    y0, y1 = np.percentile(matrix[y_channel], [35, 65])
    gate = make_rectangle_gate(
        "SavedGate",
        x_channel,
        y_channel,
        (float(x0), float(x1)),
        (float(y0), float(y1)),
        sess.get_transform_id(x_channel),
        sess.get_transform_id(y_channel),
    )
    sess.add_gate(gate, ("root",))
    results = sess.gate_sample(rec)
    expected = results.get_gate_count("SavedGate", ("root",))

    project_path = save_project(
        tmp_path / "test.cytodesk",
        sess,
        active_sample_id=rec.id,
        layout={"layout": 1, "mode": "scatter"},
    )
    loaded = load_project(project_path)
    assert loaded.active_sample_id == rec.id
    assert len(loaded.session.samples) == 1
    assert loaded.session.gate_full_paths() == [("root", "SavedGate")]
    new_rec = loaded.session.get_sample(rec.id)
    assert new_rec is not None
    new_results = loaded.session.results_for(new_rec)
    assert new_results is not None
    assert new_results.get_gate_count("SavedGate", ("root",)) == expected
    assert loaded.session.get_transform(x_channel).kind == sess.get_transform(x_channel).kind
