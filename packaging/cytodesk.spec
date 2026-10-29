# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for CytoDesk.

Build from the repository root:

    uv sync --group build
    uv run pyinstaller packaging/cytodesk.spec --noconfirm --clean

PyInstaller cannot cross-compile.  Build Windows packages on Windows and
macOS packages on macOS (the GitHub Actions workflow does this).
"""

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


def _project_root() -> Path:
    spec_path = Path(SPECPATH).resolve()
    if spec_path.is_file():
        spec_path = spec_path.parent
    return spec_path.parent


ROOT = _project_root()
SRC = ROOT / "src"
ICON_ICO = ROOT / "packaging" / "cytodesk.ico"
ICON_ICNS = ROOT / "packaging" / "cytodesk.icns"

datas = []
datas += collect_data_files("flowkit")

hiddenimports = []
hiddenimports += collect_submodules("flowkit")
hiddenimports += collect_submodules("flowutils")
hiddenimports += collect_submodules("pyqtgraph.exporters")
hiddenimports += [
    "bokeh.layouts",
    "bokeh.models",
    "contourpy",
    "PySide6.QtSvg",
]

excludes = [
    "tkinter",
    "matplotlib",
    "IPython",
    "jupyter",
    "jupyter_core",
    "nbformat",
    "notebook",
    "pyarrow",
    "pyarrow.lib",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "shiboken2",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetworkAuth",
    "PySide6.QtNfc",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialBus",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtStateMachine",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

icon = None
if ICON_ICO.exists():
    icon = str(ICON_ICO)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CytoDesk",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CytoDesk",
)

if sys.platform == "darwin":  # noqa: F821 - provided by PyInstaller
    app_icon = str(ICON_ICNS) if ICON_ICNS.exists() else None
    app = BUNDLE(
        coll,
        name="CytoDesk.app",
        icon=app_icon,
        bundle_identifier="io.cytodesk.app",
        info_plist={
            "CFBundleName": "CytoDesk",
            "CFBundleDisplayName": "CytoDesk",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "0.1.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "NSPrincipalClass": "NSApplication",
        },
    )
