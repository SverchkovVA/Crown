"""Сохранённые сценарии: явные действия и обращения к книге через рабочую задачу."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar
from uuid import uuid4

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from personal_finance.application.analytics import Analytics
from personal_finance.application.vault import VaultSession
from personal_finance.domain.deposit_plan_fact import DepositPlanFact
from personal_finance.domain.deposit_scenarios import SavedDepositScenario
from personal_finance.domain.deposits import DepositForecast, DepositScenario, forecast_deposit
from personal_finance.domain.errors import FinanceError, ValidationError
from personal_finance.domain.models import Account, Category
from personal_finance.presentation.analytics import _label
from personal_finance.presentation.analytics_cashflow import _size
from personal_finance.presentation.deposit_plan_fact import (
    DepositPlanFactDialog,
    DepositPlanFactOptions,
)
from personal_finance.presentation.deposit_scenarios import (
    SavedScenariosDialog,
    ScenarioAction,
    ScenarioRequest,
    date_text,
)
from personal_finance.presentation.deposits import DepositForecastDialog, DepositScenarioDialog
from personal_finance.presentation.inline_forms import InlineForms, present_form
from personal_finance.presentation.tables import replace_table

if TYPE_CHECKING:
    from personal_finance.presentation.main_window import MainWindow

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ScenarioError:
    message: str


class ScenarioNameDialog(QDialog):
    def __init__(self, name: str, parent: QDialog | MainWindow) -> None:
        super().__init__(parent)
        self.setWindowTitle("Название сценария вклада")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.addWidget(_label("Название для сохранения условий расчёта в этой книге:"))
        self.name = QLineEdit(name)
        self.name.setMaxLength(120)
        self.name.setAccessibleName("Название сценария вклада")
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
        if not self.name.text().strip():
            self.feedback.setText("Укажите название сценария.")
            return
        self.accept()


class SaveForecastDialog(DepositForecastDialog):
    def __init__(self, report: DepositForecast, parent: MainWindow, *, editing: bool) -> None:
        super().__init__(report, parent, allow_edit=True)
        self.save_requested = False
        buttons = self.findChild(QDialogButtonBox)
        assert buttons is not None
        # В Windows CI addButton вернул QThread; сохраняем созданную QPushButton.
        self.save_button = QPushButton(
            "Сохранить изменения…" if editing else "Сохранить сценарий…", buttons
        )
        buttons.addButton(self.save_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.storage_note.setText(
            "Нажмите «" + self.save_button.text() + "», чтобы сохранить условия в этой книге. "
            "Расчёт не создаёт фактических выплат и не меняет остатки."
        )
        self.save_button.clicked.connect(self._save)

    def _save(self) -> None:
        self.save_requested = True
        self.accept()


class ScenarioHistoryDialog(QDialog):
    def __init__(self, records: list[SavedDepositScenario], parent: MainWindow) -> None:
        super().__init__(parent)
        self.records = tuple(records)
        self.request: ScenarioRequest | None = None
        self.setWindowTitle("История сценария вклада")
        _size(self, 900, 600)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                "Версии неизменяемы. Выбранную версию можно рассчитать или скопировать "
                "в отдельный сценарий. Текущий сценарий при этом не заменяется."
            )
        )
        self.table = QTableView()
        self.table.setAccessibleName("Неизменяемые версии сценария")
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.model = replace_table(
            self.table,
            ["Название", "Сохранено · UTC", "Срок", "Состояние"],
            [
                [
                    v.name,
                    v.created_at,
                    date_text(v.scenario.opened_on) + " — " + date_text(v.scenario.matures_on),
                    "В архиве" if v.is_deleted else "Активный",
                ]
                for v in records
            ],
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.calculate = buttons.addButton("Рассчитать", QDialogButtonBox.ButtonRole.ActionRole)
        self.copy = buttons.addButton("Создать копию", QDialogButtonBox.ButtonRole.ActionRole)
        self.calculate.clicked.connect(lambda: self.choose(ScenarioAction.CALCULATE))
        self.copy.clicked.connect(lambda: self.choose(ScenarioAction.COPY))
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.table.selectionModel().selectionChanged.connect(self._selection)
        self._selection()

    def _selected(self) -> SavedDepositScenario | None:
        rows = self.table.selectionModel().selectedRows()
        if len(rows) != 1 or not 0 <= rows[0].row() < len(self.records):
            return None
        return self.records[rows[0].row()]

    def _selection(self, *unused: object) -> None:
        enabled = self._selected() is not None
        self.calculate.setEnabled(enabled)
        self.copy.setEnabled(enabled)

    def choose(self, action: ScenarioAction) -> None:
        record = self._selected()
        if record is None or action not in (ScenarioAction.CALCULATE, ScenarioAction.COPY):
            return
        self.request = ScenarioRequest(action, record)
        self.accept()


class DepositScenariosController:
    def __init__(
        self, window: MainWindow, session: VaultSession, panel: InlineForms | None = None
    ) -> None:
        self.window, self.session, self.panel = window, session, panel
        assert session.deposit_scenarios is not None
        self.service = session.deposit_scenarios
        self.query = ""
        self.show_archived = False
        self.notice = ""

    def current(self) -> bool:
        return self.window.session is self.session and (
            self.panel is None or not self.panel.clearing
        )

    def _job(
        self,
        action: Callable[[], T],
        success: Callable[[T], object],
        error: Callable[[str], object],
        message: str,
    ) -> None:
        if not self.current() or self.window._busy:
            return

        def run() -> T | ScenarioError:
            try:
                with self.session.mutex:
                    self.session.ensure_open()
                    return action()
            except FinanceError as exc:
                return ScenarioError(str(exc))
            except Exception:
                return ScenarioError(
                    "Не удалось завершить действие. Проверьте условия и повторите попытку."
                )

        def applied(result: T | ScenarioError) -> None:
            if self.current():
                if isinstance(result, ScenarioError):
                    error(result.message)
                else:
                    success(result)

        self.window._job(run, applied, message, defer_success=True)

    def open(self) -> None:
        self._job(
            lambda: self.service.items(include_archived=True),
            self._show,
            self._list_failed,
            "Загрузка сохранённых сценариев…",
        )

    def _list_failed(self, message: str) -> None:
        # Не повторять неуспешное чтение бесконечно.
        if self.panel is not None:
            self.panel.message(message)
            return
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(self.window, "Сценарии не загружены", message)

    def _show(self, records: list[SavedDepositScenario]) -> None:
        dialog = SavedScenariosDialog(records, self.window)
        dialog.search.setText(self.query)
        dialog.archived.setChecked(self.show_archived)
        if self.notice:
            dialog.selection_info.setText(self.notice)
            self.notice = ""

        def completed(code: int) -> None:
            self.query, self.show_archived = dialog.search.text(), dialog.archived.isChecked()
            if (
                code == QDialog.DialogCode.Accepted
                and dialog.request is not None
                and self.current()
            ):
                self.handle(dialog.request)

        present_form(dialog, self.panel, completed)

    def handle(self, request: ScenarioRequest) -> None:
        if not self.current() or self.window._busy:
            return
        action, record = request.action, request.record
        if action is ScenarioAction.CREATE:
            self.edit(None)
            return
        if record is None:
            return
        if action is ScenarioAction.CALCULATE:
            self._job(
                lambda: forecast_deposit(record.scenario),
                self._view,
                self._failed,
                "Расчёт сохранённой версии без изменения книги…",
            )
        elif action is ScenarioAction.PLAN_FACT:
            self._plan_fact(record)
        elif action in (ScenarioAction.EDIT, ScenarioAction.COPY):
            self.edit(record, copy=action is ScenarioAction.COPY)
        elif action is ScenarioAction.RENAME:
            self.save_name(record.scenario, record, rename=True)
        elif action in (ScenarioAction.ARCHIVE, ScenarioAction.RESTORE):
            command = str(uuid4())
            self._job(
                lambda: self.service.archive(
                    record.id,
                    record.revision_id,
                    archived=action is ScenarioAction.ARCHIVE,
                    command_id=command,
                ),
                self._saved,
                self._failed,
                "Сохранение состояния сценария…",
            )
        elif action is ScenarioAction.HISTORY:
            self._job(
                lambda: self.service.history(record.id),
                self._history,
                self._failed,
                "Загрузка истории сценария…",
            )

    def _failed(self, message: str) -> None:
        self.notice = message
        self.open()

    def _saved(self, result: SavedDepositScenario) -> None:
        self.notice = "Сценарий сохранён. Финансовые операции и остатки не изменены."
        self.open()

    def _view(self, report: DepositForecast) -> None:
        dialog = DepositForecastDialog(report, self.window, conditions_saved=True)
        present_form(dialog, self.panel, lambda _: self.open())

    def _plan_fact(self, record: SavedDepositScenario) -> None:
        if self.session.ledger is None:
            return
        catalog = self.session.catalog
        self._job(
            lambda: (catalog.accounts(), catalog.categories()),
            lambda values: self._plan_fact_options(record, *values),
            self._failed,
            "Подготовка сравнения процентов…",
        )

    def _plan_fact_options(
        self, record: SavedDepositScenario, accounts: list[Account], categories: list[Category]
    ) -> None:
        ledger = self.session.ledger
        if ledger is None:
            return
        options = DepositPlanFactOptions(record, accounts, categories, self.window)
        analytics = Analytics(ledger, self.session.mutex, self.session.ensure_open)

        def completed(code: int) -> None:
            selection = options.result_selection
            if code != QDialog.DialogCode.Accepted or selection is None:
                self.open()
                return

            def show(report: DepositPlanFact) -> None:
                dialog = DepositPlanFactDialog(
                    report, record, selection, accounts, categories, self.window
                )
                present_form(dialog, self.panel, lambda _: self.open())

            self._job(
                lambda: analytics.deposit_plan_fact(
                    record.scenario, selection.account, selection.category, selection.through
                ),
                show,
                self._failed,
                "Сравнение прогноза с журналом…",
            )

        present_form(options, self.panel, completed)

    def _history(self, records: list[SavedDepositScenario]) -> None:
        dialog = ScenarioHistoryDialog(records, self.window)

        def completed(code: int) -> None:
            if code == QDialog.DialogCode.Accepted and dialog.request is not None:
                self.handle(dialog.request)
            else:
                self.open()

        present_form(dialog, self.panel, completed)

    def edit(self, record: SavedDepositScenario | None, *, copy: bool = False) -> None:
        dialog = DepositScenarioDialog(self.window)
        try:
            if record is not None:
                dialog.load_scenario(record.scenario)
        except ValidationError as exc:
            dialog.deleteLater()
            self._failed(str(exc))
            return
        self._form(dialog, record, copy=copy)

    def _form(
        self,
        dialog: DepositScenarioDialog,
        record: SavedDepositScenario | None,
        *,
        copy: bool,
    ) -> None:
        if not self.current():
            dialog.deleteLater()
            return
        dialog.result_scenario = None

        def completed(code: int) -> None:
            scenario = dialog.result_scenario
            if code != QDialog.DialogCode.Accepted or scenario is None or not self.current():
                dialog.deleteLater()
                self.open()
                return

            def failed(message: str) -> None:
                dialog.feedback.setText(message)
                dialog.feedback.show()
                self._form(dialog, record, copy=copy)

            self._job(
                lambda: forecast_deposit(scenario),
                lambda result: self._result(dialog, record, result, copy=copy),
                failed,
                "Расчёт сценария перед сохранением…",
            )

        present_form(dialog, self.panel, completed, dispose=False)

    def _result(
        self,
        form: DepositScenarioDialog,
        record: SavedDepositScenario | None,
        report: DepositForecast,
        *,
        copy: bool,
    ) -> None:
        dialog = SaveForecastDialog(report, self.window, editing=record is not None and not copy)

        def completed(code: int) -> None:
            accepted = code == QDialog.DialogCode.Accepted and self.current()
            if accepted and dialog.edit_requested:
                self._form(form, record, copy=copy)
                return
            form.deleteLater()
            if accepted and dialog.save_requested:
                self.save_name(report.scenario, record, copy=copy)
            else:
                self.open()

        present_form(dialog, self.panel, completed)

    def save_name(
        self,
        scenario: DepositScenario,
        record: SavedDepositScenario | None,
        *,
        copy: bool = False,
        rename: bool = False,
    ) -> None:
        name = (
            record.name + " · копия"
            if record is not None and copy
            else (record.name if record is not None else "")
        )
        dialog = ScenarioNameDialog(name, self.window)
        self._name_form(dialog, scenario, record, copy=copy, rename=rename)

    def _name_form(
        self,
        dialog: ScenarioNameDialog,
        scenario: DepositScenario,
        record: SavedDepositScenario | None,
        *,
        copy: bool,
        rename: bool,
    ) -> None:
        if not self.current():
            dialog.deleteLater()
            return

        def completed(code: int) -> None:
            if code != QDialog.DialogCode.Accepted or not self.current():
                dialog.deleteLater()
                self.open()
                return
            name, command = dialog.name.text(), str(uuid4())

            def save() -> SavedDepositScenario:
                if record is not None and rename:
                    return self.service.rename(
                        record.id, record.revision_id, name, command_id=command
                    )
                return self.service.save(
                    name,
                    scenario,
                    identifier=record.id if record is not None and not copy else None,
                    expected_revision_id=(
                        record.revision_id if record is not None and not copy else None
                    ),
                    command_id=command,
                )

            def saved(value: SavedDepositScenario) -> None:
                dialog.deleteLater()
                self._saved(value)

            def failed(message: str) -> None:
                dialog.feedback.setText(message)
                self._name_form(dialog, scenario, record, copy=copy, rename=rename)

            self._job(save, saved, failed, "Сохранение условий сценария без финансовой записи…")

        present_form(dialog, self.panel, completed, dispose=False)
