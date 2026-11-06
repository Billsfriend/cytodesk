"""Project file support for CytoDesk.

A ``.cytodesk`` file is a zip archive containing a single ``project.json``
document.  Keeping the file as a zip lets us add thumbnails or an exported
GatingML document later without changing the extension.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import tempfile
import zipfile

from .session import AnalysisSession, load_sample_file
from .transforms import TransformSpec


PROJECT_FORMAT = "cytodesk-project"
PROJECT_VERSION = 2
PROJECT_JSON_NAME = "project.json"


@dataclass
class LoadedProject:
    session: AnalysisSession
    active_sample_id: str | None = None
    layout: dict = field(default_factory=dict)
    missing_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    path: Path | None = None


def save_project(
    path: str | Path,
    session: AnalysisSession,
    active_sample_id: str | None = None,
    layout: dict | None = None,
) -> Path:
    path = Path(path)
    if path.suffix.lower() != ".cytodesk":
        path = path.with_suffix(".cytodesk")
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "format": PROJECT_FORMAT,
        "version": PROJECT_VERSION,
        "samples": session.sample_dicts(),
        "active_sample": active_sample_id,
        "transforms": session.transform_dicts(),
        "gates": session.gate_tree_dicts(),
        "layout": layout or {},
    }

    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with open(tmp_fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        # Replace the payload file with a zip archive atomically.
        with zipfile.ZipFile(tmp_name, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(PROJECT_JSON_NAME, payload)
        Path(tmp_name).replace(path)
    finally:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass
    return path


def read_project_payload(path: str | Path) -> dict:
    path = Path(path)
    try:
        with zipfile.ZipFile(path, "r") as zf:
            with zf.open(PROJECT_JSON_NAME) as handle:
                return json.loads(handle.read().decode("utf-8"))
    except zipfile.BadZipFile:
        # Also accept a plain JSON document (useful for debugging/hand editing).
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)


def load_project(path: str | Path) -> LoadedProject:
    path = Path(path)
    data = read_project_payload(path)
    if data.get("format") != PROJECT_FORMAT:
        raise ValueError("不是有效的 CytoDesk 项目文件")

    session = AnalysisSession()
    missing: list[str] = []
    errors: list[str] = []

    for item in data.get("samples", []):
        sample_path = Path(item.get("path", ""))
        sample_id = item.get("id")
        if not sample_path.exists():
            missing.append(str(sample_path))
            continue
        compensation = item.get("compensation")
        auto_compensate = True
        if isinstance(compensation, dict):
            source = str(compensation.get("source") or "fcs")
            auto_compensate = source == "fcs"
        try:
            try:
                record = load_sample_file(
                    sample_path,
                    sample_id=sample_id,
                    auto_compensate=auto_compensate,
                )
            except Exception:
                # Project files may reference slightly non-compliant FCS files.
                record = load_sample_file(
                    sample_path,
                    sample_id=sample_id,
                    repair=True,
                    auto_compensate=auto_compensate,
                )
            session.add_record(record)
            if isinstance(compensation, dict):
                comp_error = session.restore_compensation(record, compensation)
                if comp_error:
                    errors.append(f"{sample_path}: 补偿矩阵恢复失败: {comp_error}")
        except Exception as exc:  # pragma: no cover - depends on external files
            errors.append(f"{sample_path}: {exc}")

    transforms = {}
    for channel, spec_data in (data.get("transforms") or {}).items():
        try:
            transforms[channel] = TransformSpec.from_dict(spec_data)
        except Exception:
            continue
    session.restore_transforms(transforms)

    for gate_data in data.get("gates", []):
        try:
            parent_path = tuple(gate_data.get("parent_path") or ("root",))
            full_path = session.add_gate_from_dict(gate_data, parent_path)
            color = gate_data.get("color")
            if color:
                session.set_gate_color(full_path, color)
        except Exception as exc:
            name = gate_data.get("name", "?")
            errors.append(f"门 {name}: {exc}")

    # Recompute results for loaded samples.
    for record in session.samples.values():
        try:
            session.gate_sample(record)
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"{record.id}: 门控失败: {exc}")

    active = data.get("active_sample")
    if active not in session.samples:
        active = next(iter(session.samples), None)

    return LoadedProject(
        session=session,
        active_sample_id=active,
        layout=data.get("layout") or {},
        missing_files=missing,
        errors=errors,
        path=path,
    )
