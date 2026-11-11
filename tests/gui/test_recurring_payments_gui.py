from datetime import date

from PySide6.QtCore import QDate

from personal_finance.domain.recurring_payments import PaymentDirection, RepeatUnit
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.recurring_payments import (
    PaymentCalendarDialog,
    PaymentCalendarOptions,
    ScheduleEditor,
)
from tests.gui.test_planning_scenarios_workflow import current


def test_calendar_uses_drafts_recalculates_and_preserves_the_journal(session, qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert not window.payment_calendar_action.isEnabled()
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    ledger = session.ledger
    before = ledger.operations(), ledger.balances()
    try:
        next(b for b in window.report_buttons if b.text() == "Календарь платежей…").click()
        form = current(window, PaymentCalendarOptions)
        assert not form.calculate.isEnabled()
        form.start.setDate(QDate(2024, 1, 31))
        form.end.setDate(QDate(2024, 4, 30))

        form.add.click()
        editor = current(window, ScheduleEditor)
        editor.apply.click()
        assert editor.feedback.isVisible() and form.schedules == []
        editor.name.setText("Учебная подписка")
        editor.amount.setText("5,01")
        editor.unit.setCurrentIndex(editor.unit.findData(RepeatUnit.MONTHS.value))
        editor.has_last.setChecked(True)
        editor.last.setDate(QDate(2024, 3, 31))
        editor.apply.click()
        assert current(window, PaymentCalendarOptions) is form

        form.add.click()
        editor = current(window, ScheduleEditor)
        editor.name.setText("Учебное поступление")
        editor.amount.setText("18,00")
        editor.direction.setCurrentIndex(editor.direction.findData(PaymentDirection.INCOME.value))
        editor.apply.click()
        assert form.table.model().rowCount() == 2

        form.table.selectRow(1)
        form.edit.click()
        editor = current(window, ScheduleEditor)
        editor.amount.setText("20,00")
        editor.apply.click()

        form.calculate.click()
        qtbot.waitUntil(lambda: not window._busy)
        projection = current(window, PaymentCalendarDialog).forecast
        assert [event.occurs_on for event in projection.events] == [
            date(2024, 1, 31),
            date(2024, 1, 31),
            date(2024, 2, 29),
            date(2024, 3, 31),
        ]
        assert projection.totals[0].net_minor == 497
        window._report_forms.back.click()
        assert current(window, PaymentCalendarOptions) is form
        assert form.table.model().rowCount() == 2

        form.table.selectRow(0)
        form.remove.click()
        assert form.table.model().rowCount() == 1
        assert (ledger.operations(), ledger.balances()) == before
        session.check_integrity()
    finally:
        window._report_forms.clear()
        window.session = None
