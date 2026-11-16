"""Сценарный расчёт цели накоплений в рабочей области отчётов."""

from datetime import date

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import RUB, Account, Money
from personal_finance.domain.savings_goals import (
    SavingsGoalAccountProgress,
    SavingsGoalDay,
    SavingsGoalProjection,
    SavingsGoalScenario,
)
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.analytics_cashflow import _size, _view
from personal_finance.presentation.tables import replace_table


def _percent(basis_points: int) -> str:
    return f"{basis_points // 100},{basis_points % 100:02d} %"


def _date(field: QDateEdit) -> date:
    day = field.date()
    return date(day.year(), day.month(), day.day())


class SavingsGoalOptions(QDialog):
    calculationRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Цель накоплений · Условия")
        self.result_request: SavingsGoalScenario | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                "Введите суммы для сценария. Первое пополнение — через месяц после даты расчёта, "
                "затем ежемесячно в то же число. В коротком месяце — в его последний день."
            )
        )
        form = QFormLayout()
        form.setHorizontalSpacing(24)
        self.as_of = QDateEdit()
        self.deadline = QDateEdit()
        for date_field, title in ((self.as_of, "Дата расчёта цели"), (self.deadline, "Срок цели")):
            date_field.setDateRange(QDate(1, 1, 1), QDate(9999, 12, 31))
            date_field.setCalendarPopup(True)
            date_field.setDisplayFormat("dd.MM.yyyy")
            date_field.setAccessibleName(title)
        today = QDate.currentDate()
        self.as_of.setDate(today)
        self.deadline.setDate(today.addYears(1))
        self.current = QLineEdit("0,00")
        self.target = QLineEdit()
        self.monthly = QLineEdit("0,00")
        self.target.setPlaceholderText("Например, 300 000,00")
        for field, title in (
            (self.current, "Уже накоплено"),
            (self.target, "Сумма цели"),
            (self.monthly, "Пополнение в месяц"),
        ):
            field.setMaxLength(128)
            field.setAccessibleName(title + " в рублях")
        self.use_deadline = QCheckBox("Рассчитать нужное пополнение к сроку")
        self.use_deadline.setChecked(True)
        self.use_deadline.toggled.connect(self.deadline.setEnabled)
        form.addRow("Дата расчёта", self.as_of)
        form.addRow("Уже накоплено, RUB", self.current)
        form.addRow("Цель, RUB", self.target)
        form.addRow("Пополнение в месяц, RUB", self.monthly)
        form.addRow(self.use_deadline)
        form.addRow("Срок включительно", self.deadline)
        layout.addLayout(form)
        layout.addWidget(
            _label(
                "Расчёт без процентов, налогов и изменения цен. "
                "Сценарий хранится до закрытия формы."
            )
        )
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
        buttons.accepted.connect(self._calculate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _calculate(self) -> None:
        self.result_request = None
        try:
            request = SavingsGoalScenario(
                _date(self.as_of),
                Money.parse(self.current.text(), RUB),
                Money.parse(self.target.text(), RUB),
                Money.parse(self.monthly.text(), RUB),
                _date(self.deadline) if self.use_deadline.isChecked() else None,
            )
        except ValidationError as error:
            self.feedback.setText(str(error))
            self.feedback.show()
            return
        self.feedback.hide()
        self.result_request = request
        self.calculationRequested.emit()


class SavingsGoalAccountOptions(QDialog):
    calculationRequested = Signal(str)

    def __init__(
        self, scenario: SavingsGoalScenario, accounts: list[Account], parent: QWidget
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Цель накоплений · Выбор счёта")
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                f"Остаток на конец {scenario.as_of:%d.%m.%Y}. "
                "Выберите счёт, средства которого выделены на эту цель."
            )
        )
        self.account = QComboBox()
        self.account.setAccessibleName("Счёт для расчёта цели")
        self.account.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.account.setMinimumContentsLength(22)
        self.account.addItem("Выберите счёт", None)
        for account in sorted(accounts, key=lambda item: (item.name.casefold(), item.id)):
            self.account.addItem(account.name, account.id)
        form = QFormLayout()
        form.addRow("Счёт", self.account)
        layout.addLayout(form)
        self.feedback = _label("" if accounts else "В книге нет счёта в валюте цели.")
        self.feedback.setObjectName("feedbackError")
        self.feedback.setVisible(not accounts)
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.calculate = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.calculate.setText("Рассчитать")
        self.calculate.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.account.currentIndexChanged.connect(self._account_changed)
        buttons.accepted.connect(self._request)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        layout.addStretch(1)

    def _account_changed(self, index: int) -> None:
        self.calculate.setEnabled(index > 0)
        self.feedback.hide()

    def show_error(self, message: str) -> None:
        self.feedback.setText(message)
        self.feedback.show()

    def _request(self) -> None:
        if identifier := self.account.currentData():
            self.calculationRequested.emit(identifier)


