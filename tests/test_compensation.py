from pathlib import Path

import numpy as np
import pytest

from cytodesk.core.compensation import (
    align_matrix_to_detectors,
    compensation_info,
    parse_matrix_text,
    read_matrix_file,
)
from cytodesk.core.project import load_project, save_project
from cytodesk.core.session import AnalysisSession, load_sample_file


LEARN_DIR = Path(__file__).resolve().parents[2]
FCS_DIR = LEARN_DIR / "FlowKit-master" / "data" / "8_color_data_set" / "fcs_files"
COMP_FCS_PATH = FCS_DIR / "101_DEN084Y5_15_E01_008_clean.fcs"


pytestmark = pytest.mark.skipif(
    not COMP_FCS_PATH.exists(), reason="FlowKit example data not available"
)


@pytest.fixture()
def record():
    return load_sample_file(COMP_FCS_PATH)


def test_embedded_compensation_state(record):
    info = compensation_info(record)
    assert record.compensation_source == "fcs"
    assert record.compensation_error is None
    assert info is not None
    assert info.matrix.shape == (len(info.detectors), len(info.detectors))
    np.testing.assert_allclose(np.diag(info.matrix), 1.0)
    raw = record.sample.get_events(source="raw")
    comp = record.sample.get_events(source="comp")
    assert raw.shape == comp.shape
    assert not np.allclose(raw, comp)


def test_apply_and_clear_custom_matrix(record):
    session = AnalysisSession()
    rec = session.add_record(record)
    info = compensation_info(rec)
    matrix = info.matrix.copy()
    matrix[0, 1] = 0.25
    session.apply_compensation(rec, info.detectors, matrix)
    assert rec.compensation_source == "custom"
    custom = compensation_info(rec)
    assert custom.matrix[0, 1] == pytest.approx(0.25)

    session.clear_compensation(rec)
    assert rec.compensation_source is None
    assert rec.sample.compensation is None
    assert compensation_info(rec) is None


def test_matrix_text_parsing_and_alignment():
    labels, matrix = parse_matrix_text("A,B\n1,0.1\n0.2,1\n")
    assert labels == ["A", "B"]
    np.testing.assert_allclose(matrix, [[1.0, 0.1], [0.2, 1.0]])

    aligned = align_matrix_to_detectors(labels, matrix, ["B", "A", "C"])
    assert aligned.shape == (3, 3)
    assert aligned[0, 0] == 1.0
    assert aligned[0, 1] == pytest.approx(0.2)
    assert aligned[1, 0] == pytest.approx(0.1)
    assert aligned[1, 1] == 1.0
    assert aligned[2, 2] == 1.0
    assert aligned[0, 2] == 0.0

    fcs_labels, fcs_matrix = parse_matrix_text("2,A,B,1,0.05,0.1,1")
    assert fcs_labels == ["A", "B"]
    np.testing.assert_allclose(fcs_matrix, [[1.0, 0.05], [0.1, 1.0]])

    by_alias = align_matrix_to_detectors(
        ["CD4", "CD8"],
        [[1.0, 0.4], [0.3, 1.0]],
        ["A", "B"],
        aliases={"A": ["CD4"], "B": ["CD8"]},
    )
    assert by_alias[0, 1] == pytest.approx(0.4)

    with pytest.raises(ValueError):
        align_matrix_to_detectors(["X", "Y"], np.eye(2), ["A", "B"])


def test_matrix_file_round_trip(tmp_path):
    path = tmp_path / "matrix.csv"
    path.write_text(",A,B\nA,1,0.15\nB,0.05,1\n", encoding="utf-8")
    labels, matrix = read_matrix_file(path)
    assert labels == ["A", "B"]
    np.testing.assert_allclose(matrix, [[1.0, 0.15], [0.05, 1.0]])


def test_project_round_trip_custom_compensation(record, tmp_path):
    session = AnalysisSession()
    rec = session.add_record(record)
    info = compensation_info(rec)
    matrix = info.matrix.copy()
    matrix[0, 1] = 0.31
    session.apply_compensation(rec, info.detectors, matrix)

    path = save_project(tmp_path / "comp.cytodesk", session, active_sample_id=rec.id)
    loaded = load_project(path)
    new_rec = loaded.session.get_sample(rec.id)
    assert new_rec is not None
    assert new_rec.compensation_source == "custom"
    new_info = compensation_info(new_rec)
    assert new_info.matrix[0, 1] == pytest.approx(0.31)
    assert not loaded.errors
