from datetime import date

from PySide6.QtCore import QDate

from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.presentation.balance_forecast import BalanceForecastDialog
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.recurring_payments import PaymentCalendarOptions, ScheduleEditor
from tests.gui.test_analytics_gui import fingerprints
from tests.gui.test_planning_scenarios_workflow import current


def test_forecast_keeps_booked_and_planned_separate_and_does_not_change_the_book(session, qtbot):
    account = session.catalog.add_account("Проверочный счёт", AccountKind.CASH)
    other = session.catalog.add_account("Другой счёт", AccountKind.CASH)
    category = session.catalog.add_category("Проверочная покупка", CategoryKind.EXPENSE)
    ledger = session.ledger
    ledger.add(OperationDraft(OperationKind.OPENING, date(2026, 1, 30), account.id, Money(10000)))
    ledger.add(OperationDraft(OperationKind.OPENING, date(2026, 1, 30), other.id, Money(50000)))
    booked = ledger.add(
        OperationDraft(
            OperationKind.EXPENSE,
            date(2026, 1, 31),
            account.id,
            Money(2000),
            category.id,
            note="Проверочная оплата",
        )
    )
    before = fingerprints(session)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    try:
        next(b for b in window.report_buttons if b.text() == "Календарь платежей…").click()
        options = current(window, PaymentCalendarOptions)
        options.start.setDate(QDate(2026, 1, 31))
        options.end.setDate(QDate(2026, 2, 28))
        options.account.setCurrentIndex(options.account.findData(account.id))
        assert options.balance_hint.isVisible() and options.calculate.isEnabled()
        options.add.click()
        editor = current(window, ScheduleEditor)
        editor.name.setText("Планируемый расход")
        editor.amount.setText("10,00")
        editor.apply.click()
        options.calculate.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        result = current(window, BalanceForecastDialog)
        assert result.forecast.opening_minor == 10000 and result.forecast.closing_minor == 7000
        assert result.forecast.booked[0].operation.revision_id == booked.revision_id
        assert [result.days.model().index(0, column).data() for column in range(1, 4)] == [
            "−20,00",
            "−10,00",
            "70,00",
        ]
        assert [result.movements.model().index(row, 1).data() for row in range(2)] == [
            "Журнал",
            "Ожидается",
        ]
        window._report_forms.back.click()
        assert current(window, PaymentCalendarOptions) is options
        assert options.account.currentData() == account.id and len(options.schedules) == 1
        assert fingerprints(session) == before
    finally:
        window._report_forms.clear()
        window.session = None


def test_matching_requires_explicit_confirmation_and_changes_only_the_projection(session, qtbot):
    account = session.catalog.add_account("Проверочный счёт", AccountKind.CASH)
    category = session.catalog.add_category("Проверочная подписка", CategoryKind.EXPENSE)
    ledger = session.ledger
    ledger.add(OperationDraft(OperationKind.OPENING, date(2026, 1, 30), account.id, Money(10000)))
    for note in ("Оплата подписки", "Другая оплата на ту же сумму"):
        ledger.add(
            OperationDraft(
                OperationKind.EXPENSE,
                date(2026, 1, 31),
                account.id,
                Money(2000),
                category.id,
                note=note,
            )
        )
    before = fingerprints(session)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    try:
        window.payment_calendar_action.trigger()
        options = current(window, PaymentCalendarOptions)
        options.start.setDate(QDate(2026, 1, 31))
        options.end.setDate(QDate(2026, 1, 31))
        options.account.setCurrentIndex(options.account.findData(account.id))
        options.add.click()
        editor = current(window, ScheduleEditor)
        editor.name.setText("Ожидаемая подписка")
        editor.amount.setText("20,00")
        editor.apply.click()
        options.calculate.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        result = current(window, BalanceForecastDialog)
        assert result.forecast.closing_minor == 4000
        result.reserve.amount.setText("-1,00")
        result.reserve.check.click()
        assert result.reserve.error.isVisible()
        result.reserve.amount.setText("50,00")
        result.reserve.check.click()
        assert "Первый день ниже резерва" in result.reserve.result.text()
        assert "10,00 RUB" in result.reserve.result.text()
        matching = result.matching
        assert matching is not None and not matching.confirm.isEnabled()
        matching.table.selectRow(0)
        assert matching.journal.count() == 3 and not matching.confirm.isEnabled()
        row = next(
            i for i in range(1, 3) if matching.journal.itemText(i).startswith("Оплата подписки")
        )
        matching.journal.setCurrentIndex(row)
        assert result.forecast.closing_minor == 4000
        matching.confirm.click()
        assert result.forecast.closing_minor == 6000
        assert result.reserve.result.text().startswith("Резерв выдержан.")
        assert result.days.model().index(0, 2).data() == "0,00"
        assert matching.table.model().index(0, 3).data() == "Учтён в журнале"
        matching.undo.click()
        assert result.forecast.closing_minor == 4000 and not result.forecast.matches
        assert "10,00 RUB" in result.reserve.result.text()
        assert fingerprints(session) == before
    finally:
        window._report_forms.clear()
        window.session = None