class SavingsGoalDialog(QDialog):
    def __init__(
        self,
        result: SavingsGoalProjection,
        parent: QWidget | None = None,
        *,
        account_name: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.projection = result
        self.setWindowTitle("Цель накоплений · Расчёт")
        _size(self, 900, 650)
        layout = QVBoxLayout(self)
        scenario = result.scenario
        if account_name is None:
            layout.addWidget(_label("Прогноз по введённым суммам и ежемесячным пополнениям."))
        else:
            layout.addWidget(
                _label(
                    f"Счёт «{account_name}». Прогноз от денежного остатка по журналу "
                    "и ежемесячного пополнения из условий цели."
                )
            )
        form = QFormLayout()
        form.setHorizontalSpacing(24)
        self.fields: dict[str, QLabel] = {}

        def field(title: str, value: str) -> None:
            label = _label(value)
            self.fields[title] = label
            form.addRow(title, label)

        def amount(minor: int) -> str:
            currency = scenario.target.currency
            return f"{format_report_amount(minor, currency)} {currency.code}"

        field("Дата расчёта", f"{scenario.as_of:%d.%m.%Y}")
        field(
            "Уже накоплено" if account_name is None else "Остаток на счёте",
            amount(scenario.current.minor),
        )
        field("Цель", amount(scenario.target.minor))
        field("Осталось накопить", amount(result.remaining_minor))
        field("Прогресс", _percent(result.progress_basis_points))
        field("Пополнение в месяц", amount(scenario.monthly.minor))
        if result.remaining_minor == 0:
            field("Состояние", "Цель уже достигнута")
        else:
            field(
                "Первое пополнение",
                f"{result.first_contribution_on:%d.%m.%Y}"
                if result.first_contribution_on
                else "За пределами 31.12.9999",
            )
            field(
                "Пополнений до цели",
                str(result.contributions_to_goal)
                if result.contributions_to_goal is not None
                else "Без пополнений срок не рассчитан",
            )
        field(
            "Ожидаемая дата цели",
            f"{result.reached_on:%d.%m.%Y}"
            if result.reached_on
            else "Без пополнений срок не рассчитан"
            if result.contributions_to_goal is None
            else "За пределами 31.12.9999",
        )
        if scenario.deadline is not None:
            field("Срок включительно", f"{scenario.deadline:%d.%m.%Y}")
            field("Пополнений до срока", str(result.contributions_by_deadline))
            if result.at_deadline_minor is not None:
                field("Сумма к сроку", amount(result.at_deadline_minor))
            field(
                "Нужно пополнять в месяц",
                amount(result.required_monthly_minor)
                if result.required_monthly_minor is not None
                else "До срока нет ежемесячных пополнений",
            )
        layout.addLayout(form)
        layout.addWidget(
            _label(
                "Нужное пополнение округлено вверх до копейки. "
                f"Плановые пополнения — {scenario.as_of.day}-го числа каждого месяца; "
                "в коротком месяце — в его последний день. "
                "Проценты, налоги и изменение цен не учитываются."
            )
        )
        layout.addStretch()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setText(
            "К условиям" if account_name is None else "К выбору счёта"
        )
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)


class SavingsGoalHistoryDialog(QDialog):
    sourcesRequested = Signal(int)

    def __init__(
        self,
        result: SavingsGoalAccountProgress,
        history: tuple[SavingsGoalDay, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Цель накоплений · История прогресса")
        _size(self, 780, 600)
        self.days = history[::-1]
        target = result.projection.scenario.target
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                f"Счёт «{result.account.name}» · Цель "
                f"{format_report_amount(target.minor, target.currency)} "
                f"{target.currency.code}. Остатки на конец дня."
            )
        )
        self.table = _view("История прогресса по счёту")
        replace_table(
            self.table,
            ["Дата", "Остаток · " + target.currency.code, "Прогресс"],
            [
                [
                    f"{row.day:%d.%m.%Y}",
                    format_report_amount(row.balance_minor, target.currency),
                    _percent(row.progress_basis_points),
                ]
                for row in self.days
            ],
            right_columns=(1, 2),
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("К расчёту")
        buttons.rejected.connect(self.reject)
        self.sources = buttons.addButton("Движения за день", QDialogButtonBox.ButtonRole.ActionRole)
        self.sources.clicked.connect(self._request)
        self.table.doubleClicked.connect(lambda _: self._request())
        self.table.selectionModel().selectionChanged.connect(self._update_actions)
        layout.addWidget(buttons)
        if self.days:
            self.table.selectRow(0)
        self._update_actions()

    def _selected(self) -> int | None:
        index = self.table.currentIndex()
        row = index.row()
        if not index.isValid() or not self.table.selectionModel().hasSelection():
            return None
        return row if self.days[row].sources else None

    def _update_actions(self) -> None:
        self.sources.setEnabled(self._selected() is not None)

    def _request(self) -> None:
        if (index := self._selected()) is not None:
            self.sourcesRequested.emit(index)
