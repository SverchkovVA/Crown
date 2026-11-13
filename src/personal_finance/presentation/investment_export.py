"""Состав открытого файла портфеля и подтверждение сохранения."""

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.exporting import ExportFormat
from personal_finance.domain.investment_export import (
    PortfolioExport,
    portfolio_export_row,
    prepare_portfolio_export,
)
from personal_finance.domain.investment_portfolio import SavedPortfolioReport
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.deposit_scenarios import date_text
from personal_finance.presentation.recurring_payments import _table
from personal_finance.presentation.tables import replace_table


class InvestmentExportDialog(QDialog):
    def __init__(self, report: SavedPortfolioReport, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Экспорт портфеля")
        self.report = report
        layout = QVBoxLayout(self)
        layout.addWidget(_label(f"Позиций: {len(report.rows)} · На {date_text(report.as_of)}"))
        if report.unavailable_count:
            layout.addWidget(_label(f"Позиции на недоступных счетах: {report.unavailable_count}."))
        layout.addWidget(
            _label(
                "В файл войдут коды бумаг, количества, суммы и даты цен. "
                "Без журнала операций, реквизитов, паролей и идентификаторов книги."
            )
        )
        fields = QFormLayout()
        self.format = QComboBox()
        self.format.addItem("CSV — для таблиц", ExportFormat.CSV.value)
        self.format.addItem("JSON — для обработки данных", ExportFormat.JSON.value)
        self.format.setAccessibleName("Формат файла портфеля")
        fields.addRow("Формат", self.format)
        self.include_names = QCheckBox("Включить названия счетов")
        fields.addRow(self.include_names)
        layout.addLayout(fields)
        self.preview: QTableView = _table("Предпросмотр строк портфеля")
        layout.addWidget(self.preview, 1)
        self.preview_note: QLabel = _label("")
        layout.addWidget(self.preview_note)
        self.include_names.toggled.connect(self._update_preview)
        self._update_preview()
        warning = _label("Файл будет открыт для чтения без пароля.")
        warning.setProperty("stickyNotice", True)
        layout.addWidget(warning)
        self.save = QPushButton("Выбрать файл…")
        self.save.setObjectName("primary")
        self.save.setProperty("stickyActions", True)
        self.save.clicked.connect(self.accept)
        layout.addWidget(self.save)

    def _update_preview(self) -> None:
        include_names = self.include_names.isChecked()
        headers = ["Инструмент", "Валюта", "Бумаг", "Оценка"]
        if include_names:
            headers.insert(1, "Счёт")
        rows = []
        for source in self.report.rows[:8]:
            item = portfolio_export_row(source, include_names=include_names)
            value = (
                "—"
                if item.market_minor is None
                else format_report_amount(item.market_minor, item.currency)
            )
            row = [item.security_code, item.currency.code, str(item.quantity), value]
            if include_names:
                row.insert(1, item.account_name or "")
            rows.append(row)
        replace_table(
            self.preview, headers, rows, right_columns=(len(headers) - 2, len(headers) - 1)
        )
        column = 1 if include_names else 0
        self.preview.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        count = len(self.report.rows)
        self.preview_note.setText(f"Показаны первые 8 из {count} строк." if count > 8 else "")

    def selection(self) -> tuple[PortfolioExport, ExportFormat]:
        return (
            prepare_portfolio_export(self.report, include_names=self.include_names.isChecked()),
            ExportFormat(self.format.currentData()),
        )
