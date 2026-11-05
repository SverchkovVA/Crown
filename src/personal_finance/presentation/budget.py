"""Лимит за выбранный период и учтённые расходы в рабочей области отчётов."""

from datetime import date

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.analytics import ReportPeriod, ReportScope
from personal_finance.domain.budget import BudgetExecution
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OPERATION_LABELS
from personal_finance.domain.models import RUB, Account, Category, CategoryKind, Money
from personal_finance.domain.periods import AccountingMonth
from personal_finance.presentation.analytics import (
    OperationSourceDialog,
    _label,
    format_report_amount,
)
from personal_finance.presentation.analytics_cashflow import _size
from personal_finance.presentation.inline_forms import present_form
from personal_finance.presentation.ledger_dialogs import category_title
from personal_finance.presentation.tables import replace_table


class BudgetOptions(QDialog):
    calculationRequested = Signal()

    def __init__(
        self, accounts: list[Account], categories: list[Category], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Бюджет расходов")
        self.result_request: tuple[ReportPeriod, Money, ReportScope] | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                "Введите лимит для этого расчёта. Расходы берутся из журнала за выбранный период. "
                "Параметры можно изменить после расчёта."
            )
        )
        form = QFormLayout()
        self.period_mode = QComboBox()
        self.period_mode.setAccessibleName("Вид периода бюджета")
        self.period_mode.addItem("Календарный месяц")
        self.period_mode.addItem("По датам")
        self.month = QDateEdit(QDate.currentDate())
        self.month.setDateRange(QDate(1, 1, 1), QDate(9999, 12, 31))
        self.month.setDisplayFormat("MM.yyyy")
        self.month.setAccessibleName("Месяц бюджета")
        today = QDate.currentDate()
        self.start = QDateEdit()
        self.end = QDateEdit()
        for field, title in (
            (self.start, "Начало периода бюджета"),
            (self.end, "Окончание периода бюджета"),
        ):
            field.setDateRange(QDate(1, 1, 1), QDate(9999, 12, 31))
            field.setDisplayFormat("dd.MM.yyyy")
            field.setCalendarPopup(True)
            field.setAccessibleName(title)
        self.start.setDate(QDate(today.year(), today.month(), 1))
        self.end.setDate(QDate(today.year(), today.month(), today.daysInMonth()))
        self.limit = QLineEdit()
        self.limit.setMaxLength(128)
        self.limit.setPlaceholderText("Например, 15 000,00")
        self.limit.setAccessibleName("Лимит бюджета в рублях")
        self.account = QComboBox()
        self.account.setAccessibleName("Счёт бюджета")
        self.account.addItem("Все счета в RUB", None)
        for account in sorted(accounts, key=lambda a: a.name.casefold()):
            if account.currency == RUB.code:
                self.account.addItem(account.name, account.id)
        self.category = QComboBox()
        self.category.setAccessibleName("Категория бюджета")
        self.category.addItem("Все категории расходов", None)
        for title, identifier in sorted(
            (category_title(category, categories), category.id)
            for category in categories
            if category.kind == CategoryKind.EXPENSE
        ):
            self.category.addItem(title, identifier)
        for combo in (self.account, self.category):
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(24)
        form.addRow("Период", self.period_mode)
        form.addRow("Месяц", self.month)
        form.addRow("С", self.start)
        form.addRow("По", self.end)

        def update_period() -> None:
            by_dates = self.period_mode.currentIndex() == 1
            form.setRowVisible(self.month, not by_dates)
            form.setRowVisible(self.start, by_dates)
            form.setRowVisible(self.end, by_dates)

        self.period_mode.currentIndexChanged.connect(update_period)
        update_period()
        form.addRow("Лимит, RUB", self.limit)
        form.addRow("Счёт", self.account)
        form.addRow("Точная категория", self.category)
        layout.addLayout(form)
        self.feedback = _label("")
        self.feedback.setObjectName("feedbackError")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.calculate = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.calculate.setText("Рассчитать")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        self.result_request = None
        try:
            if self.period_mode.currentIndex() == 0:
                day = self.month.date()
                month = AccountingMonth(day.year(), day.month())
                period = ReportPeriod(month.start, month.end)
            else:
                start, end = self.start.date(), self.end.date()
                period = ReportPeriod(
                    date(start.year(), start.month(), start.day()),
                    date(end.year(), end.month(), end.day()),
                )
            limit = Money.parse(self.limit.text(), RUB)
            if limit.minor < 0:
                raise ValidationError("Введите неотрицательный лимит расходов.")
            account, category = self.account.currentData(), self.category.currentData()
            scope = ReportScope(
                None if account is None else frozenset((account,)),
                None if category is None else frozenset((category,)),
            )
        except ValidationError as error:
            self.feedback.setText(str(error))
            self.feedback.show()
            return
        self.feedback.hide()
        self.result_request = (period, limit, scope)
        self.calculationRequested.emit()


