"""Редактирование сохранённых условий бюджета и цели во встроенных формах."""

from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QComboBox, QLabel, QVBoxLayout, QWidget

from personal_finance.domain.analytics import ReportPeriod, ReportScope
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import RUB, Account, Category, Money
from personal_finance.domain.periods import AccountingMonth
from personal_finance.domain.planning_scenarios import BudgetPeriodMode, BudgetScenario
from personal_finance.domain.savings_goals import SavingsGoalScenario
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.budget import BudgetOptions
from personal_finance.presentation.savings_goals import SavingsGoalOptions


def _qdate(day: date) -> QDate:
    return QDate(day.year, day.month, day.day)


def _selected_ids(combo: QComboBox) -> frozenset[str] | None:
    value = combo.currentData()
    if value is None:
        return None
    if type(value) is str:
        return frozenset((value,))
    if type(value) is frozenset:
        return value
    raise ValidationError("Выберите доступный отбор бюджета.")


def _load_ids(combo: QComboBox, identifiers: frozenset[str] | None, title: str) -> None:
    if identifiers is None:
        combo.setCurrentIndex(0)
        return
    if len(identifiers) == 1:
        index = combo.findData(next(iter(identifiers)))
        if index >= 0:
            combo.setCurrentIndex(index)
            return
    caption = (
        "Ничего не выбрано" if not identifiers else f"Сохранённый отбор: {len(identifiers)} {title}"
    )
    combo.addItem(caption, identifiers)
    combo.setCurrentIndex(combo.count() - 1)


class BudgetScenarioOptions(BudgetOptions):
    def __init__(
        self,
        accounts: list[Account],
        categories: list[Category],
        parent: QWidget | None = None,
        *,
        scenario: BudgetScenario | None = None,
    ) -> None:
        if scenario is not None and (
            not isinstance(scenario, BudgetScenario) or scenario.limit.currency != RUB
        ):
            raise ValidationError("Эта форма поддерживает условия бюджета в RUB с точностью 2.")
        super().__init__(accounts, categories, parent)
        self.scenario_request: BudgetScenario | None = None
        self.scope_note = _label(
            "Сохранённый отбор из нескольких записей сохраняется целиком. "
            "Выбор другого пункта заменит его."
        )
        self.scope_note.hide()
        layout = self.layout()
        assert isinstance(layout, QVBoxLayout)
        layout.insertWidget(1, self.scope_note)
        if scenario is None:
            return
        self.period_mode.setCurrentIndex(0 if scenario.period_mode is BudgetPeriodMode.MONTH else 1)
        self.month.setDate(_qdate(scenario.period.start))
        self.start.setDate(_qdate(scenario.period.start))
        self.end.setDate(_qdate(scenario.period.end))
        self.limit.setText(format_report_amount(scenario.limit.minor, RUB))
        _load_ids(self.account, scenario.scope.account_ids, "счетов")
        _load_ids(self.category, scenario.scope.category_ids, "категорий")
        self.scope_note.setVisible(
            type(self.account.currentData()) is frozenset
            or type(self.category.currentData()) is frozenset
        )

    def _accept(self) -> None:
        self.result_request = None
        self.scenario_request = None
        try:
            if self.period_mode.currentIndex() == 0:
                day = self.month.date()
                month = AccountingMonth(day.year(), day.month())
                period = ReportPeriod(month.start, month.end)
                mode = BudgetPeriodMode.MONTH
            else:
                first, last = self.start.date(), self.end.date()
                period = ReportPeriod(
                    date(first.year(), first.month(), first.day()),
                    date(last.year(), last.month(), last.day()),
                )
                mode = BudgetPeriodMode.DATES
            request = BudgetScenario(
                period,
                Money.parse(self.limit.text(), RUB),
                ReportScope(_selected_ids(self.account), _selected_ids(self.category)),
                mode,
            )
        except ValidationError as error:
            self.feedback.setText(str(error))
            self.feedback.show()
            return
        self.feedback.hide()
        self.scenario_request = request
        self.result_request = (request.period, request.limit, request.scope)
        self.calculationRequested.emit()


class SavingsGoalScenarioOptions(SavingsGoalOptions):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        scenario: SavingsGoalScenario | None = None,
    ) -> None:
        if scenario is not None and (
            not isinstance(scenario, SavingsGoalScenario) or scenario.target.currency != RUB
        ):
            raise ValidationError("Эта форма поддерживает условия цели в RUB с точностью 2.")
        super().__init__(parent)
        for label in self.findChildren(QLabel):
            if "Сценарий хранится до закрытия формы." in label.text():
                label.setText("Расчёт без процентов, налогов и изменения цен.")
        if scenario is None:
            return
        self.as_of.setDate(_qdate(scenario.as_of))
        self.current.setText(format_report_amount(scenario.current.minor, RUB))
        self.target.setText(format_report_amount(scenario.target.minor, RUB))
        self.monthly.setText(format_report_amount(scenario.monthly.minor, RUB))
        self.use_deadline.setChecked(scenario.deadline is not None)
        if scenario.deadline is not None:
            self.deadline.setDate(_qdate(scenario.deadline))
