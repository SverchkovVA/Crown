from dataclasses import replace
from uuid import uuid4

from PySide6.QtWidgets import QDialog

from personal_finance.domain.classification import CategoryRule
from personal_finance.domain.ledger import OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind
from personal_finance.domain.ocr_settings import OcrPreferences, TransferParty
from personal_finance.presentation.ocr_batch import OcrBatchDialog
from personal_finance.presentation.ocr_preview import OcrPreviewDialog
from tests.gui.test_ocr_batch_gui import source


def configured(session, *, account_specific=False):
    account = session.catalog.add_account("Вымышленный счёт правил OCR", AccountKind.CASH)
    other = session.catalog.add_account("Вымышленный второй счёт правил OCR", AccountKind.CASH)
    categories = session.catalog.categories()
    expense = [c.id for c in categories if c.kind == CategoryKind.EXPENSE]
    rule = CategoryRule(
        str(uuid4()),
        "Магазин <пример>",
        OperationKind.EXPENSE,
        "магазин",
        expense[0],
        20,
        account.id if account_specific else None,
    )
    session.classification.save(rule)
    return account, other, expense, rule


def make_dialog(qtbot, session, preferences=None):
    dialog = OcrBatchDialog(
        source(),
        session.ocr_importing,
        session.catalog.accounts(),
        session.catalog.categories(),
        preferences
        or OcrPreferences(reference_year=2026, confirm_fields=False, review_before_save=False),
        classification=session.classification.snapshot(),
    )
    qtbot.addWidget(dialog)
    return dialog


def choose_account(dialog, identifier):
    dialog.common_account.setCurrentIndex(dialog.common_account.findData(identifier))


def test_rule_proposes_category_and_commits_without_changing_old_operations(qtbot, session):
    account, _, expense, rule = configured(session)
    dialog = make_dialog(qtbot, session)
    choose_account(dialog, account.id)
    assert dialog.rows[0].category.currentData() == expense[0]
    assert rule.name in dialog.rows[0].state.text()
    assert not session.ledger.operations()
    dialog.prepare()
    qtbot.waitUntil(lambda: dialog.saved_count == 2 and dialog._job is None, timeout=15000)
    operations = session.ledger.operations()
    assert (
        next(v for v in operations if v.draft.kind == OperationKind.EXPENSE).draft.category_id
        == expense[0]
    )
    assert len(operations) == 2
    session.check_integrity()


def test_manual_category_survives_account_and_rule_switches(qtbot, session):
    account, other, expense, _ = configured(session)
    dialog = make_dialog(qtbot, session)
    choose_account(dialog, account.id)
    row = dialog.rows[0]
    row.category.setCurrentIndex(row.category.findData(expense[1]))
    row.account.setCurrentIndex(row.account.findData(other.id))
    dialog.use_rules.setChecked(False)
    dialog.use_rules.setChecked(True)
    assert row.category.currentData() == expense[1]
    assert row.manual_category and "вручную" in row.state.text()


def test_reselecting_same_default_category_is_an_explicit_manual_choice(qtbot, session):
    account, _, _, _ = configured(session)
    dialog = make_dialog(qtbot, session)
    row = dialog.rows[0]
    default = row.category.currentData()
    row.category.activated.emit(row.category.currentIndex())
    choose_account(dialog, account.id)
    assert row.category.currentData() == default and row.manual_category


def test_account_specific_rule_recalculates_only_automatic_choice(qtbot, session):
    account, other, expense, _ = configured(session, account_specific=True)
    dialog = make_dialog(qtbot, session)
    choose_account(dialog, other.id)
    row = dialog.rows[0]
    assert row.category.currentData() != expense[0]
    row.account.setCurrentIndex(row.account.findData(account.id))
    assert row.category.currentData() == expense[0] and not row.manual_category