class BudgetDialog(QDialog):
    def __init__(
        self,
        report: BudgetExecution,
        accounts: list[Account],
        categories: list[Category],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.report = report
        self._accounts = {a.id: a.name for a in accounts}
        self._categories = {c.id: category_title(c, categories) for c in categories}
        self.setWindowTitle("Исполнение бюджета расходов")
        _size(self, 950, 620)
        layout = QVBoxLayout(self)

        def caption(ids: frozenset[str] | None, names: dict[str, str], default: str) -> str:
            if ids is None:
                return default
            return (
                ", ".join(sorted(names.get(key, "Недоступная запись") for key in ids))
                or "Нет выбранных записей"
            )

        account_title = caption(
            report.scope.account_ids, self._accounts, f"Все счета в {report.limit.currency.code}"
        )
        category_title_text = caption(
            report.scope.category_ids, self._categories, "Все категории расходов"
        )
        period_title = f"{report.period.start:%d.%m.%Y} — {report.period.end:%d.%m.%Y}"
        layout.addWidget(_label(f"{period_title} · {account_title}\n{category_title_text}"))
        layout.addWidget(
            _label("Возвраты уменьшают расходы в дату получения. Комиссии учитываются как расходы.")
        )

        def amount(minor: int) -> str:
            currency = report.limit.currency
            return f"{format_report_amount(minor, currency)} {currency.code}"

        totals = QFormLayout()
        totals.addRow("Лимит", _label(amount(report.limit.minor)))
        totals.addRow("Расходы с учётом возвратов", _label(amount(report.spent_minor)))
        remaining = report.remaining_minor
        totals.addRow(
            "Осталось по лимиту" if remaining >= 0 else "Лимит превышен на",
            _label(amount(abs(remaining))),
        )
        layout.addLayout(totals)
        if not report.sources:
            layout.addWidget(_label("За выбранный период расходов и возвратов в этом отборе нет."))

        self.sources = QTableView()
        self.sources.setAccessibleName("Учтённые операции бюджета")
        self.sources.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.sources.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.sources.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.sources.setWordWrap(False)
        header = QHeaderView(Qt.Orientation.Horizontal, self.sources)
        self.sources.setHorizontalHeader(header)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        replace_table(
            self.sources,
            ["Дата", "Вид", "Учтено в расходах", "Примечание"],
            [
                [
                    f"{flow.operation.draft.occurred_on:%d.%m.%Y}",
                    OPERATION_LABELS[flow.operation.draft.kind],
                    amount(flow.totals.expense_minor),
                    flow.operation.draft.note or "—",
                ]
                for flow in report.sources
            ],
            right_columns=(2,),
        )
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.sources, 1)
        self.open_source = QPushButton("Открыть версию операции")
        self.open_source.setEnabled(False)
        self.sources.selectionModel().selectionChanged.connect(
            lambda: self.open_source.setEnabled(bool(self.sources.selectionModel().selectedRows()))
        )
        self.open_source.clicked.connect(self._open_source)
        self.sources.doubleClicked.connect(lambda _: self._open_source())
        layout.addWidget(self.open_source)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("К параметрам")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _open_source(self) -> None:
        rows = self.sources.selectionModel().selectedRows()
        if len(rows) == 1:
            flow = self.report.sources[rows[0].row()]
            present_form(OperationSourceDialog(flow, self._categories, self._accounts, self), None)
