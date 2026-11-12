"""Напоминания по сохранённому расписанию для открытой книги."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Slot
from PySide6.QtWidgets import QPushButton

from personal_finance.application.vault import VaultSession
from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.recurring_payments import (
    PaymentCalendarScenario,
    PaymentDirection,
    PaymentForecast,
    forecast_payments,
)
from personal_finance.infrastructure.payment_reminder_preferences import PaymentReminderPreferences
from personal_finance.presentation.inline_forms import present_form
from personal_finance.presentation.recurring_payments import PaymentCalendarDialog

if TYPE_CHECKING:
    from personal_finance.presentation.main_window import MainWindow


REMINDER_DAYS = 7


class CalendarReminders(QObject):
    def __init__(self, window: MainWindow, preferences: PaymentReminderPreferences) -> None:
        super().__init__(window)
        self.window = window
        self.preferences = preferences
        self.button = QPushButton()
        self.button.setToolTip(
            "Запланированные расходы на семь дней по выбранному календарю. "
            "Фактические оплаты сверяются в прогнозе счёта."
        )
        self.button.hide()
        self.button.clicked.connect(self._open)
        self.timer = QTimer(self)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self.refresh)
        self._request: tuple[VaultSession, str, date] | None = None
        self._result: tuple[str, PaymentForecast] | None = None

    def selection(self, session: VaultSession) -> str | None:
        return self.preferences.selection(session.replica_id)

    def select(self, session: VaultSession, calendar_id: str | None) -> None:
        if self.window.session is not session:
            return
        self.preferences.select(session.replica_id, calendar_id)
        self.clear()
        self.refresh()

    def invalidate(self, session: VaultSession, calendar_id: str) -> None:
        if self.window.session is session and self.selection(session) == calendar_id:
            self.clear()

    def clear(self) -> None:
        self.timer.stop()
        self._request = None
        self._result = None
        self.button.hide()

    @Slot()
    def refresh(self) -> None:
        session = self.window.session
        if session is None or not getattr(session, "supports_payment_calendars", False):
            self.clear()
            return
        service = session.planning_scenarios
        selected = self.selection(session)
        if service is None or selected is None:
            self.clear()
            return
        if not self.timer.isActive():
            self.timer.start()
        today = date.today()
        request = session, selected, today
        if self.window._busy or self._request == request:
            return
        self._request = request
        self._result = None
        self.button.hide()
        horizon = min(REMINDER_DAYS - 1, (date.max - today).days)
        period = ReportPeriod(today, today + timedelta(days=horizon))

        def load() -> tuple[str, PaymentForecast] | None:
            with session.mutex:
                session.ensure_open()
                try:
                    record = service.get(selected)
                except ValidationError:
                    return None
            scenario = record.scenario
            if record.is_deleted or not isinstance(scenario, PaymentCalendarScenario):
                return None
            expenses = tuple(
                schedule
                for schedule in scenario.schedules
                if schedule.direction is PaymentDirection.EXPENSE
            )
            forecast = forecast_payments(expenses, period)
            return (record.name, forecast) if forecast.events else None

        def show(result: tuple[str, PaymentForecast] | None) -> None:
            if self.window.session is not session or self._request != request:
                return
            self._result = result
            if result is not None:
                count = len(result[1].events)
                self.button.setText(f"Платежи по календарю · {count}")
                self.button.show()

        def failed(_message: str) -> None:
            if self.window.session is session and self._request == request:
                self.button.setText("Повторить проверку календаря")
                self.button.show()

        self.window._job(
            load, show, "Проверка предстоящих платежей…", defer_success=True, on_error=failed
        )

    @Slot()
    def _open(self) -> None:
        if self.window._busy or self.window.session is None or self._request is None:
            return
        if self._result is None or self._request[2] != date.today():
            self._request = None
            self.refresh()
            return
        name, forecast = self._result
        dialog = PaymentCalendarDialog(forecast, self.window, calendar_name=name)
        dialog.setWindowTitle("Платежи по календарю")
        present_form(dialog, self.window._journal_forms)