def test_equal_priority_conflict_requires_manual_choice_and_writes_no_partial_batch(qtbot, session):
    account, _, expense, _ = configured(session)
    session.classification.save(
        CategoryRule(
            str(uuid4()), "Другое правило", OperationKind.EXPENSE, "МАГАЗИН", expense[1], 20
        )
    )
    dialog = make_dialog(qtbot, session)
    choose_account(dialog, account.id)
    assert dialog.rows[0].category_conflict and dialog.rows[0].category.currentData() is None
    dialog.prepare()
    assert "Строка 1" in dialog.notice.text() and "Конфликт правил" in dialog.notice.text()
    assert not session.ledger.operations() and dialog._job is None
    row = dialog.rows[0]
    row.category.setCurrentIndex(row.category.findData(expense[1]))
    dialog.prepare()
    qtbot.waitUntil(lambda: dialog.saved_count == 2 and dialog._job is None, timeout=15000)
    assert len(session.ledger.operations()) == 2


def test_disabling_rules_restores_fallback_for_automatic_rows(qtbot, session):
    account, _, expense, _ = configured(session)
    dialog = make_dialog(qtbot, session)
    choose_account(dialog, account.id)
    assert dialog.rows[0].category.currentData() == expense[0]
    dialog.use_rules.setChecked(False)
    assert dialog.rows[0].category.currentData() != expense[0]
    assert not dialog.rows[0].manual_category
    dialog.use_rules.setChecked(True)
    assert dialog.rows[0].category.currentData() == expense[0]


def test_snapshot_does_not_replace_open_hints_or_reclassify_existing_facts(qtbot, session):
    account, _, expense, rule = configured(session)
    dialog = make_dialog(qtbot, session)
    choose_account(dialog, account.id)
    previous = session.classification.rules()[0]
    session.classification.save(
        replace(rule, category_id=expense[1]), expected_revision_id=previous.revision_id
    )
    assert dialog.rows[0].category.currentData() == expense[0]
    new = make_dialog(qtbot, session)
    choose_account(new, account.id)
    assert new.rows[0].category.currentData() == expense[1]
    assert not session.ledger.operations()


def test_corrected_description_recalculates_automatic_hint(qtbot, session, monkeypatch):
    account, _, expense, _ = configured(session)
    session.classification.save(
        CategoryRule(
            str(uuid4()), "Другое описание", OperationKind.EXPENSE, "другой пример", expense[1], 20
        )
    )
    dialog = make_dialog(qtbot, session)
    choose_account(dialog, account.id)

    def edit(value):
        value.description.setText("Вымышленный другой пример")
        value._validate()
        assert value.reviewed is not None
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(OcrPreviewDialog, "exec", edit)
    dialog.edit_selected()
    assert dialog.rows[0].category.currentData() == expense[1]


def test_own_transfer_and_exclusion_do_not_get_income_or_expense_category(qtbot, session):
    account, other, _, _ = configured(session)
    prefs = OcrPreferences(
        reference_year=2026,
        confirm_fields=False,
        review_before_save=False,
        transfer_parties=(TransferParty("Магазин", other.id),),
    )
    dialog = make_dialog(qtbot, session, prefs)
    choose_account(dialog, account.id)
    assert all(
        row.kind.currentData() == OperationKind.TRANSFER
        and not row.category.isEnabled()
        and not row.category_conflict
        for row in dialog.rows
    )
    excluded = make_dialog(
        qtbot, session, replace(prefs, transfer_parties=(TransferParty("Магазин", exclude=True),))
    )
    choose_account(excluded, account.id)
    assert all(not row.include.isChecked() and not row.category_conflict for row in excluded.rows)


def test_main_window_passes_book_rule_snapshot_to_history_list(qtbot, session, monkeypatch):
    from personal_finance.presentation.main_window import MainWindow

    _, _, _, rule = configured(session)
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    seen = []

    def inspect(dialog):
        seen.append(dialog._classification)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(OcrBatchDialog, "exec", inspect)
    window._show_ocr_preview(source())
    assert len(seen) == 1 and rule in seen[0].rules
    assert not session.ledger.operations()
    window.lock_book()
