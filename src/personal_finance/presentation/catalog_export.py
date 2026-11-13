"""Выбор и предпросмотр справочников перед локальным сохранением."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHeaderView,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.catalog_export import (
    FIELDS,
    SECTION_LABELS,
    CatalogExport,
    CatalogSection,
    catalog_export_cells,
)
from personal_finance.domain.exporting import ExportFormat
from personal_finance.presentation.analytics import _label
from personal_finance.presentation.recurring_payments import _table
from personal_finance.presentation.tables import replace_table


class CatalogExportOptions(QDialog):
    previewRequested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Экспорт справочников")
        layout = QVBoxLayout(self)
        self.sections = {section: QCheckBox(label) for section, label in SECTION_LABELS.items()}
        for field in self.sections.values():
            field.setChecked(True)
            layout.addWidget(field)
            field.toggled.connect(self._selection_changed)
        self.names = QCheckBox("Включить названия")
        layout.addWidget(self.names)
        layout.addWidget(_label("Номера для связей присваиваются внутри файла."))
        form = QFormLayout()
        self.format = QComboBox()
        self.format.setAccessibleName("Формат экспорта справочников")
        self.format.addItem("CSV — для таблиц", ExportFormat.CSV.value)
        self.format.addItem("JSON — для обработки данных", ExportFormat.JSON.value)
        form.addRow("Формат", self.format)
        layout.addLayout(form)
        self.preview = QPushButton("Предпросмотр")
        self.preview.setObjectName("primary")
        self.preview.clicked.connect(self.previewRequested)
        layout.addWidget(self.preview)
        layout.addStretch()

    def _selection_changed(self) -> None:
        self.preview.setEnabled(any(field.isChecked() for field in self.sections.values()))

    def selection(self) -> tuple[frozenset[CatalogSection], ExportFormat, bool]:
        return (
            frozenset(section for section, field in self.sections.items() if field.isChecked()),
            ExportFormat(self.format.currentData()),
            self.names.isChecked(),
        )


class CatalogExportPreview(QDialog):
    def __init__(self, snapshot: CatalogExport, format: ExportFormat, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Предпросмотр справочников")
        layout = QVBoxLayout(self)
        count = len(snapshot.rows)
        names = ", ".join(SECTION_LABELS[item] for item in snapshot.sections)
        layout.addWidget(_label(f"{names} · {format.value.upper()} · Записей: {count}"))
        self.table = _table("Данные справочников")
        replace_table(
            self.table, FIELDS, [catalog_export_cells(row) for row in snapshot.rows[:200]]
        )
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        if count > 200:
            layout.addWidget(_label(f"Первые 200 из {count} записей. В файле будет весь отбор."))
        elif not count:
            layout.addWidget(_label("В выбранных справочниках пока нет записей."))
        warning = _label("Локальный файл будет доступен для чтения без пароля.")
        warning.setProperty("stickyNotice", True)
        layout.addWidget(warning)
        self.save = QPushButton("Выбрать файл…")
        self.save.setObjectName("primary")
        self.save.setProperty("stickyActions", True)
        self.save.clicked.connect(self.accept)
        self.save.setEnabled(bool(count))
        layout.addWidget(self.save)
