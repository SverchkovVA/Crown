"""Выбор периода и предпросмотр локального экспорта бонусов."""

from PySide6.QtCore import QDate, Signal
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

from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.bonus_export import BonusExport
from personal_finance.domain.bonuses import KIND_LABELS, format_units
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.exporting import ExportFormat
from personal_finance.presentation.analytics import _label
from personal_finance.presentation.deposit_scenarios import date_text
from personal_finance.presentation.recurring_payments import _date, _date_input, _table
from personal_finance.presentation.tables import replace_table


class BonusExportOptions(QDialog):
    previewRequested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Экспорт бонусного журнала")
        layout = QVBoxLayout(self)
        layout.addWidget(_label("В файл войдут действующие бонусные записи за выбранные даты."))
        form = QFormLayout()
        today = QDate.currentDate()
        self.start = _date_input(QDate(today.year(), today.month(), 1), "Начало периода бонусов")
        self.end = _date_input(today, "Конец периода бонусов")
        form.addRow("Начало", self.start)
        form.addRow("Конец", self.end)
        self.format = QComboBox()
        self.format.setAccessibleName("Формат экспорта бонусов")
        self.format.addItem("CSV — для таблиц", ExportFormat.CSV.value)
        self.format.addItem("JSON — для обработки данных", ExportFormat.JSON.value)
        form.addRow("Формат", self.format)
        layout.addLayout(form)
        self.include_names = QCheckBox("Включить названия программ и категорий")
        layout.addWidget(self.include_names)
        layout.addWidget(_label("Без этой отметки будут использованы условные названия."))
        self.error = _label("")
        self.error.setObjectName("feedbackError")
        self.error.hide()
        layout.addWidget(self.error)
        self.preview = QPushButton("Предпросмотр")
        self.preview.setObjectName("primary")
        self.preview.clicked.connect(self.previewRequested)
        layout.addWidget(self.preview)
        layout.addStretch()

    def selection(self) -> tuple[ReportPeriod, ExportFormat, bool] | None:
        try:
            period = ReportPeriod(_date(self.start), _date(self.end))
        except ValidationError as error:
            self.error.setText(str(error))
            self.error.show()
            return None
        self.error.hide()
        return period, ExportFormat(self.format.currentData()), self.include_names.isChecked()


class BonusExportPreview(QDialog):
    def __init__(self, snapshot: BonusExport, format: ExportFormat, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Предпросмотр экспорта бонусов")
        layout = QVBoxLayout(self)
        period = snapshot.period
        count = len(snapshot.rows)
        layout.addWidget(
            _label(
                f"{date_text(period.start)} — {date_text(period.end)}"
                f" · {format.value.upper()} · Записей: {count}"
            )
        )
        self.table = _table("Данные бонусного файла")
        replace_table(
            self.table,
            ["Дата", "Программа", "Вид", "Бонусы", "Категория"],
            [
                [
                    date_text(row.day),
                    row.program,
                    KIND_LABELS[row.kind],
                    format_units(row.units_minor),
                    row.category or "—",
                ]
                for row in snapshot.rows[:200]
            ],
            right_columns=(3,),
        )
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        if count > 200:
            layout.addWidget(_label(f"Первые 200 из {count} записей. В файле будет весь период."))
        elif not count:
            layout.addWidget(_label("За выбранный период бонусных записей нет."))
        warning = _label("Локальный файл будет доступен для чтения без пароля.")
        warning.setProperty("stickyNotice", True)
        layout.addWidget(warning)
        self.save = QPushButton("Выбрать файл…")
        self.save.setObjectName("primary")
        self.save.setProperty("stickyActions", True)
        self.save.clicked.connect(self.accept)
        self.save.setEnabled(bool(count))
        layout.addWidget(self.save)
