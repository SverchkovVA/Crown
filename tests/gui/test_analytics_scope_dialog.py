from uuid import uuid4

from PySide6.QtCore import QDate

from personal_finance.domain.models import Account, AccountKind, Category, CategoryKind, Institution
from personal_finance.presentation.analytics import ReportPeriodDialog

A, B, EMPTY, AA, BB, CASH, ROOT, CHILD, GRAND, OTHER = [str(uuid4()) for _ in range(10)]


def form(qtbot):
    value = ReportPeriodDialog(
        accounts=[
            Account(AA, "Счёт А", AccountKind.DEBIT, A),
            Account(BB, "Счёт Б", AccountKind.DEBIT, B),
            Account(CASH, "Кошелёк", AccountKind.CASH, None),
        ],
        institutions=[
            Institution(A, "Банк А"),
            Institution(B, "Банк Б"),
            Institution(EMPTY, "Без счетов"),
        ],
        categories=[
            Category(ROOT, "Родитель", CategoryKind.EXPENSE),
            Category(CHILD, "Дочерняя", CategoryKind.EXPENSE, ROOT),
            Category(GRAND, "Внучатая", CategoryKind.EXPENSE, CHILD),
            Category(OTHER, "Другая", CategoryKind.EXPENSE),
        ],
    )
    qtbot.addWidget(value)
    return value


def test_default_scope_is_all(qtbot):
    value = form(qtbot)
    value._accept()
    assert (
        value.result_period is not None
        and value.result_scope.account_ids is None
        and value.result_scope.category_ids is None
    )


def test_bank_scope_includes_only_its_accounts(qtbot):
    value = form(qtbot)
    value.bank.setCurrentIndex(value.bank.findData(A))
    value._accept()
    assert value.result_scope.account_ids == frozenset({AA})
    assert value.account.findData(BB) == value.account.findData(CASH) == -1


def test_bank_without_accounts_means_empty_not_all(qtbot):
    value = form(qtbot)
    value.bank.setCurrentIndex(value.bank.findData(EMPTY))
    value._accept()
    assert value.result_scope.account_ids == frozenset()


def test_account_scope_without_bank_filter(qtbot):
    value = form(qtbot)
    value.account.setCurrentIndex(value.account.findData(BB))
    value._accept()
    assert value.result_scope.account_ids == frozenset({BB}) and value.bank.currentData() is None


def test_cash_account_can_be_selected(qtbot):
    value = form(qtbot)
    value.account.setCurrentIndex(value.account.findData(CASH))
    value._accept()
    assert value.result_scope.account_ids == frozenset({CASH})


def test_category_includes_descendants_once(qtbot):
    value = form(qtbot)
    value.category.setCurrentIndex(value.category.findData(ROOT))
    value._accept()
    assert value.result_scope.category_ids == frozenset({ROOT, CHILD, GRAND})


def test_category_can_exclude_children(qtbot):
    value = form(qtbot)
    value.category.setCurrentIndex(value.category.findData(ROOT))
    value.include_children.setChecked(False)
    value._accept()
    assert value.result_scope.category_ids == frozenset({ROOT})


def test_invalid_period_retains_filters_but_has_no_ready_result(qtbot):
    value = form(qtbot)
    value.bank.setCurrentIndex(value.bank.findData(A))
    value.start.setDate(QDate(2026, 11, 2))
    value.end.setDate(QDate(2026, 10, 1))
    value._accept()
    assert value.result_period is None and value.result_scope is None
    assert value.bank.currentData() == A and value.feedback.text()


def test_switching_bank_clears_incompatible_account_selection(qtbot):
    value = form(qtbot)
    value.account.setCurrentIndex(value.account.findData(BB))
    value.bank.setCurrentIndex(value.bank.findData(A))
    value._accept()
    assert value.account.currentData() is None and value.result_scope.account_ids == frozenset({AA})
