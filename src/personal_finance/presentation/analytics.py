"""Период и таблицы отчёта только для чтения, без финансовых команд."""

from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.analytics import FlowTotals, IncomeExpenseReport, ReportPeriod
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import Category, Currency
from personal_finance.presentation.ledger_dialogs import category_title
from personal_finance.presentation.tables import replace_table


def _label(text: str) -> QLabel:
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    return result


def format_report_amount(minor: int, currency: Currency) -> str:
    scale = 10**currency.exponent
    whole, fraction = divmod(abs(minor), scale)
    text = f"{whole:,}".replace(",", " ")
    if currency.exponent:
        text += "," + f"{fraction:0{currency.exponent}d}"
    return ("−" if minor < 0 else "") + text


class ReportPeriodDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Доходы и расходы · Период")
        self.result_period: ReportPeriod | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label("Все счета и категории за выбранный период. Валюты рассчитываются отдельно.")
        )
        form = QFormLayout()
        today = date.today()
        self.start = QDateEdit(QDate(today.year, today.month, 1))
        self.end = QDateEdit(QDate(today.year, today.month, today.day))
        for editor, name in (
            (self.start, "Начало периода отчёта"),
            (self.end, "Конец периода отчёта"),
        ):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd.MM.yyyy")
            editor.setMinimumDate(QDate(1, 1, 1))
            editor.setMaximumDate(QDate(9999, 12, 31))
            editor.setAccessibleName(name)
        form.addRow("С", self.start)
        form.addRow("По включительно", self.end)
        layout.addLayout(form)
        self.feedback = _label("")
        self.feedback.setObjectName("feedbackError")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Построить отчёт")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        try:
            self.result_period = ReportPeriod(
                date(self.start.date().year(), self.start.date().month(), self.start.date().day()),
                date(self.end.date().year(), self.end.date().month(), self.end.date().day()),
            )
        except ValidationError as error:
            self.result_period = None
            self.feedback.setText(str(error))
            self.feedback.show()
            return
        self.accept()


class IncomeExpenseDialog(QDialog):
    def __init__(
        self, report: IncomeExpenseReport, categories: list[Category], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.report = report
        self.setWindowTitle("Доходы и расходы")
        self.resize(1020, 650)
        self.setMinimumSize(760, 480)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                f"{report.period.start:%d.%m.%Y} — {report.period.end:%d.%m.%Y} · "
                f"Операций в расчёте: {report.operation_count}"
            )
        )
        layout.addWidget(
            _label(
                "Переводы между своими счетами и начальные остатки исключены из доходов/расходов. "
                "Комиссии включены в расходы; возвраты уменьшают расходы на дату получения. "
                "Разница = доходы − расходы."
            )
        )
        tabs = QTabWidget()
        self.summary_table = self._table(
            tabs,
            "Итоги",
            ["Валюта", "Доходы", "Расходы с возвратами", "Разница"],
            [self._total_row(v) for v in report.totals],
            (1, 2, 3),
        )
        names = {c.id: category_title(c, categories) for c in categories}
        self.category_table = self._table(
            tabs,
            "Категории",
            ["Категория", "Валюта", "Доходы", "Расходы с возвратами"],
            [
                [
                    names.get(v.category_id, "Недоступная категория"),
                    v.totals.currency.code,
                    format_report_amount(v.totals.income_minor, v.totals.currency),
                    format_report_amount(v.totals.expense_minor, v.totals.currency),
                ]
                for v in sorted(
                    report.categories,
                    key=lambda v: (v.totals.currency.code, names.get(v.category_id, "").casefold()),
                )
            ],
            (2, 3),
        )
        self.month_table = self._table(
            tabs,
            "По месяцам",
            ["Месяц", "Валюта", "Доходы", "Расходы с возвратами", "Разница"],
            [[f"{v.month:%m.%Y}", *self._total_row(v.totals)] for v in report.months],
            (2, 3, 4),
        )
        layout.addWidget(tabs, 1)
        self.notice = _label(
            "В выбранном периоде нет действующих доходов, расходов, возвратов или комиссий."
            if not report.operation_count
            else "Снимок текущих записанных операций. "
            "После изменения книги сформируйте отчёт заново. "
            "В таблице месяцев показаны месяцы с движениями; отрицательный расход означает "
            "превышение возвратов в этом периоде."
        )
        layout.addWidget(self.notice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _total_row(value: FlowTotals) -> list[str]:
        return [
            value.currency.code,
            format_report_amount(value.income_minor, value.currency),
            format_report_amount(value.expense_minor, value.currency),
            format_report_amount(value.difference_minor, value.currency),
        ]

    @staticmethod
    def _table(
        tabs: QTabWidget,
        title: str,
        headers: list[str],
        rows: list[list[str]],
        right_columns: tuple[int, ...],
    ) -> QTableView:
        table = QTableView()
        table.setAccessibleName(title + " отчёта")
        replace_table(table, headers, rows, right_columns=right_columns)
        table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        tabs.addTab(table, title)
        return table
