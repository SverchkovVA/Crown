from contextlib import closing

from personal_finance.application import vault
from personal_finance.domain.planning_scenarios import PlanningKind
from personal_finance.presentation.calendar_storage import SavedCalendarsDialog
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.planning_scenarios_workflow import PlanningNameDialog
from personal_finance.presentation.recurring_payments import PaymentCalendarOptions, ScheduleEditor
from tests.conftest import PASSWORD
from tests.gui.test_planning_scenarios_workflow import current
from tests.integration.test_deposit_scenario_upgrade import create_at


def enter_calendar(window):
    window.navigation.setCurrentRow(2)
    window.payment_calendar_action.trigger()
    return current(window, PaymentCalendarOptions)


def add_payment(window, options, title, amount):
    options.add.click()
    editor = current(window, ScheduleEditor)
    editor.name.setText(title)
    editor.amount.setText(amount)
    editor.apply.click()
    assert current(window, PaymentCalendarOptions) is options


def test_save_open_and_update_calendar_via_embedded_forms(tmp_path, monkeypatch, qtbot):
    root = tmp_path / "calendar.pfvault"
    session, _ = create_at(monkeypatch, root, "0016")
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.activate_session(session)
    try:
        before = session.ledger.operations(), session.ledger.balances()
        options = enter_calendar(window)
        assert options.open_saved.isVisible() and not options.save_calendar.isEnabled()
        options.open_saved.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        empty = current(window, SavedCalendarsDialog)
        assert not empty.open_button.isEnabled()
        empty.reject()

        add_payment(window, options, "Учебная подписка", "120,01")
        options.save_calendar.click()
        dialog = current(window, PlanningNameDialog)
        assert dialog.windowTitle() == "Название календаря"
        dialog._accept()
        assert current(window, PlanningNameDialog) is dialog and dialog.feedback.text()
        dialog.name.setText("Проверочное расписание")
        dialog._accept()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        (first,) = session.planning_scenarios.items(kind=PlanningKind.PAYMENT_CALENDAR)
        assert first.name == "Проверочное расписание"
        assert first.scenario.schedules[0].amount.minor == 12001

        add_payment(window, options, "Оставить в отдельном черновике", "3,00")
        options.open_saved.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        picker = current(window, SavedCalendarsDialog)
        picker.table.selectRow(0)
        picker.open_button.click()
        loaded = current(window, PaymentCalendarOptions)
        assert loaded is not options
        assert loaded.schedules == list(first.scenario.schedules)
        assert len(options.schedules) == 2
        assert loaded.storage_title.text() == first.name

        loaded.table.selectRow(0)
        loaded.edit.click()
        editor = current(window, ScheduleEditor)
        editor.amount.setText("125,02")
        editor.apply.click()
        loaded.save_calendar.click()
        current(window, PlanningNameDialog)._accept()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        (second,) = session.planning_scenarios.items(kind=PlanningKind.PAYMENT_CALENDAR)
        assert second.id == first.id and second.revision_id != first.revision_id
        assert second.scenario.schedules[0].amount.minor == 12502
        assert (session.ledger.operations(), session.ledger.balances()) == before
        window.lock_book()
        assert window.session is None
        with closing(vault.open_vault(root, PASSWORD)) as reopened:
            assert reopened.planning_scenarios.history(first.id) == [first, second]
            reopened.check_integrity()
    finally:
        window._report_forms.clear()
        window.session = None
        session.close()


def test_calendar_in_default_0015_keeps_existing_draft_workflow(qtbot, session):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.activate_session(session)
    try:
        options = enter_calendar(window)
        assert not options.open_saved.isVisible() and not options.save_calendar.isVisible()
        add_payment(window, options, "Только черновик", "6,50")
        assert options.calculate.isEnabled()
        assert session.planning_scenarios.items(kind=PlanningKind.PAYMENT_CALENDAR) == []
    finally:
        window._report_forms.clear()
        window.session = None
