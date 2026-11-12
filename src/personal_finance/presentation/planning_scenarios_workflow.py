"""Сохранённые условия бюджета и цели во встроенных формах отчётов."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar
from uuid import uuid4

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit, QVBoxLayout

from personal_finance.application.analytics import Analytics
from personal_finance.application.planning_scenarios import PlanningScenarios
from personal_finance.application.vault import VaultSession
from personal_finance.domain.budget import BudgetExecution
from personal_finance.domain.errors import FinanceError, ValidationError
from personal_finance.domain.models import Account, Category, validate_name
from personal_finance.domain.planning_scenarios import (
    BudgetScenario,
    PlanningKind,
    PlanningScenario,
    SavedPlanningScenario,
    planning_kind,
)
from personal_finance.domain.savings_goals import (
    SavingsGoalProjection,
    SavingsGoalScenario,
    project_savings_goal,
)
from personal_finance.presentation.analytics import _label
from personal_finance.presentation.budget import BudgetDialog
from personal_finance.presentation.inline_forms import InlineForms, present_form
from personal_finance.presentation.planning_options import (
    BudgetScenarioOptions,
    SavingsGoalScenarioOptions,
)
from personal_finance.presentation.planning_scenarios import (
    PlanningAction,
    PlanningHistoryDialog,
    PlanningRequest,
    SavedPlanningDialog,
)
from personal_finance.presentation.savings_goals import SavingsGoalDialog

if TYPE_CHECKING:
    from personal_finance.presentation.main_window import MainWindow

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class PlanningError:
    message: str


@dataclass(frozen=True, slots=True)
class PlanningCalculation:
    report: BudgetExecution | SavingsGoalProjection
    accounts: list[Account]
    categories: list[Category]


class PlanningNameDialog(QDialog):
    def __init__(
        self, name: str, parent: MainWindow, *, label: str = "Название бюджета или цели"
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(label)
        layout = QVBoxLayout(self)
        layout.addWidget(_label("Название сохранённых условий в этой книге:"))
        self.name = QLineEdit(name)
        self.name.setMaxLength(120)
        self.name.setAccessibleName(label)
        layout.addWidget(self.name)
        self.feedback = _label("")
        self.feedback.setObjectName("feedbackError")
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        try:
            name = validate_name(self.name.text())
            if len(name.casefold()) > 240:
                raise ValidationError("Сократите название сохранённых условий.")
        except ValidationError as error:
            self.feedback.setText(str(error))
            self.name.setFocus()
            return
        self.name.setText(name)
        self.accept()


class PlanningScenariosController:
    def __init__(
        self,
        window: MainWindow,
        session: VaultSession,
        service: PlanningScenarios,
        panel: InlineForms,
    ) -> None:
        self.window, self.session, self.service, self.panel = window, session, service, panel
        self.kind = PlanningKind.BUDGET
        self.query = ""
        self.show_archived = False
        self.notice = ""

    def current(self) -> bool:
        return self.window.session is self.session and not self.panel.clearing

    def _job(
        self,
        action: Callable[[], T],
        success: Callable[[T], object],
        failure: Callable[[str], object],
        message: str,
    ) -> None:
        if not self.current() or self.window._busy:
            return

        def run() -> T | PlanningError:
            try:
                with self.session.mutex:
                    self.session.ensure_open()
                    return action()
            except FinanceError as error:
                return PlanningError(str(error))
            except Exception:
                return PlanningError("Не удалось завершить действие. Повторите попытку.")

        def applied(value: T | PlanningError) -> None:
            if not self.current():
                return
            if isinstance(value, PlanningError):
                failure(value.message)
            else:
                success(value)

        self.window._job(run, applied, message, defer_success=True)

    def open(self) -> None:
        self._job(
            lambda: self.service.items(include_archived=True),
            self._show,
            self.panel.message,
            "Загрузка бюджетов и целей…",
        )

    def _show(self, records: list[SavedPlanningScenario]) -> None:
        dialog = SavedPlanningDialog(records, self.window, kind=self.kind)
        dialog.search.setText(self.query)
        dialog.archived.setChecked(self.show_archived)
        if self.notice:
            dialog.selection_info.setText(self.notice)
            self.notice = ""

        def completed(code: int) -> None:
            self.query, self.show_archived = dialog.search.text(), dialog.archived.isChecked()
            self.kind = PlanningKind(dialog.kind.currentData())
            if (
                code == QDialog.DialogCode.Accepted
                and dialog.request is not None
                and self.current()
            ):
                self.handle(dialog.request)

        present_form(dialog, self.panel, completed)

    def handle(self, request: PlanningRequest) -> None:
        if not self.current() or self.window._busy:
            return
        self.kind = request.kind
        record, action = request.record, request.action
        if action is PlanningAction.CREATE:
            self.edit(None, kind=request.kind)
            return
        if record is None or record.kind != request.kind:
            return
        if action is PlanningAction.CALCULATE:
            self._job(
                lambda: self._calculate(record.scenario),
                lambda result: self._view(result, record),
                self._failed,
                "Расчёт выбранной версии условий…",
            )
        elif action in (PlanningAction.EDIT, PlanningAction.COPY):
            self.edit(record, kind=record.kind, copy=action is PlanningAction.COPY)
        elif action is PlanningAction.RENAME:
            self.save_name(record.scenario, record, rename=True)
        elif action in (PlanningAction.ARCHIVE, PlanningAction.RESTORE):
            command = str(uuid4())
            self._job(
                lambda: self.service.archive(
                    record.id,
                    record.revision_id,
                    archived=action is PlanningAction.ARCHIVE,
                    command_id=command,
                ),
                self._saved,
                self._failed,
                "Сохранение состояния условий…",
            )
        elif action is PlanningAction.HISTORY:
            self._job(
                lambda: self.service.history(record.id),
                self._history,
                self._failed,
                "Загрузка истории условий…",
            )

    def _failed(self, message: str) -> None:
        self.notice = message
        self.open()

    def _saved(self, result: SavedPlanningScenario) -> None:
        self.kind = result.kind
        self.notice = "Условия сохранены."
        self.open()

    def _calculate(self, scenario: PlanningScenario) -> PlanningCalculation:
        if isinstance(scenario, BudgetScenario):
            ledger = self.session.ledger
            if ledger is None:
                raise ValidationError("Для расчёта бюджета нужен журнал операций.")
            analytics = Analytics(ledger, self.session.mutex, self.session.ensure_open)
            report = analytics.budget(scenario.period, scenario.limit, scenario.scope)
            return PlanningCalculation(
                report,
                self.session.catalog.accounts(),
                self.session.catalog.categories(),
            )
        if isinstance(scenario, SavingsGoalScenario):
            return PlanningCalculation(project_savings_goal(scenario), [], [])
        raise ValidationError("Выберите бюджет или цель накоплений.")

    def _report_dialog(self, value: PlanningCalculation) -> BudgetDialog | SavingsGoalDialog:
        report = value.report
        if isinstance(report, BudgetExecution):
            return BudgetDialog(report, value.accounts, value.categories, self.window)
        return SavingsGoalDialog(report, self.window)

    def _view(self, value: PlanningCalculation, record: SavedPlanningScenario) -> None:
        dialog = self._report_dialog(value)
        layout = dialog.layout()
        assert isinstance(layout, QVBoxLayout)
        text = f"Условия: {record.name}"
        if record.kind is PlanningKind.BUDGET:
            text += " · Расчёт по текущему журналу"
        summary = _label(text)
        summary.setToolTip(f"Версия условий: {record.revision_id}")
        layout.insertWidget(0, summary)
        present_form(dialog, self.panel, lambda _: self.open())

    def _history(self, records: list[SavedPlanningScenario]) -> None:
        dialog = PlanningHistoryDialog(records, self.window)

        def completed(code: int) -> None:
            if code == QDialog.DialogCode.Accepted and dialog.request is not None:
                self.handle(dialog.request)
            else:
                self.open()

        present_form(dialog, self.panel, completed)

    def edit(
        self,
        record: SavedPlanningScenario | None,
        *,
        kind: PlanningKind,
        copy: bool = False,
    ) -> None:
        def ready(catalog: tuple[list[Account], list[Category]]) -> None:
            try:
                if kind is PlanningKind.BUDGET:
                    scenario = record.scenario if record is not None else None
                    if scenario is not None and not isinstance(scenario, BudgetScenario):
                        raise ValidationError("Для этой формы выберите бюджет.")
                    form: BudgetScenarioOptions | SavingsGoalScenarioOptions = (
                        BudgetScenarioOptions(
                            *catalog,
                            self.window,
                            scenario=scenario,
                        )
                    )
                else:
                    scenario_goal = record.scenario if record is not None else None
                    if scenario_goal is not None and not isinstance(
                        scenario_goal, SavingsGoalScenario
                    ):
                        raise ValidationError("Для этой формы выберите цель.")
                    form = SavingsGoalScenarioOptions(self.window, scenario=scenario_goal)
            except ValidationError as error:
                self._failed(str(error))
                return
            self._edit_form(form, record, copy=copy)

        self._job(
            lambda: (self.session.catalog.accounts(), self.session.catalog.categories()),
            ready,
            self._failed,
            "Подготовка условий расчёта…",
        )

    def _edit_form(
        self,
        form: BudgetScenarioOptions | SavingsGoalScenarioOptions,
        record: SavedPlanningScenario | None,
        *,
        copy: bool,
    ) -> None:
        def calculate() -> None:
            scenario = (
                form.scenario_request
                if isinstance(form, BudgetScenarioOptions)
                else form.result_request
            )
            if scenario is None:
                return

            def failure(message: str) -> None:
                form.feedback.setText(message)
                form.feedback.show()

            self._job(
                lambda: self._calculate(scenario),
                lambda result: self._preview(result, scenario, form, record, copy=copy),
                failure,
                "Расчёт условий перед сохранением…",
            )

        form.calculationRequested.connect(calculate)
        present_form(form, self.panel, lambda _: self.open())

    def _preview(
        self,
        value: PlanningCalculation,
        scenario: PlanningScenario,
        form: BudgetScenarioOptions | SavingsGoalScenarioOptions,
        record: SavedPlanningScenario | None,
        *,
        copy: bool,
    ) -> None:
        dialog = self._report_dialog(value)
        buttons = dialog.findChild(QDialogButtonBox)
        assert buttons is not None
        save = buttons.addButton(
            "Сохранить изменения…" if record is not None and not copy else "Сохранить условия…",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        save.setAutoDefault(False)
        requested = False

        def choose_save() -> None:
            nonlocal requested
            requested = True
            dialog.accept()

        save.clicked.connect(choose_save)
        layout = dialog.layout()
        assert isinstance(layout, QVBoxLayout)
        layout.insertWidget(
            0,
            _label(
                "Черновик условий. После закрытия расчёта можно изменить параметры. "
                "Для сохранения нажмите «" + save.text() + "»."
            ),
        )

        def completed(code: int) -> None:
            if code == QDialog.DialogCode.Accepted and requested and self.current():
                self.save_name(scenario, record, copy=copy, form=form)

        present_form(dialog, self.panel, completed)

    def save_name(
        self,
        scenario: PlanningScenario,
        record: SavedPlanningScenario | None,
        *,
        copy: bool = False,
        rename: bool = False,
        form: BudgetScenarioOptions | SavingsGoalScenarioOptions | None = None,
    ) -> None:
        name = "" if record is None else record.name
        if copy:
            name = name[:110] + " · копия"
        dialog = PlanningNameDialog(name, self.window)
        command: str | None = None
        last_name: str | None = None

        def present() -> None:
            if not self.current():
                dialog.deleteLater()
                return
            present_form(dialog, self.panel, completed, dispose=False)

        def completed(code: int) -> None:
            nonlocal command, last_name
            if code != QDialog.DialogCode.Accepted or not self.current():
                dialog.deleteLater()
                if form is None:
                    self.open()
                return
            name = dialog.name.text()
            if command is None or name != last_name:
                command, last_name = str(uuid4()), name
            current_command = command

            def save() -> SavedPlanningScenario:
                if record is not None and rename:
                    return self.service.rename(
                        record.id,
                        record.revision_id,
                        name,
                        command_id=current_command,
                    )
                return self.service.save(
                    name,
                    scenario,
                    identifier=record.id if record is not None and not copy else None,
                    expected_revision_id=(
                        record.revision_id if record is not None and not copy else None
                    ),
                    command_id=current_command,
                )

            def saved(value: SavedPlanningScenario) -> None:
                dialog.deleteLater()
                if form is not None:
                    self.kind = planning_kind(scenario)
                    self.notice = "Условия сохранены."
                    form.reject()
                else:
                    self._saved(value)

            def failed(message: str) -> None:
                dialog.feedback.setText(message)
                present()

            self._job(save, saved, failed, "Сохранение условий без финансовой записи…")

        present()
