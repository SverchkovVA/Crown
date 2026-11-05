"""Светлая, тёмная и системная темы; настройка только на этом ПК."""

import re
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QComboBox, QToolButton, QWidget

from personal_finance.presentation.window_chrome import apply_caption_theme


class ThemeChoice(StrEnum):
    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"


THEME_LABELS = {
    ThemeChoice.LIGHT: "Светлая",
    ThemeChoice.DARK: "Тёмная",
    ThemeChoice.SYSTEM: "Как в системе",
}


def default_theme_settings() -> QSettings:
    return QSettings("PersonalFinanceLocal", "Appearance")


class ThemePreferences:
    def load(self) -> ThemeChoice:
        try:
            raw = default_theme_settings().value("theme", "light")
            return ThemeChoice(raw) if isinstance(raw, str) else ThemeChoice.LIGHT
        except (ValueError, TypeError):
            return ThemeChoice.LIGHT

    def save(self, choice: ThemeChoice) -> None:
        settings = default_theme_settings()
        settings.setValue("theme", choice.value)
        settings.sync()


BASE_STYLE = """
QMainWindow, QDialog, QWidget {
    background-color: #f5f7f9; color: #172b35;
    font-family: 'Segoe UI', 'DejaVu Sans'; font-size: 14px;
}
QLabel { background: transparent; }
QLabel#heading { font-size: 29px; font-weight: 600; }
QLabel#muted { color: #4e626e; }
QLabel#brand { font-size: 23px; font-weight: 600; color: #12634b; }
QLabel#number { font-size: 28px; font-weight: 600; color: #12634b; }
QLabel#emptyState { background: #e8f0f4; color: #263f4c; padding: 12px;
                   border: 1px solid #a9bdc8; border-radius: 6px; }
QLabel#feedbackError { background: #fff0ed; color: #9c291e; padding: 8px;
                      border: 1px solid #b23a2c; border-radius: 4px; }
QFrame#card { background: white; border: 1px solid #a9bdc8; border-radius: 12px; }
QFrame#sidebar { background: #eaf0ed; border: 0; }
QPushButton { background: white; color: #172b35; border: 1px solid #728b97;
              border-radius: 7px; padding: 9px 14px; }
QPushButton:hover { background: #e2f0e9; border-color: #17694f; }
QPushButton:pressed { background: #c7dfd2; border-color: #104c3a; }
QPushButton:focus { border: 2px solid #155c9b; padding: 8px 13px; }
QPushButton:default { border: 2px solid #17694f; padding: 8px 13px; }
QPushButton:disabled { color: #52636d; background: #e4e9ec; border-color: #a7b5bd; }
QPushButton#primary { background: #17694f; color: white; border: 1px solid #17694f; }
QPushButton#primary:hover { background: #11533e; }
QPushButton#primary:pressed { background: #0b402f; }
QPushButton#primary:focus { border: 2px solid #f5c842; padding: 8px 13px; }
QPushButton#primary:disabled { background: #d5dde2; color: #52636d; border-color: #a7b5bd; }
QListWidget { border: 0; background: transparent; color: #172b35; }
QListWidget::item { padding: 12px; margin: 3px 0; border: 1px solid transparent;
                    border-radius: 7px; }
QListWidget::item:hover { background: #e2f0e9; }
QListWidget::item:selected, QListWidget::item:selected:!active {
    background: #17694f; color: white; border-color: #104c3a;
}
QListWidget::item:focus { border: 2px solid #155c9b; padding: 11px; }
QFrame#sidebar QListWidget::item { padding: 6px 12px; margin: 1px 0; }
QFrame#sidebar QListWidget::item:focus { padding: 5px 11px; }
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QAbstractSpinBox {
    background: white; color: #172b35; border: 1px solid #728b97;
    border-radius: 5px; padding: 7px; selection-background-color: #17694f;
    selection-color: white; placeholder-text-color: #52636d;
}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus,
QAbstractSpinBox:focus { border: 2px solid #155c9b; padding: 6px; }
QLineEdit:disabled, QComboBox:disabled, QPlainTextEdit:disabled, QTextEdit:disabled,
QAbstractSpinBox:disabled { background: #e4e9ec; color: #52636d; border-color: #a7b5bd; }
QComboBox::drop-down { background: #edf3f1; border-left: 1px solid #a9bdc8; width: 24px; }
QComboBox::down-arrow { image: url("@ICONS@/chevron-down.svg"); width: 16px; height: 12px; }
QComboBox::down-arrow:disabled { image: url("@ICONS@/chevron-disabled.svg"); }
QSpinBox::up-arrow { image: url("@ICONS@/chevron-up.svg"); width: 16px; height: 12px; }
QSpinBox::down-arrow { image: url("@ICONS@/chevron-down.svg"); width: 16px; height: 12px; }
QComboBox QAbstractItemView { background: white; color: #172b35;
    selection-background-color: #17694f; selection-color: white; border: 1px solid #728b97; }
QCheckBox, QRadioButton { background: transparent; color: #172b35; spacing: 8px; padding: 3px; }
QCheckBox:disabled, QRadioButton:disabled { color: #52636d; }
QCheckBox:focus, QRadioButton:focus { background: #e3edf7; border-radius: 3px;
    border: 2px solid #155c9b; padding: 1px; }
QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px;
    background: white; border: 2px solid #526b79; }
QCheckBox::indicator { border-radius: 3px; }
QRadioButton::indicator { border-radius: 11px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: #17694f; }
QCheckBox::indicator:checked { background: #17694f; border-color: #17694f;
    image: url("@ICONS@/check.svg"); }
QCheckBox::indicator:indeterminate { background: #17694f; border: 5px solid #526b79; }
QRadioButton::indicator:checked { image: url("@ICONS@/radio.svg"); border-color: #17694f; }
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {
    background: #e4e9ec; border-color: #9aaab4; }
QCheckBox::indicator:checked:disabled { image: url("@ICONS@/check-disabled.svg"); }
QRadioButton::indicator:checked:disabled { image: url("@ICONS@/radio-disabled.svg"); }
QTableView, QTableWidget, QTreeWidget, QTreeView {
    background: white; color: #172b35; alternate-background-color: #edf3f1;
    selection-background-color: #17694f; selection-color: white;
    border: 1px solid #a9bdc8; border-radius: 6px; gridline-color: #d5dfe5;
}
QTableView::item:selected, QTableView::item:selected:!active,
QTreeView::item:selected, QTreeView::item:selected:!active {
    background: #17694f; color: white;
}
QTableView::item:focus, QTreeView::item:focus { border: 2px solid #155c9b; }
QListWidget::item:selected:focus, QTableView::item:selected:focus,
QTreeView::item:selected:focus { border: 2px solid #f5c842; }
QTableCornerButton::section, QHeaderView::section {
    background: #e6eeeb; color: #172b35; border: 0; padding: 10px; font-weight: 600;
}
QScrollArea { background: #f5f7f9; border: 0; }
QScrollArea#operationScroll, QScrollArea#ocrScroll, QWidget#operationBody, QWidget#ocrBody {
    background: #f5f7f9;
}
QScrollBar:vertical { background: #e1e8ed; width: 14px; margin: 1px; }
QScrollBar:horizontal { background: #e1e8ed; height: 14px; margin: 1px; }
QScrollBar::handle:vertical { background: #607d8b; min-height: 28px; border-radius: 5px; }
QScrollBar::handle:horizontal { background: #607d8b; min-width: 28px; border-radius: 5px; }
QScrollBar::handle:hover { background: #305f75; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: #e1e8ed; }
QTabWidget::pane { border: 1px solid #a9bdc8; background: #f5f7f9; }
QTabBar::tab { background: #e6eeeb; color: #172b35; border: 1px solid #a9bdc8;
               padding: 8px 12px; }
QTabBar::tab:selected { background: #17694f; color: white; }
QTabBar::tab:disabled { background: #e4e9ec; color: #52636d; }
QMenuBar, QMenu { background: white; color: #172b35; }
QMenuBar::item:selected, QMenu::item:selected { background: #17694f; color: white; }
QMenu::item:disabled { color: #52636d; }
QToolTip { background: #172b35; color: white; border: 1px solid #526b79; padding: 6px; }
QStatusBar { background: #eaf0ed; color: #172b35; }

QFrame#inlineFooter { border-top: 1px solid #a9bdc8; background: #f5f7f9; }
QScrollArea#inlineContent, QWidget#inlineBody { background: #f5f7f9; border: 0; }
QToolButton { color: #172b35; background: white; border: 1px solid #728b97;
              border-radius: 6px; padding: 7px 10px; }
QToolButton:hover { background: #e2f0e9; border-color: #17694f; }
QToolButton:disabled { color: #52636d; background: #e4e9ec; border-color: #a7b5bd; }
QToolButton:focus { border: 2px solid #155c9b; padding: 6px 9px; }
QFrame#ocrInput { border: 1px solid #a9bdc8; border-radius: 8px; padding: 8px; }
QFrame#ocrDropZone { background: white; border: 2px dashed #728b97; border-radius: 8px; }
QFrame#ocrDropZone[dragActive="true"] { background: #e2f0e9; border-color: #17694f; }
QLabel#dropTitle { font-size: 18px; font-weight: 600; }
"""

