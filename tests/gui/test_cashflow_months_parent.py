from datetime import date

from PySide6.QtWidgets import QDialog

import personal_finance.presentation.analytics_cashflow as module
from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.analytics_cashflow import (
    CashSourceKind,
    cash_flow_report,
    cash_flow_sources,
)
from personal_finance.domain.models import RUB, Account, AccountKind
from tests.unit.test_analytics import A, B
from tests.unit.test_analytics_cashflow import snapshot


def example():
    return cash_flow_report(snapshot(), ReportPeriod(date(2026, 9, 15), date(2026, 12, 15)))


def test_monthly_tab_has_own_selection_and_sources_without_total_fallback(qtbot, monkeypatch):
    value = example()
    accounts = [
        Account(A, "Вымышленный основной", AccountKind.CASH, None),
        Account(B, "Вымышленный резерв", AccountKind.CASH, None),
    ]
    dialog = module.CashFlowDialog(value, accounts)
    qtbot.addWidget(dialog)
    seen = []

    def show(child):
        seen.append(child.lines)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(module.CashFlowSourcesDialog, "exec", show)
    dialog.tabs.setCurrentIndex(2)
    assert dialog._selected() is None
    assert all(b.isHidden() and not b.isEnabled() for b in dialog.source_buttons.values())
    dialog.show_sources(CashSourceKind.THROUGH_END)
    assert not seen
    dialog.months.table.selectRow(1)
    dialog.months.source_buttons[CashSourceKind.PERIOD].click()
    assert len(seen) == 1 and sum(v.minor for v in seen[0]) == 44900
    dialog.tabs.setCurrentIndex(0)
    dialog.total_table.clearSelection()
    assert all(not b.isEnabled() for b in dialog.source_buttons.values())
    dialog.show_sources(CashSourceKind.THROUGH_END)
    assert len(seen) == 1
    dialog.reject()
    assert dialog.months._disposed


def test_source_dialog_cleared_selection_opens_no_old_operation(qtbot, monkeypatch):
    lines = cash_flow_sources(example(), RUB, CashSourceKind.THROUGH_END)
    dialog = module.CashFlowSourcesDialog(lines, RUB, "Вымышленные источники", {}, {})
    qtbot.addWidget(dialog)
    seen = []
    monkeypatch.setattr(module.OperationSourceDialog, "exec", lambda child: seen.append(child))
    dialog.table.clearSelection()
    assert not dialog.details_button.isEnabled()
    dialog.show_details()
    assert not seen
    dialog.reject()


def test_monthly_account_filter_keeps_incoming_transfer_and_source_subset(qtbot, monkeypatch):
    dialog = module.CashFlowDialog(
        example(),
        [
            Account(A, "Первый", AccountKind.CASH, None),
            Account(B, "Второй", AccountKind.CASH, None),
        ],
    )
    qtbot.addWidget(dialog)
    seen = []
    monkeypatch.setattr(
        module.CashFlowSourcesDialog,
        "exec",
        lambda child: seen.append(child.lines) or QDialog.DialogCode.Rejected,
    )
    dialog.tabs.setCurrentIndex(2)
    dialog.months.account.setCurrentIndex(dialog.months.account.findData(B))
    dialog.months.table.selectRow(1)
    dialog.months.show_sources(CashSourceKind.THROUGH_END)
    assert len(seen) == 1 and all(v.account_id == B for v in seen[0])
    assert sum(v.minor for v in seen[0]) == 45000
    dialog.reject()


def test_monthly_tab_scrolls_in_compact_window(qtbot):
    from personal_finance.presentation.main_window import STYLE

    dialog = module.CashFlowDialog(example(), [])
    qtbot.addWidget(dialog)
    dialog.setStyleSheet(STYLE)
    dialog.resize(850, 450)
    dialog.show()
    dialog.tabs.setCurrentIndex(2)
    qtbot.waitUntil(lambda: dialog.isVisible())
    assert dialog.height() <= 450
    button = dialog.months.source_buttons[CashSourceKind.THROUGH_END]
    dialog.months_scroll.ensureWidgetVisible(button)
    point = button.mapTo(dialog.months_scroll.viewport(), button.rect().center())
    assert dialog.months_scroll.viewport().rect().contains(point)
    dialog.reject()
