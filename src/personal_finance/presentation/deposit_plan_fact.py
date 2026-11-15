"""Встроенные формы сверки процентов по вкладу."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QPushButton,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.analytics import OperationFlow
from personal_finance.domain.analytics_cashflow import CashMovement
from personal_finance.domain.deposit_plan_fact import DepositPlanFact
from personal_finance.domain.deposit_scenarios import SavedDepositScenario
from personal_finance.domain.ledger import OPERATION_LABELS
from personal_finance.domain.models import Account, Category, CategoryKind
from personal_finance.presentation.analytics import (
    OperationSourceDialog,
    _label,
    format_report_amount,
)
from personal_finance.presentation.analytics_cashflow import _size
from personal_finance.presentation.deposit_scenarios import date_text
from personal_finance.presentation.inline_forms import present_form
from personal_finance.presentation.ledger_dialogs import category_title
from personal_finance.presentation.tables import replace_table


def _annual_percent(value: Decimal) -> str:
    display = f"{value:.2E}" if value.adjusted() >= 6 else f"{value:.2f}"
    return display.replace(".", ",") + " %"


@dataclass(frozen=True, slots=True)
class DepositPlanFactSelection:
    account: Account
    category: Category
    through: date
    dedicated_account: bool = False


class DepositPlanFactOptions(QDialog):
    def __init__(
        self,
        record: SavedDepositScenario,
        accounts: list[Account],
        categories: list[Category],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("План и факт процентов")
        self.result_selection: DepositPlanFactSelection | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(_label(record.name))
        layout.addWidget(
            _label(
                "Выберите счёт получения процентов и доходную категорию, в которой "
                "записаны проценты этого вклада. Учитываются записи именно этой категории."
            )
        )
        currency = record.scenario.initial.currency
        self._accounts = {a.id: a for a in accounts if a.currency == currency.code}
        self._categories = {c.id: c for c in categories if c.kind == CategoryKind.INCOME}
        self.account = QComboBox()
        self.account.setAccessibleName("Счёт получения процентов")
        self.account.addItem("Выберите счёт · " + currency.code, None)
        for account in sorted(self._accounts.values(), key=lambda a: a.name.casefold()):
            self.account.addItem(account.name, account.id)
        self.category = QComboBox()
        self.category.setAccessibleName("Категория процентов")
        self.category.addItem("Выберите доходную категорию", None)
        for name, identifier in sorted(
            (category_title(c, categories), c.id) for c in self._categories.values()
        ):
            self.category.addItem(name, identifier)
        for combo in (self.account, self.category):
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(24)
        opening = record.scenario.opened_on
        layout.addWidget(
            _label(
                f"Сравнение начинается {date_text(opening)}. "
                "Для задержанной выплаты можно указать дату позже окончания вклада."
            )
        )
        default = min(max(opening, date.today()), record.scenario.matures_on)
        self.through = QDateEdit()
        self.through.setAccessibleName("Дата сравнения включительно")
        self.through.setDateRange(
            QDate(opening.year, opening.month, opening.day), QDate(9999, 12, 31)
        )
        self.through.setDate(QDate(default.year, default.month, default.day))
        self.through.setDisplayFormat("dd.MM.yyyy")
        self.through.setCalendarPopup(True)
        fields = QFormLayout()
        fields.addRow("Счёт", self.account)
        fields.addRow("Категория", self.category)
        fields.addRow("По включительно", self.through)
        layout.addLayout(fields)
        self.dedicated_account = QCheckBox("Рассчитать доходность по отдельному счёту вклада")
        self.dedicated_account.setToolTip(
            "Выберите, если на счёте записаны все движения только этого вклада и его проценты."
        )
        layout.addWidget(self.dedicated_account)
        self.feedback = _label("")
        self.feedback.setObjectName("feedbackError")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.compare = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.compare.setText("Сравнить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.compare.setEnabled(bool(self._accounts and self._categories))
        if not self.compare.isEnabled():
            self.feedback.setText("Нужны счёт в валюте сценария и доходная категория.")
            self.feedback.show()
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _submit(self) -> None:
        account = self._accounts.get(self.account.currentData())
        category = self._categories.get(self.category.currentData())
        if account is None or category is None:
            self.feedback.setText("Выберите счёт и доходную категорию процентов.")
            self.feedback.show()
            return
        self.result_selection = DepositPlanFactSelection(
            account,
            category,
            date(*self.through.date().getDate()),
            self.dedicated_account.isChecked(),
        )
        self.accept()


class DepositPlanFactDialog(QDialog):
    def __init__(
        self,
        report: DepositPlanFact,
        record: SavedDepositScenario,
        selection: DepositPlanFactSelection,
        accounts: list[Account],
        categories: list[Category],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.report = report
        self._accounts = {a.id: a.name for a in accounts}
        self._categories = {c.id: category_title(c, categories) for c in categories}
        self.setWindowTitle("План и факт процентов")
        _size(self, 920, 620)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                f"{record.name} · версия {record.revision_id[:8]}\n"
                f"{date_text(report.period.start)} — {date_text(report.period.end)}\n"
                f"{selection.account.name} · {self._categories[selection.category.id]}"
            )
        )
        layout.addWidget(
            _label(
                "План включает проценты на даты выплат из сценария. "
                "Факт берётся из журнала по выбранным счёту и категории. "
                "Разница равна факту минус план."
            )
        )

        def amount(minor: int) -> str:
            return f"{format_report_amount(minor, report.currency)} {report.currency.code}"

        totals = QFormLayout()
        totals.addRow("План", _label(amount(report.planned_minor)))
        totals.addRow("Факт по журналу", _label(amount(report.actual_minor)))
        totals.addRow("Разница", _label(amount(report.difference_minor)))
        layout.addLayout(totals)
        actual = report.actual_yield
        if actual is not None:
            planned = report.planned_yield
            totals.addRow(
                "Годовая доходность по сценарию",
                _label(_annual_percent(planned) if planned is not None else "Недоступна"),
            )
            annual = actual.annual_percent
            if annual is None:
                layout.addWidget(
                    _label(f"Годовая доходность недоступна. {actual.unavailable_reason}")
                )
            else:
                totals.addRow("Годовая доходность по журналу", _label(_annual_percent(annual)))
                totals.addRow("Остаток на дату среза", _label(amount(actual.closing_minor)))
        if not report.sources:
            layout.addWidget(_label("В выбранном счёте и категории за этот период записей нет."))

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        self.months = QTableView()
        self.months.setAccessibleName("План и факт процентов по месяцам")
        self._configure_table(self.months)
        replace_table(
            self.months,
            ["Месяц", "План", "Факт", "Разница"],
            [
                [
                    f"{row.month:%m.%Y}",
                    amount(row.planned_minor),
                    amount(row.actual_minor),
                    amount(row.difference_minor),
                ]
                for row in report.months
            ],
            right_columns=(1, 2, 3),
        )
        tabs.addTab(self.months, "По месяцам")

        details = QWidget()
        detail_layout = QVBoxLayout(details)
        self.sources = QTableView()
        self.sources.setAccessibleName("Учтённые доходы из журнала")
        self._configure_table(self.sources)
        replace_table(
            self.sources,
            ["Дата", "Примечание", "Учтено"],
            [
                [
                    date_text(flow.operation.draft.occurred_on),
                    flow.operation.draft.note or "—",
                    amount(flow.totals.income_minor),
                ]
                for flow in report.sources
            ],
            right_columns=(2,),
        )
        detail_layout.addWidget(self.sources, 1)
        self.open_source = QPushButton("Открыть версию операции")
        self.open_source.setEnabled(False)
        self.sources.selectionModel().selectionChanged.connect(
            lambda: self.open_source.setEnabled(bool(self.sources.selectionModel().selectedRows()))
        )
        self.open_source.clicked.connect(self._open_source)
        self.sources.doubleClicked.connect(lambda _: self._open_source())
        detail_layout.addWidget(self.open_source)
        tabs.addTab(details, "Записи журнала")
        if actual is not None and actual.annual_percent is not None:
            movements = QWidget()
            movement_layout = QVBoxLayout(movements)
            self.yield_sources = QTableView()
            self.yield_sources.setAccessibleName("Движения отдельного счёта вклада")
            self._configure_table(self.yield_sources)
            replace_table(
                self.yield_sources,
                ["Дата", "Вид", "По счёту"],
                [
                    [
                        date_text(flow.operation.draft.occurred_on),
                        OPERATION_LABELS[flow.operation.draft.kind],
                        amount(flow.minor),
                    ]
                    for flow in actual.movements
                ],
                right_columns=(2,),
            )
            movement_layout.addWidget(self.yield_sources, 1)
            open_movement = QPushButton("Открыть версию операции")
            open_movement.setEnabled(False)
            self.yield_sources.selectionModel().selectionChanged.connect(
                lambda: open_movement.setEnabled(
                    bool(self.yield_sources.selectionModel().selectedRows())
                )
            )

            def open_details() -> None:
                self._open_selected(self.yield_sources, actual.movements)

            open_movement.clicked.connect(open_details)
            self.yield_sources.doubleClicked.connect(lambda _: open_details())
            movement_layout.addWidget(open_movement)
            tabs.addTab(movements, "Движения вклада")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("К списку сценариев")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _configure_table(self, table: QTableView) -> None:
        table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        header = QHeaderView(Qt.Orientation.Horizontal, table)
        table.setHorizontalHeader(header)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

    def _open_source(self) -> None:
        self._open_selected(self.sources, self.report.sources)

    def _open_selected(
        self, table: QTableView, flows: Sequence[OperationFlow | CashMovement]
    ) -> None:
        rows = table.selectionModel().selectedRows()
        if len(rows) != 1:
            return
        flow = flows[rows[0].row()]
        present_form(OperationSourceDialog(flow, self._categories, self._accounts, self), None)
