"""Сохранение и открытие именованных календарей внутри отчётов."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QDialog, QHBoxLayout, QHeaderView, QPushButton, QVBoxLayout

from personal_finance.application.planning_scenarios import PlanningScenarios
from personal_finance.application.vault import VaultSession
from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import RUB
from personal_finance.domain.planning_scenarios import PlanningKind, SavedPlanningScenario
from personal_finance.domain.recurring_payments import (
    PaymentCalendarScenario,
    PaymentForecast,
    forecast_payments,
)
from personal_finance.presentation.analytics import _label
from personal_finance.presentation.inline_forms import InlineForms, present_form
from personal_finance.presentation.planning_scenarios_workflow import PlanningNameDialog
from personal_finance.presentation.recurring_payments import (
    PaymentCalendarDialog,
    PaymentCalendarOptions,
    _table,
)
from personal_finance.presentation.tables import replace_table

if TYPE_CHECKING:
    from personal_finance.presentation.main_window import MainWindow


UPCOMING_DAYS = 14


class SavedCalendarsDialog(QDialog):
    def __init__(self, records: Sequence[SavedPlanningScenario], parent: MainWindow) -> None:
        super().__init__(parent)
        self.setWindowTitle("Сохранённые календари платежей")
        self.records = tuple(
            record for record in records if isinstance(record.scenario, PaymentCalendarScenario)
        )
        self.selected: SavedPlanningScenario | None = None
        self.show_upcoming = False
        layout = QVBoxLayout(self)
        self.table = _table("Сохранённые календари платежей")
        replace_table(
            self.table,
            ["Календарь", "Платежи в расписании"],
            [
                [record.name, str(len(record.scenario.schedules))]
                for record in self.records
                if isinstance(record.scenario, PaymentCalendarScenario)
            ],
            right_columns=(1,),
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        if not self.records:
            layout.addWidget(_label("Сохранённых календарей пока нет."))
        layout.addWidget(self.table, 1)
        self.open_button = QPushButton("Открыть календарь")
        self.open_button.setObjectName("primary")
        self.upcoming_button = QPushButton(f"Ближайшие {UPCOMING_DAYS} дней")
        self.table.selectionModel().selectionChanged.connect(self._update_actions)
        self.open_button.clicked.connect(lambda: self._select(upcoming=False))
        self.upcoming_button.clicked.connect(lambda: self._select(upcoming=True))
        self._update_actions()
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(self.upcoming_button)
        actions.addWidget(self.open_button)
        layout.addLayout(actions)

    def _update_actions(self) -> None:
        selected = bool(self.table.selectionModel().selectedRows())
        self.open_button.setEnabled(selected)
        self.upcoming_button.setEnabled(selected)

    def _select(self, *, upcoming: bool) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self.selected = self.records[rows[0].row()]
            self.show_upcoming = upcoming
            self.accept()


class CalendarStorage:
    def __init__(
        self,
        window: MainWindow,
        session: VaultSession,
        panel: InlineForms,
        form: PaymentCalendarOptions,
        service: PlanningScenarios,
        record: SavedPlanningScenario | None,
        open_record: Callable[[SavedPlanningScenario], None],
    ) -> None:
        self.window, self.session, self.panel = window, session, panel
        self.form, self.service, self.record = form, service, record
        self.open_record = open_record
        self.form.open_saved.clicked.connect(lambda: self.pick())
        self.form.save_calendar.clicked.connect(lambda: self.save())

    def current(self) -> bool:
        return self.window.session is self.session and not self.panel.clearing

    def pick(self) -> None:
        if not self.current() or self.window._busy:
            return

        def load() -> list[SavedPlanningScenario]:
            with self.session.mutex:
                self.session.ensure_open()
                return self.service.items(kind=PlanningKind.PAYMENT_CALENDAR)

        def show(records: list[SavedPlanningScenario]) -> None:
            if not self.current():
                return
            dialog = SavedCalendarsDialog(records, self.window)

            def completed(code: int) -> None:
                if (
                    code != QDialog.DialogCode.Accepted
                    or not self.current()
                    or dialog.selected is None
                ):
                    return
                selected = dialog.selected
                value = selected.scenario
                if not isinstance(value, PaymentCalendarScenario):
                    return
                if dialog.show_upcoming:
                    self._upcoming(selected.name, value)
                    return
                if any(schedule.amount.currency != RUB for schedule in value.schedules):
                    self.panel.message("Этот календарь содержит платежи в валюте, отличной от RUB.")
                    return
                self.open_record(selected)

            present_form(dialog, self.panel, completed)

        self.window._panel_job(self.panel, load, show, "Загрузка сохранённых календарей…")

    def _upcoming(self, name: str, calendar: PaymentCalendarScenario) -> None:
        today = date.today()
        horizon = min(UPCOMING_DAYS - 1, (date.max - today).days)
        period = ReportPeriod(today, today + timedelta(days=horizon))

        def show(forecast: PaymentForecast) -> None:
            if self.current():
                view = PaymentCalendarDialog(forecast, self.window, calendar_name=name)
                view.setWindowTitle("Ближайшие платежи")
                present_form(view, self.panel)

        self.window._panel_job(
            self.panel,
            lambda: forecast_payments(calendar.schedules, period),
            show,
            "Расчёт ближайших платежей…",
        )

    def save(self) -> None:
        if not self.current() or self.window._busy:
            return
        try:
            scenario = PaymentCalendarScenario(tuple(self.form.schedules))
        except ValidationError as error:
            self.panel.message(str(error))
            return
        prior = self.record
        dialog = PlanningNameDialog(
            prior.name if prior is not None else "", self.window, label="Название календаря"
        )

        def completed(code: int) -> None:
            if code != QDialog.DialogCode.Accepted or not self.current():
                return
            name = dialog.name.text()
            if prior is not None and prior.name == name and prior.scenario == scenario:
                self.panel.message("Календарь уже сохранён.")
                return

            def persist() -> SavedPlanningScenario:
                with self.session.mutex:
                    self.session.ensure_open()
                    return self.service.save(
                        name,
                        scenario,
                        identifier=None if prior is None else prior.id,
                        expected_revision_id=None if prior is None else prior.revision_id,
                    )

            def saved(record: SavedPlanningScenario) -> None:
                if self.current():
                    self.record = record
                    self.form.set_saved_name(record.name)
                    self.panel.message("Календарь сохранён в книге.")

            self.window._panel_job(self.panel, persist, saved, "Сохранение календаря…")

        present_form(dialog, self.panel, completed)
