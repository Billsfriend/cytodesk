"""Application entry point and global styling."""

from __future__ import annotations

import sys
import traceback

import pyqtgraph as pg
from PySide6 import QtGui, QtWidgets

from . import __version__
from .ui.main_window import MainWindow


STYLESHEET = """
QMainWindow, QWidget {
    background: #f5f6f8;
    color: #20242a;
}
QMenuBar {
    background: #ffffff;
    border-bottom: 1px solid #e2e5ea;
}
QMenuBar::item {
    padding: 5px 10px;
    background: transparent;
}
QMenuBar::item:selected {
    background: #eaf1fd;
    border-radius: 4px;
}
QMenu {
    background: #ffffff;
    border: 1px solid #dde1e6;
    padding: 4px;
}
QMenu::item {
    padding: 5px 24px 5px 12px;
    border-radius: 4px;
}
QMenu::item:selected {
    background: #eaf1fd;
}
QToolBar {
    background: #ffffff;
    border: none;
    border-bottom: 1px solid #e2e5ea;
    padding: 3px 6px;
    spacing: 2px;
}
QToolBar QLabel {
    color: #5c6570;
    font-size: 9pt;
}
QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 3px 7px;
    color: #2b3038;
}
QToolButton:hover {
    background: #edf3fd;
}
QToolButton:checked {
    background: #d8e7ff;
    border-color: #b7d0f7;
    color: #123f8c;
}
QGroupBox {
    background: #ffffff;
    border: 1px solid #e1e4e9;
    border-radius: 6px;
    margin-top: 9px;
    padding-top: 4px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #3a414b;
}
QListWidget, QTreeWidget, QTableWidget, QTableView, QScrollArea, QLineEdit,
QComboBox, QDoubleSpinBox {
    background: #ffffff;
    border: 1px solid #dfe3e8;
    border-radius: 4px;
    selection-background-color: #d8e7ff;
    selection-color: #123f8c;
}
QListWidget::item {
    padding: 5px 4px;
}
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {
    background: #d8e7ff;
    color: #123f8c;
}
QLineEdit, QComboBox, QDoubleSpinBox {
    padding: 3px 5px;
    min-height: 20px;
}
QComboBox::drop-down {
    border: none;
    width: 18px;
}
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #d5dbe3;
    outline: 0;
    selection-background-color: #d8e7ff;
    selection-color: #123f8c;
}
QPushButton {
    background: #f7f9fc;
    border: 1px solid #d5dbe3;
    border-radius: 5px;
    padding: 4px 10px;
}
QPushButton:hover {
    background: #edf3fd;
    border-color: #b7d0f7;
}
QPushButton:pressed {
    background: #d8e7ff;
}
QHeaderView::section {
    background: #f1f3f6;
    border: none;
    border-right: 1px solid #e4e7ec;
    border-bottom: 1px solid #e4e7ec;
    padding: 4px 6px;
    font-weight: 600;
    color: #4a525d;
}
QStatusBar {
    background: #ffffff;
    border-top: 1px solid #e2e5ea;
    color: #5c6570;
}
QStatusBar QLabel {
    color: #5c6570;
    padding: 0 6px;
}
QSplitter::handle {
    background: #e6e9ee;
}
QSplitter::handle:horizontal {
    width: 3px;
}
QSplitter::handle:vertical {
    height: 3px;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #c8ced7;
    min-height: 24px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #aab3bf;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #c8ced7;
    min-width: 24px;
    border-radius: 5px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
"""


def _install_excepthook() -> None:
    def hook(exc_type, exc_value, exc_tb):
        traceback.print_exception(exc_type, exc_value, exc_tb)
        message = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        if QtWidgets.QApplication.instance() is not None:
            dialog = QtWidgets.QMessageBox(
                QtWidgets.QMessageBox.Critical,
                "CytoDesk 发生错误",
                f"{exc_value}\n\n详细信息已输出到终端。",
            )
            dialog.setDetailedText(message)
            dialog.exec()

    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--version" in argv[1:]:
        print(f"CytoDesk {__version__}")
        return 0
    if any(arg in {"-h", "--help"} for arg in argv[1:]):
        print("用法: cytodesk [FCS 文件 …]")
        print("      cytodesk --version")
        return 0
    app = QtWidgets.QApplication(argv)
    app.setApplicationName("CytoDesk")
    app.setOrganizationName("CytoDesk")
    app.setApplicationDisplayName("CytoDesk")

    app.setStyle("Fusion")
    families = set(QtGui.QFontDatabase.families())
    font_family = next(
        (
            name
            for name in (
                "Noto Sans CJK SC",
                "Source Han Sans VF",
                "Source Han Sans SC",
                "WenQuanYi Micro Hei",
                "Microsoft YaHei",
                "PingFang SC",
                "Droid Sans Fallback",
            )
            if name in families
        ),
        "Sans Serif",
    )
    app.setFont(QtGui.QFont(font_family, 10))
    app.setStyleSheet(STYLESHEET)

    pg.setConfigOptions(antialias=False, background="w", foreground="k")
    _install_excepthook()

    window = MainWindow()
    window.show()

    paths = [path for path in argv[1:] if not path.startswith("-")]
    if paths:
        window.load_paths(paths)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