DARK_COLORS = {
    "#f5f7f9": "#151d23",
    "#172b35": "#e5edf2",
    "#4e626e": "#b5c6d0",
    "#12634b": "#8eddbb",
    "#e8f0f4": "#23313c",
    "#263f4c": "#deebf2",
    "#a9bdc8": "#546b79",
    "#fff0ed": "#492b2a",
    "#9c291e": "#ffb9ad",
    "#b23a2c": "#ce8278",
    "#eaf0ed": "#1b292e",
    "#728b97": "#68818e",
    "#e2f0e9": "#263f39",
    "#17694f": "#17694f",
    "#c7dfd2": "#315448",
    "#104c3a": "#8eddbb",
    "#155c9b": "#8ac4ff",
    "#52636d": "#abbcc6",
    "#e4e9ec": "#29363e",
    "#a7b5bd": "#5a707d",
    "#d5dde2": "#29363e",
    "#edf3f1": "#202e35",
    "#e3edf7": "#283e56",
    "#526b79": "#8098a6",
    "#9aaab4": "#7a929e",
    "#d5dfe5": "#344a55",
    "#e6eeeb": "#26373e",
    "#e1e8ed": "#22323b",
    "#607d8b": "#6e8998",
    "#305f75": "#9bbacb",
}


def theme_style(dark: bool) -> str:
    value = BASE_STYLE
    if dark:
        value = re.sub(r"#[0-9a-fA-F]{6}", lambda m: DARK_COLORS.get(m[0].lower(), m[0]), value)
        value = re.sub(r"background(?:-color)?: white", "background: #1d2931", value)
    directory = Path(__file__).with_name("icons")
    if dark:
        directory /= "dark"
    return value.replace("@ICONS@", directory.as_posix())


