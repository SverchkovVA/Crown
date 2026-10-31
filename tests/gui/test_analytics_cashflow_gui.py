from dataclasses import replace
from uuid import uuid4

import pytest
from PySide6.QtCore import QDate, QModelIndex, Qt
from PySide6.QtWidgets import QDialog

from personal_finance.domain.analytics import ReportScope
from personal_finance.domain.analytics_cashflow import CashSourceKind, cash_flow_report
from personal_finance.domain.ledger import OperationKind
from personal_finance.domain.models import Account, AccountKind, Currency, Institution, Money
from personal_finance.presentation.analytics import OperationSourceDialog
from personal_finance.presentation.analytics_cashflow import (
    CashFlowDialog,
    CashFlowPeriodDialog,
    CashFlowSourcesDialog,
)
from tests.unit.test_analytics import A, B, operation
from tests.unit.test_analytics_cashflow import PERIOD, snapshot


def accounts():
    return [
        Account(A, "Первый", AccountKind.CASH, None),
        Account(B, "Второй", AccountKind.CASH, None),
    ]


def test_period_hides_categories_and_preserves_empty_bank_scope(qtbot):
    bank = Institution(str(uuid4()), "Без счетов")
    dialog = CashFlowPeriodDialog(accounts=accounts(), institutions=[bank])
    qtbot.addWidget(dialog)
    assert not dialog.form.isRowVisible(dialog.category) and not dialog.form.isRowVisible(
        dialog.include_children
    )
    dialog.bank.setCurrentIndex(dialog.bank.findData(bank.id))
    dialog._accept()
    assert dialog.result_scope == ReportScope(account_ids=frozenset())


def test_period_error_keeps_entered_dates_and_account(qtbot):
    dialog = CashFlowPeriodDialog(accounts=accounts())
    qtbot.addWidget(dialog)
    dialog.account.setCurrentIndex(dialog.account.findData(A))
    dialog.start.setDate(QDate(2026, 10, 31))
    dialog.end.setDate(QDate(2026, 10, 1))
    dialog._accept()
    assert (
        dialog.result_period is None
        and dialog.result_scope is None
        and not dialog.feedback.isHidden()
    )
    assert dialog.account.currentData() == A and dialog.start.date() == QDate(2026, 10, 31)


def test_totals_are_exact_and_sources_use_selected_currency_snapshot(qtbot, monkeypatch):
    report = cash_flow_report(snapshot(), PERIOD)
    dialog = CashFlowDialog(report, accounts())
    qtbot.addWidget(dialog)
    model = dialog.total_table.model()
    assert [model.data(model.index(0, c)) for c in range(7)] == [
        "RUB",
        "1 000,00",
        "100,00",
        "850,00",
        "501,00",
        "449,00",
        "1 449,00",
    ]
    seen = []
    monkeypatch.setattr(
        CashFlowSourcesDialog,
        "exec",
        lambda child: seen.append(child.lines) or QDialog.DialogCode.Rejected,
    )
    dialog.show_sources(CashSourceKind.PERIOD)
    assert sum(v.minor for v in seen[0]) == 44900 and len(seen[0]) == 6


def test_account_sources_do_not_expand_and_invalid_selection_opens_nothing(qtbot, monkeypatch):
    report = cash_flow_report(snapshot(), PERIOD)
    dialog = CashFlowDialog(report, accounts())
    qtbot.addWidget(dialog)
    dialog.tabs.setCurrentIndex(1)
    row = next(i for i, v in enumerate(dialog.positions) if v.account_id == B)
    dialog.account_table.selectRow(row)
    assert not dialog.source_buttons[CashSourceKind.BEFORE].isEnabled()
    seen = []
    monkeypatch.setattr(
        CashFlowSourcesDialog,
        "exec",
        lambda child: seen.append(child.lines) or QDialog.DialogCode.Rejected,
    )
    dialog.show_sources(CashSourceKind.THROUGH_END)
    assert (
        len(seen) == 1
        and sum(v.minor for v in seen[0]) == 45000
        and all(v.account_id == B for v in seen[0])
    )
    dialog.account_table.clearSelection()
    dialog.account_table.setCurrentIndex(QModelIndex())
    dialog._update_actions()
    assert not any(v.isEnabled() for v in dialog.source_buttons.values())
    dialog.show_sources(CashSourceKind.PERIOD)
    assert len(seen) == 1


