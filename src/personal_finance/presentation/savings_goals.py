"""Сценарный расчёт цели накоплений в рабочей области отчётов."""

from datetime import date

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import RUB, Money
from personal_finance.domain.savings_goals import SavingsGoalProjection, SavingsGoalScenario
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.analytics_cashflow import _size


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


class SavingsGoalDialog(QDialog):
    def __init__(self, result: SavingsGoalProjection, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.projection = result
        self.setWindowTitle("Цель накоплений · Расчёт")
        _size(self, 900, 650)
        layout = QVBoxLayout(self)
        scenario = result.scenario
        layout.addWidget(
            _label(
                "Прогноз по введённым суммам и ежемесячным пополнениям. "
                "Сверяйте накопления с журналом и обновляйте условия по мере изменений."
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
        field("Уже накоплено", amount(scenario.current.minor))
        field("Цель", amount(scenario.target.minor))
        field("Осталось накопить", amount(result.remaining_minor))
        percent = result.progress_basis_points
        field("Прогресс", f"{percent // 100},{percent % 100:02d} %")
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
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("К условиям")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