LIGHT_STYLE = theme_style(False)


class ThemeManager(QObject):
    changed = Signal()

    def __init__(self, window: QWidget, preferences: ThemePreferences | None = None) -> None:
        super().__init__(window)
        self.window = window
        self.window.installEventFilter(self)
        self.preferences = preferences or ThemePreferences()
        self.choice = self.preferences.load()
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        app.styleHints().colorSchemeChanged.connect(self.apply)
        self.apply()

    def set_choice(self, value: ThemeChoice) -> None:
        if value != self.choice:
            self.preferences.save(value)
            self.choice = value
            self.apply()
            self.changed.emit()

    def apply(self, *unused: object) -> None:
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        dark = self.choice is ThemeChoice.DARK or (
            self.choice is ThemeChoice.SYSTEM
            and app.styleHints().colorScheme() == Qt.ColorScheme.Dark
        )
        palette = app.style().standardPalette()
        if dark:
            for role, color in (
                (QPalette.ColorRole.Window, "#151d23"),
                (QPalette.ColorRole.Base, "#1d2931"),
                (QPalette.ColorRole.AlternateBase, "#202e35"),
                (QPalette.ColorRole.WindowText, "#e5edf2"),
                (QPalette.ColorRole.Text, "#e5edf2"),
                (QPalette.ColorRole.ButtonText, "#e5edf2"),
                (QPalette.ColorRole.Button, "#1d2931"),
                (QPalette.ColorRole.Highlight, "#17694f"),
                (QPalette.ColorRole.HighlightedText, "#ffffff"),
                (QPalette.ColorRole.PlaceholderText, "#abbcc6"),
                (QPalette.ColorRole.ToolTipBase, "#1d2931"),
                (QPalette.ColorRole.ToolTipText, "#e5edf2"),
            ):
                palette.setColor(role, QColor(color))
            palette.setColor(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#abbcc6")
            )
            palette.setColor(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#abbcc6")
            )
        self.window.setPalette(palette)
        self.window.setStyleSheet(theme_style(dark))
        self.window.setProperty("darkTheme", dark)
        self._apply_caption()
        folder = Path(__file__).with_name("icons")
        if dark:
            folder /= "dark"
        for button in self.window.findChildren(QToolButton):
            direction = button.property("navigationDirection")
            if direction in ("back", "forward"):
                button.setIcon(QIcon(str(folder / ("arrow-" + str(direction) + ".svg"))))

    def _apply_caption(self) -> None:
        if self.window.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
            applied = apply_caption_theme(self.window, dark=bool(self.window.property("darkTheme")))
            self.window.setProperty("nativeCaptionThemed", applied)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.window and event.type() in (QEvent.Type.Show, QEvent.Type.WinIdChange):
            self._apply_caption()
        return super().eventFilter(watched, event)

    def selector(self) -> QComboBox:
        combo = QComboBox()
        combo.setAccessibleName("Тема оформления")
        for choice, title in THEME_LABELS.items():
            combo.addItem(title, choice.value)
        combo.setCurrentIndex(combo.findData(self.choice.value))
        combo.currentIndexChanged.connect(lambda: self.set_choice(ThemeChoice(combo.currentData())))

        def sync() -> None:
            blocked = combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(self.choice.value))
            combo.blockSignals(blocked)

        self.changed.connect(sync)
        return combo
