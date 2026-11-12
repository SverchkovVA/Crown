from datetime import date, timedelta
from uuid import uuid4

from PySide6.QtCore import QSettings

from personal_finance.application.vault import open_vault
from personal_finance.domain.models import Money
from personal_finance.domain.recurring_payments import (
    PaymentCalendarScenario,
    PaymentDirection,
    PaymentSchedule,
)
from personal_finance.infrastructure.payment_reminder_preferences import PaymentReminderPreferences
from personal_finance.presentation import calendar_reminders
from personal_finance.presentation.calendar_storage import SavedCalendarsDialog
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.recurring_payments import PaymentCalendarDialog
from tests.conftest import PASSWORD
from tests.gui.test_analytics_gui import fingerprints
from tests.gui.test_planning_scenarios_workflow import current
from tests.gui.test_saved_calendars_gui import enter_calendar

DAY = date(2026, 11, 12)


def preferences(tmp_path):
    return PaymentReminderPreferences(
        QSettings(str(tmp_path / "reminders.ini"), QSettings.Format.IniFormat)
    )


def open_picker(window, qtbot):
    options = enter_calendar(window)
    options.open_saved.click()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    return current(window, SavedCalendarsDialog)


def test_only_one_replica_calendar_is_remembered_locally(tmp_path):
    replica, other, first, second = (str(uuid4()) for _ in range(4))
    store = preferences(tmp_path)
    store.select(replica, first)
    store.select(replica, second)
    assert preferences(tmp_path).selection(replica) == second
    assert preferences(tmp_path).selection(other) is None
    raw = (tmp_path / "reminders.ini").read_text(encoding="utf-8")
    assert second in raw
    assert replica not in raw and first not in raw
    store.select(replica, None)
    assert preferences(tmp_path).selection(replica) is None


def test_reminder_comes_from_selected_calendar_and_is_hidden_after_close(
    session, qtbot, tmp_path, monkeypatch
):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return DAY

    monkeypatch.setattr(calendar_reminders, "date", FixedDate)
    service = session.planning_scenarios
    assert service is not None
    expense = PaymentDirection.EXPENSE
    chosen = service.save(
        "Учебный календарь",
        PaymentCalendarScenario(
            tuple(
                PaymentSchedule(title, Money(100), direction, DAY + timedelta(days=offset))
                for title, offset, direction in (
                    ("Вчера", -1, expense),
                    ("Сегодня", 0, expense),
                    ("Последний день", 6, expense),
                    ("Позже", 7, expense),
                    ("Ожидаемое поступление", 0, PaymentDirection.INCOME),
                )
            )
        ),
    )
    service.save(
        "Другой календарь",
        PaymentCalendarScenario((PaymentSchedule("Другой", Money(100), expense, DAY),)),
    )
    before = fingerprints(session)
    window = MainWindow(payment_reminders=preferences(tmp_path))
    qtbot.addWidget(window)
    window.show()
    window.activate_session(session)
    reminder = window._calendar_reminders
    assert reminder.button.isHidden()
    picker = open_picker(window, qtbot)
    assert not picker.reminders.isEnabled()
    picker.table.selectRow(picker.records.index(chosen))
    picker.reminders.click()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert picker.reminders.isChecked()
    assert reminder.selection(session) == chosen.id
    window.navigation.setCurrentRow(0)
    assert reminder.button.isVisible() and reminder.button.text().endswith("2")
    reminder.button.click()
    report = window._journal_forms.stack.currentWidget().widget()
    assert isinstance(report, PaymentCalendarDialog)
    assert report.forecast.period.start == DAY
    assert report.forecast.period.end == DAY + timedelta(days=6)
    assert [event.schedule.name for event in report.forecast.events] == [
        "Сегодня",
        "Последний день",
    ]
    assert fingerprints(session) == before
    root = session.root
    window.lock_book()
    assert reminder.button.isHidden() and not reminder.timer.isActive()
    reopened = open_vault(root, PASSWORD)
    try:
        window.activate_session(reopened)
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        assert reminder.button.isVisible()
        picker = open_picker(window, qtbot)
        picker.table.selectRow(picker.records.index(chosen))
        assert picker.reminders.isChecked()
        picker.reminders.click()
        assert reminder.button.isHidden() and reminder.selection(reopened) is None
    finally:
        window.lock_book()


def test_reminder_rechecks_the_date_and_clears_when_last_payment_passes(
    session, qtbot, tmp_path, monkeypatch
):
    clock = [DAY]

    class MutableDate(date):
        @classmethod
        def today(cls):
            return clock[0]

    monkeypatch.setattr(calendar_reminders, "date", MutableDate)
    service = session.planning_scenarios
    assert service is not None
    selected = service.save(
        "Разовый платёж",
        PaymentCalendarScenario(
            (PaymentSchedule("Учебный расход", Money(100), PaymentDirection.EXPENSE, DAY),)
        ),
    )
    store = preferences(tmp_path)
    store.select(session.replica_id, selected.id)
    window = MainWindow(payment_reminders=store)
    qtbot.addWidget(window)
    window.show()
    window.activate_session(session)
    reminder = window._calendar_reminders
    try:
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        assert reminder.button.isVisible() and reminder.timer.isActive()
        clock[0] = DAY + timedelta(days=1)
        reminder.timer.timeout.emit()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        assert reminder.button.isHidden()
        assert reminder.selection(session) == selected.id
        assert service.history(selected.id) == [selected]
    finally:
        window.lock_book()
