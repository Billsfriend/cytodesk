# AGENTS.md

## Commands

- `uv sync` then `uv run cytodesk [file.fcs]`; `uv run cytodesk --version` works headless.
- `uv run pytest` is the verification gate. No lint/typecheck config exists.
- Focused test: `uv run pytest tests/test_ui.py::test_name -q`.
- Tests need the FlowKit sample data at `../FlowKit-master/data`. If absent:
  `git clone --depth 1 https://github.com/whitews/FlowKit.git ../FlowKit-master`.
  Running/packaging the app does **not** need that checkout; FlowKit comes from PyPI.
- `tests/conftest.py` forces `QT_QPA_PLATFORM=offscreen`; keep it.
- Build: `uv sync --group build` then
  `uv run pyinstaller packaging/cytodesk.spec --noconfirm --clean`.
  PyInstaller cannot cross-compile.

## Architecture

- `core/session.py::AnalysisSession` is the UI-independent source of truth for samples,
  transforms, compensation, gates, gating results and batch gating. UI code should go
  through it instead of calling FlowKit directly.
- UI wiring: `ui/main_window.py` orchestrates; `ui/gate_tree.py` is the combined
  Root → sample → gates tree (there is no `sample_panel.py` anymore);
  `plotting/plot_grid.py` owns the 1×1/2×2 panels; `ui/compensation_dialog.py` edits
  spillover matrices.
- Gate geometry is stored in **transformed channel coordinates**; gate dimensions use
  PnN channel labels. Event flow is raw → compensation → per-channel transform → gate.
  `load_sample_file()` auto-applies FCS `$SPILLOVER` unless disabled.
- `.cytodesk` is a zip containing `project.json`, not a GatingML file.
- README UI details can lag behind code; verify behavior in code.

## Gating / UI invariants

- Gate names are **globally unique**. `AnalysisSession.add_gate()` and `rename_gate()`
  auto-suffix `_2`, `_3`; the UI warns when a manual name is changed. Do not restore
  path-local uniqueness checks.
- Opening a population filters to its events, rescales, and displays only its direct
  child gates. A gate created on `FSC-A / SSC-A` opens as `FSC-A / FSC-H` when
  `FSC-H` exists (singlet workflow). **Selecting** a gate in the tree instead switches
  to that gate's definition view (its parent population, X/Y channels and mode);
  do not route this through the singlet default.
- There is no select tool. Default is polygon (range in histogram mode). Dragging inside
  an existing gate moves it; `Shift`+drag forces drawing a new overlapping gate.
- Quadrant gates use FlowKit `QuadrantGate`/`Quadrant`/`QuadrantDivider`. Quadrant
  children are populations, not gates: only quadrants may be parents for child gates.
  `AnalysisSession.gate_by_path()` returns the owning QuadrantGate for a quadrant path;
  use `gate_node_by_path()`/`gate_owner_path()` to distinguish. The UI blocks opening
  the parent QuadrantGate as a population.
- Gate labels display `% parent`, not event counts.
- Fluorescence (`logicle`/`log`/`hyperlog`/`asinh`) axes use fixed decade ticks
  (10¹, 10², …) via `TransformedAxis.tickValues`; do not revert to evenly spaced
  transformed values. Root views with multiple samples pool the numeric range of all
  samples that contain the selected channels; population views use only population events.
- Scatter and density share the same ≤300k display-event subset. Density
  `ImageItem` must stay `axisOrder="col-major"` and receive the histogram **without**
  transposing (`hist`, not `hist.T`); contours must use the same data.
- Density mode does not draw selected-gate point highlights; switching modes clears them.
- Sample overlay is a single normal-colored combined series with no legend; title is
  exactly `叠加样本`. Do not reintroduce per-sample colors or legend labels.
- Compensation preview is a lower-triangular grid in numeric-matrix order. On a cell
  edit, recompute once and update only channels whose transformed values actually
  changed; do not replace it with the old single X/Y preview.
- QComboBox selection: never clear/repopulate that combo inside its own selection
  handler. X/Y/mode/layout use `activated`, with heavy updates deferred via `QTimer`.
  This fixed stuck popups and click-through.

## Packaging / release

- CI matrix: Windows x64, macOS arm64 (`macos-latest`), Ubuntu 22.04 x64. macOS Intel
  is intentionally not built.
- Workflow triggers only on tags `0.*` / `v*` and manual dispatch; pushing `main` does
  not build.
- Actions are pinned to Node 24 versions (`checkout@v7`, `upload-artifact@v7`,
  `download-artifact@v8`, `setup-uv@v10.3.3`, `action-gh-release@v3`); do not downgrade.
- Bump `pyproject.toml`, `src/cytodesk/__init__.py`, `uv.lock`, and README before
  creating a release tag.
- Create annotated tags whose message is a short user-facing summary vs the previous
  tag. The release job uses the tag annotation as the GitHub Release body; it does not
  use `generate_release_notes`.
- Keep FlowKit as a PyPI dependency; do not reintroduce `[tool.uv.sources]` path/editable
  configuration.
- Packages are unsigned: Windows SmartScreen and macOS Gatekeeper warnings are expected,
  not build failures.

## Environment gotcha

- This machine's global uv config uses a Tsinghua mirror, so plain `uv sync`/`uv run`
  can rewrite `uv.lock` URLs. Keep the committed lock on `https://pypi.org/simple`;
  use `UV_DEFAULT_INDEX=https://pypi.org/simple uv ...` for lock/install commands and
  do not commit mirror-only lock churn.