def test_empty_scope_has_no_fallback_and_no_sources(qtbot):
    report = cash_flow_report(snapshot(), PERIOD, ReportScope(account_ids=frozenset()))
    dialog = CashFlowDialog(report, accounts())
    qtbot.addWidget(dialog)
    assert (
        dialog.total_model.rowCount() == dialog.account_model.rowCount() == 0
        and not dialog.empty_label.isHidden()
    )
    assert not any(v.isEnabled() for v in dialog.source_buttons.values())


def test_movement_details_do_not_present_transfers_as_income(qtbot, monkeypatch):
    report = cash_flow_report(snapshot(), PERIOD)
    line = next(
        v
        for v in report.movements
        if v.operation.draft.kind == OperationKind.TRANSFER and v.account_id == A
    )
    names = {A: "<b>Первый</b>", B: "Второй"}
    dialog = CashFlowSourcesDialog((line,), line.currency, "Проверка", names, {})
    qtbot.addWidget(dialog)
    seen = []

    def show(child):
        assert child.flow is line and child.fields["Счёт движения"].text() == "<b>Первый</b>"
        assert child.fields["Счёт движения"].textFormat() == Qt.TextFormat.PlainText
        assert child.fields["Изменение остатка"].text() == "−301,00 RUB"
        assert "Доход в отборе" not in child.fields and "Расход в отборе" not in child.fields
        seen.append(child.flow.operation.revision_id)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(OperationSourceDialog, "exec", show)
    dialog.show_details()
    assert seen == [line.operation.revision_id]
    assert "&lt;b&gt;" in dialog.model.item(0, 2).toolTip()


@pytest.mark.parametrize("code,exponent,expected", [("USD", 2, "2,00"), ("JPY", 0, "200")])
def test_currency_row_keeps_its_precision_and_never_opens_other_currency(
    qtbot, monkeypatch, code, exponent, expected
):
    currency = Currency(code, exponent)
    foreign = operation(OperationKind.INCOME, 200, currency=currency, fee=Money(0, currency))
    foreign = replace(foreign, draft=replace(foreign.draft, account_id=B))
    report = cash_flow_report([operation(OperationKind.INCOME, 100), foreign], PERIOD)
    dialog = CashFlowDialog(report, accounts())
    qtbot.addWidget(dialog)
    row = next(i for i, v in enumerate(report.totals) if v.currency == currency)
    dialog.total_table.selectRow(row)
    assert dialog.total_model.data(dialog.total_model.index(row, 6)) == expected
    seen = []
    monkeypatch.setattr(
        CashFlowSourcesDialog,
        "exec",
        lambda child: seen.append(child) or QDialog.DialogCode.Rejected,
    )
    dialog.show_sources(CashSourceKind.PERIOD)
    assert (
        len(seen) == 1
        and seen[0].currency == currency
        and sum(v.minor for v in seen[0].lines) == 200
    )
    assert all(v.currency == currency for v in seen[0].lines)


def test_transfer_fee_is_shown_only_on_charged_account_and_full_operation_stays_visible(
    qtbot, monkeypatch
):
    report = cash_flow_report(snapshot(), PERIOD)
    lines = tuple(v for v in report.movements if v.operation.draft.kind == OperationKind.TRANSFER)
    child = CashFlowSourcesDialog(
        lines, lines[0].currency, "Перевод", {A: "Первый", B: "Второй"}, {}
    )
    qtbot.addWidget(child)
    for row, line in enumerate(lines):
        assert child.model.data(child.model.index(row, 5)) == (
            "1,00" if line.account_id == A else "0,00"
        )
    row = next(i for i, v in enumerate(lines) if v.account_id == B)
    child.table.selectRow(row)
    seen = []

    def show(dialog):
        assert dialog.fields["Комиссия операции"].text() == "1,00 RUB"
        assert dialog.fields["Комиссия по счёту"].text() == "0,00 RUB"
        assert dialog.fields["Изменение остатка"].text() == "300,00 RUB"
        seen.append(dialog.flow)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(OperationSourceDialog, "exec", show)
    child.show_details()
    assert len(seen) == 1
