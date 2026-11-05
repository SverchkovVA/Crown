from dataclasses import replace

from PySide6.QtCore import QDate, QThread
from PySide6.QtWidgets import QDialogButtonBox, QTabWidget

from personal_finance.application.analytics import Analytics
from personal_finance.domain.ledger import OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.presentation.analytics import OperationSourceDialog
from personal_finance.presentation.deposit_plan_fact import (
    DepositPlanFactDialog,
    DepositPlanFactOptions,
)
from personal_finance.presentation.deposit_scenarios import SavedScenariosDialog, ScenarioAction
from personal_finance.presentation.main_window import MainWindow
from tests.gui.test_analytics_gui import fingerprints
from tests.unit.test_deposit_plan_fact import FIRST, MATURITY, comparison_scenario


def test_saved_scenario_compares_interest_and_opens_snapshot_without_writes(
    qtbot, session, monkeypatch
):
    account = session.catalog.add_account("Счёт для процентов", AccountKind.SAVINGS)
    interest = session.catalog.add_category("Проценты проверочного вклада", CategoryKind.INCOME)
    other = session.catalog.add_category("Другой тестовый доход", CategoryKind.INCOME)
    expense = session.catalog.add_category("Проверочный расход", CategoryKind.EXPENSE)
    record = session.deposit_scenarios.save("Проверочный сценарий", comparison_scenario())
    original = session.ledger.add(
        OperationDraft(
            OperationKind.INCOME,
            FIRST,
            account.id,
            Money(125),
            parts=(OperationPart(interest.id, Money(75)), OperationPart(other.id, Money(50))),
        )
    )
    paid = session.ledger.replace(
        original.id,
        original.revision_id,
        replace(
            original.draft,
            amount=Money(130),
            parts=(OperationPart(interest.id, Money(80)), OperationPart(other.id, Money(50))),
        ),
    )
    before = fingerprints(session)
    worker_calls = []
    method = Analytics.deposit_plan_fact

    def observe(service, *args):
        worker_calls.append(QThread.currentThread() != window.thread())
        return method(service, *args)

    monkeypatch.setattr(Analytics, "deposit_plan_fact", observe)
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    window.show()
    panel = window._report_forms

    def current(kind):
        scroll = panel.stack.currentWidget()
        dialog = scroll.widget() if scroll is not None else None
        assert isinstance(dialog, kind) and dialog.isVisible() and not dialog.isWindow()
        return dialog

    window.report_buttons[-1].click()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    listing = current(SavedScenariosDialog)
    action = listing.buttons[ScenarioAction.PLAN_FACT]
    assert not action.isEnabled()
    listing.table.selectRow(0)
    action.click()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    options = current(DepositPlanFactOptions)
    assert options.category.findData(expense.id) == -1
    options.compare.click()
    assert options.feedback.isVisible() and options.result_selection is None
    options.account.setCurrentIndex(options.account.findData(account.id))
    options.category.setCurrentIndex(options.category.findData(interest.id))
    options.through.setDate(QDate(MATURITY.year, MATURITY.month, MATURITY.day))
    options.compare.click()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)

    dialog = current(DepositPlanFactDialog)
    assert worker_calls == [True]
    assert (
        dialog.report.planned_minor,
        dialog.report.actual_minor,
        dialog.report.difference_minor,
    ) == (200, 80, -120)
    assert dialog.months.model().rowCount() == 2
    assert dialog.report.sources[0].operation.revision_id == paid.revision_id
    dialog.findChild(QTabWidget).setCurrentIndex(1)
    dialog.sources.selectRow(0)
    dialog.open_source.click()
    details = current(OperationSourceDialog)
    assert details.fields["Сумма операции"].text() == "1,30 RUB"
    assert details.fields["Доход в отборе"].text() == "0,80 RUB"
    details.reject()
    assert current(DepositPlanFactDialog) is dialog
    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Close).click()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert current(SavedScenariosDialog).records == (record,)
    assert fingerprints(session) == before
    window.lock_book()
