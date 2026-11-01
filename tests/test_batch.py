from pathlib import Path

import numpy as np
import pytest

from cytodesk.core.gates import make_rectangle_gate
from cytodesk.core.session import AnalysisSession, load_sample_file


LEARN_DIR = Path(__file__).resolve().parents[2]
FCS_DIR = LEARN_DIR / "FlowKit-master" / "data" / "8_color_data_set" / "fcs_files"
BATCH_FILES = sorted(FCS_DIR.glob("*.fcs")) if FCS_DIR.exists() else []
SMALL_FCS = LEARN_DIR / "FlowKit-master" / "data" / "test_data_2d_01.fcs"


pytestmark = pytest.mark.skipif(
    len(BATCH_FILES) < 2, reason="FlowKit example data not available"
)


def _session_with_samples(paths, auto_compensate=True):
    session = AnalysisSession()
    records = [
        session.add_record(load_sample_file(path, auto_compensate=auto_compensate))
        for path in paths
    ]
    return session, records


def _add_percentile_gate(session, record, name="BatchGate"):
    panel_channels = [c.pnn for c in record.transform_candidates()]
    x_channel = next(
        (c for c in panel_channels if "FSC" in c.upper() and c.upper().endswith("-A")),
        panel_channels[0],
    )
    y_channel = next(
        (c for c in panel_channels if "SSC" in c.upper() and c.upper().endswith("-A")),
        panel_channels[1],
    )
    matrix, _ = session.transformed_matrix(record, [x_channel, y_channel])
    x0, x1 = np.percentile(matrix[x_channel], [25, 75])
    y0, y1 = np.percentile(matrix[y_channel], [25, 75])
    gate = make_rectangle_gate(
        name,
        x_channel,
        y_channel,
        (float(x0), float(x1)),
        (float(y0), float(y1)),
        session.get_transform_id(x_channel),
        session.get_transform_id(y_channel),
    )
    session.add_gate(gate, ("root",))
    return x_channel, y_channel


def test_gate_all_and_batch_stats():
    session, records = _session_with_samples(BATCH_FILES[:2])
    x_channel, y_channel = _add_percentile_gate(session, records[0])
    statuses = session.gate_all()
    assert len(statuses) == 2
    assert all(status.ok for status in statuses)
    assert session.gated_sample_ids() == {record.id for record in records}

    rows = session.batch_population_stats([x_channel, y_channel])
    assert len(rows) == 2
    assert {row.sample_id for row in rows} == {record.id for record in records}
    for row in rows:
        assert row.gate_name == "BatchGate"
        assert row.count > 0
        assert row.percent_total == pytest.approx(row.count / row.sample_total * 100)
        assert x_channel in row.medians
        assert y_channel in row.medians


def test_gate_all_reports_missing_channels():
    session, records = _session_with_samples(BATCH_FILES[:2])
    _add_percentile_gate(session, records[0])
    small = session.add_record(load_sample_file(SMALL_FCS))
    statuses = session.gate_all()
    by_id = {status.sample_id: status for status in statuses}
    assert by_id[records[0].id].ok
    assert by_id[records[1].id].ok
    assert not by_id[small.id].ok
    assert by_id[small.id].missing_channels
    assert session.results_for(small) is None
    assert session.gating_error(small.id)


def test_batch_stats_empty_before_gating():
    session, records = _session_with_samples(BATCH_FILES[:2])
    _add_percentile_gate(session, records[0])
    assert session.batch_population_stats() == []
    session.gate_all()
    assert len(session.batch_population_stats()) == 2


def test_batch_gate_survives_compensation_change():
    session, records = _session_with_samples(BATCH_FILES[:2])
    _add_percentile_gate(session, records[0])
    session.gate_all()
    before = session.batch_population_stats()
    assert before

    from cytodesk.core.compensation import compensation_info

    info = compensation_info(records[0])
    matrix = info.matrix.copy()
    matrix[0, 1] = 0.35
    session.apply_compensation(records[0], info.detectors, matrix)
    assert session.batch_population_stats() == []
    session.gate_all()
    after = session.batch_population_stats()
    assert len(after) == 2
    assert {row.sample_id for row in after} == {record.id for record in records}
