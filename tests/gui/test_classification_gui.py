from dataclasses import replace
from uuid import uuid4

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox

from personal_finance.application import vault as vault_module
from personal_finance.application.classification import prepare_import_categories
from personal_finance.domain.classification import CategoryRule
from personal_finance.domain.ledger import OperationKind, OperationPart
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.infrastructure.importing.statements import CsvMapping, parse_csv
from personal_finance.infrastructure.persistence.migration_runner import initialize_schema
from personal_finance.presentation.classification import (
    CategoryRuleDialog,
    CategoryRuleHistoryDialog,
    CategoryRulePage,
)
from personal_finance.presentation.import_dialogs import (
    ImportOptions,
    ImportOptionsDialog,
    ImportPreviewDialog,
)
from personal_finance.presentation.ledger_dialogs import OperationDialog
from personal_finance.presentation.main_window import MainWindow
from tests.conftest import PASSWORD

SOURCE = (
    "Дата;Сумма;Описание;ID\n"
    "15.01.2026;-100,00;МАГАЗИН тест;rule-1\n"
    "16.01.2026;20,00;Зарплата тест;rule-2\n"
).encode()


@pytest.fixture
def configured(session):
    account = session.catalog.add_account("Счёт подсказок", AccountKind.CASH)
    categories = session.catalog.categories()
    expense = [c.id for c in categories if c.kind == CategoryKind.EXPENSE]
    rule = CategoryRule(
        str(uuid4()), "Магазин <пример>", OperationKind.EXPENSE, "магазин", expense[0], 10
    )
    stored = session.classification.save(rule)
    return account, categories, expense, stored


def preview_dialog(qtbot, session, configured, *, manual=None, use_rules=True, prepared=None):
    account, categories, _, _ = configured
    options = ImportOptions(
        account.id, manual, None, CsvMapping("Дата", "Сумма", "Описание", "ID"), use_rules
    )
    document = parse_csv(SOURCE, options.mapping)
    preview = session.importing.preview(document, account.id)
    if prepared is None:
        prepared = prepare_import_categories(
            session.classification.snapshot(), preview, manual, None
        )
    dialog = ImportPreviewDialog(
        preview,
        options,
        session.catalog.accounts(),
        categories,
        session.catalog.institutions(),
        [],
        [],
        classification=prepared,
    )
    qtbot.addWidget(dialog)
    return dialog


def fill_rule(dialog, category):
    dialog.name.setText("Правило из формы")
    dialog.contains.setText("магазин")
    dialog.category.setCurrentIndex(dialog.category.findData(category))
    dialog.priority.setValue(12)
    dialog._validate()
    return dialog.result()


def test_rule_dialog_validates_new_definition_and_filters_category_kind(qtbot, session, configured):
    _, categories, expense, _ = configured
    dialog = CategoryRuleDialog(session.catalog.accounts(), categories)
    qtbot.addWidget(dialog)
    assert fill_rule(dialog, expense[1]) == QDialog.DialogCode.Accepted
    assert (
        dialog.rule.category_id == expense[1] and dialog.rule.priority == 12 and dialog.rule.enabled
    )
    dialog.kind.setCurrentIndex(dialog.kind.findData(OperationKind.INCOME.value))
    assert dialog.category.findData(expense[1]) == -1
    assert not session.ledger.operations()


def test_edit_dialog_keeps_identity_and_disabled_state(qtbot, session, configured):
    _, categories, _, stored = configured
    existing = replace(stored.rule, enabled=False)
    dialog = CategoryRuleDialog(session.catalog.accounts(), categories, existing=existing)
    qtbot.addWidget(dialog)
    dialog.priority.setValue(25)
    dialog._validate()
    assert dialog.rule.id == existing.id and not dialog.rule.enabled and dialog.rule.priority == 25


def test_invalid_rule_dialog_does_not_accept_or_write(qtbot, session, configured, monkeypatch):
    _, categories, _, _ = configured
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    dialog = CategoryRuleDialog(session.catalog.accounts(), categories)
    qtbot.addWidget(dialog)
    dialog._validate()
    assert errors and dialog.result() != QDialog.DialogCode.Accepted
    assert len(session.classification.rules()) == 1


def test_rule_page_literal_names_and_selection_actions(qtbot, session, configured):
    _, categories, _, stored = configured
    page = CategoryRulePage()
    qtbot.addWidget(page)
    page.set_records([stored], True, session.catalog.accounts(), categories)
    assert page.add.isEnabled() and not page.edit.isEnabled()
    assert page.model.item(0, 1).text() == stored.rule.name
    assert page.model.headerData(3, Qt.Orientation.Horizontal) == "Категория"
    assert page.model.item(0, 3).text() == next(
        c.name for c in categories if c.id == stored.rule.category_id
    )
    page.table.selectRow(0)
    assert page.edit.isEnabled() and page.toggle.text() == "Выключить"
    disabled = replace(stored, rule=replace(stored.rule, enabled=False))
    page.set_records([disabled], True, session.catalog.accounts(), categories)
    page.table.selectRow(0)
    assert page.toggle.text() == "Включить"


def test_main_rule_actions_use_worker_versions_and_keep_financial_state(
    qtbot, session, configured, monkeypatch
):
    _, _, expense, _ = configured
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    before = session.ledger.operations(), session.ledger.balances()
    monkeypatch.setattr(CategoryRuleDialog, "exec", lambda dialog: fill_rule(dialog, expense[1]))
    window.rule_action("add")
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    created = next(v for v in session.classification.rules() if v.rule.name == "Правило из формы")
    index = next(i for i, v in enumerate(window.rule_page._records) if v.rule.id == created.rule.id)
    window.rule_page.table.selectRow(index)
    window.rule_action("toggle")
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert len(session.classification.history(created.rule.id)) == 2
    assert not session.classification.history(created.rule.id)[-1].rule.enabled
    assert (session.ledger.operations(), session.ledger.balances()) == before
    window.lock_book()


def test_schema8_keeps_rules_unavailable_without_migration(qtbot, tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module, "initialize_schema", lambda e: initialize_schema(e, target="0008")
        )
        old, _ = vault_module.create_vault(
            tmp_path / "schema8.pfvault", "Книга без правил", PASSWORD
        )
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.activate_session(old)
        assert old.schema_revision == "0008" and old.classification is None
        assert (
            not window.rule_page.add.isEnabled() and "новую папку" in window.rule_page.notice.text()
        )
    finally:
        window.lock_book()


def test_rule_suggestion_is_visible_and_only_explicit_confirm_can_save(qtbot, session, configured):
    dialog = preview_dialog(qtbot, session, configured)
    assert configured[2][0] in (dialog._drafts[1].category_id,)
    assert "Правило:" in dialog.table.item(0, 6).text()
    assert "&lt;пример&gt;" in dialog.table.item(0, 6).toolTip()
    assert "<пример>" in dialog.table.item(0, 6).text()
    assert dialog.table.item(0, 0).checkState() == Qt.CheckState.Checked
    assert not session.ledger.operations()
    dialog._validate()
    assert (
        len(dialog.selections) == 1 and dialog.selections[0].draft.category_id == configured[2][0]
    )
    assert not session.ledger.operations()
    result = session.importing.confirm(
        dialog.preview.document, dialog.options.account_id, dialog.selections
    )
    assert len(result.operations) == 1 and session.ledger.balances()[0].amount.minor == -10000


def test_general_manual_category_has_priority_over_rule(qtbot, session, configured):
    manual = configured[2][1]
    dialog = preview_dialog(qtbot, session, configured, manual=manual)
    assert (
        dialog._drafts[1].category_id == manual and "Правило:" not in dialog.table.item(0, 6).text()
    )


def test_explicitly_disabled_rule_usage_does_not_apply_prepared_suggestions(
    qtbot, session, configured
):
    dialog = preview_dialog(qtbot, session, configured, use_rules=False)
    assert (
        not dialog._drafts
        and not dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
    )


def test_equal_priority_conflict_requires_manual_category_and_preserves_later_choice(
    qtbot, session, configured, monkeypatch
):
    _, _, expense, stored = configured
    session.classification.save(
        replace(stored.rule, id=str(uuid4()), name="Другое правило", category_id=expense[1])
    )
    dialog = preview_dialog(qtbot, session, configured)
    assert 1 not in dialog._drafts and "Конфликт правил" in dialog.table.item(0, 6).text()
    dialog.table.selectRow(0)

    def accept(editor):
        editor.category.setCurrentIndex(editor.category.findData(expense[1]))
        editor._validate()
        return editor.result()

    monkeypatch.setattr(OperationDialog, "exec", accept)
    dialog.edit_selected()
    dialog._render(1)
    assert (
        dialog._drafts[1].category_id == expense[1] and "вручную" in dialog.table.item(0, 6).text()
    )


def test_manual_split_replaces_suggestion_and_is_not_reclassified(
    qtbot, session, configured, monkeypatch
):
    dialog = preview_dialog(qtbot, session, configured)
    expense = configured[2]
    dialog.table.selectRow(0)

    def accept(editor):
        editor.result_draft = replace(
            dialog._drafts[1],
            category_id=None,
            parts=(OperationPart(expense[0], Money(6000)), OperationPart(expense[1], Money(4000))),
        )
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(OperationDialog, "exec", accept)
    dialog.edit_selected()
    dialog._render(1)
    assert dialog._drafts[1].category_id is None and len(dialog._drafts[1].parts) == 2
    assert "частей: 2" in dialog.table.item(0, 4).text()


def test_prepared_snapshot_keeps_original_rule_name_and_category(qtbot, session, configured):
    account, _, expense, stored = configured
    mapping = CsvMapping("Дата", "Сумма", "Описание", "ID")
    preview = session.importing.preview(parse_csv(SOURCE, mapping), account.id)
    prepared = prepare_import_categories(session.classification.snapshot(), preview, None, None)
    session.classification.save(
        replace(stored.rule, name="Позднее имя", category_id=expense[1]),
        expected_revision_id=stored.revision_id,
    )
    dialog = preview_dialog(qtbot, session, configured, prepared=prepared)
    assert dialog._drafts[1].category_id == expense[0]
    assert (
        stored.rule.name in dialog.table.item(0, 6).text()
        and "Позднее имя" not in dialog.table.item(0, 6).text()
    )


def test_import_options_make_rule_usage_explicit(qtbot, session, configured):
    _, categories, _, _ = configured
    dialog = ImportOptionsDialog(
        SOURCE,
        is_csv=True,
        accounts=session.catalog.accounts(),
        categories=categories,
        allow_rules=True,
    )
    qtbot.addWidget(dialog)
    assert dialog.use_rules.isEnabled() and dialog.use_rules.isChecked()
    dialog.account.setCurrentIndex(1)
    dialog.use_rules.setChecked(False)
    dialog._validate()
    assert dialog.options is not None and not dialog.options.use_rules


def test_history_shows_all_definitions_and_cannot_edit(qtbot, session, configured):
    _, categories, _, stored = configured
    second = session.classification.save(
        replace(stored.rule, priority=45), expected_revision_id=stored.revision_id
    )
    dialog = CategoryRuleHistoryDialog(
        session.classification.history(stored.rule.id), session.catalog.accounts(), categories
    )
    qtbot.addWidget(dialog)
    assert dialog.model.rowCount() == 2 and dialog.model.item(1, 2).text() == str(
        second.rule.priority
    )
    assert not dialog.model.item(0, 0).isEditable()


def test_main_import_pipeline_matches_rules_in_worker_before_explicit_confirmation(
    qtbot, session, configured, tmp_path, monkeypatch
):
    import threading

    from PySide6.QtWidgets import QFileDialog

    from personal_finance.presentation import main_window as main_module

    account, _, expense, _ = configured
    path = tmp_path / "rules-import.csv"
    path.write_bytes(SOURCE)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))

    def options(dialog):
        dialog.account.setCurrentIndex(dialog.account.findData(account.id))
        assert dialog.use_rules.isChecked()
        dialog._validate()
        return dialog.result()

    monkeypatch.setattr(ImportOptionsDialog, "exec", options)
    reviewed = []

    def preview(dialog):
        assert "Правило:" in dialog.table.item(0, 6).text()
        assert dialog._drafts[1].category_id == expense[0]
        dialog._validate()
        reviewed.append(dialog.selections)
        return dialog.result()

    monkeypatch.setattr(ImportPreviewDialog, "exec", preview)
    completed = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: completed.append(args[-1]))
    matching_threads = []
    prepare = main_module.prepare_import_categories

    def record_thread(*args):
        matching_threads.append(threading.get_ident())
        return prepare(*args)

    monkeypatch.setattr(main_module, "prepare_import_categories", record_thread)
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.import_statement()
    qtbot.waitUntil(lambda: bool(completed) and not window._busy, timeout=20000)
    assert reviewed and len(session.ledger.operations()) == 1
    assert matching_threads and all(
        identifier != threading.get_ident() for identifier in matching_threads
    )
    assert session.ledger.balances()[0].amount.minor == -10000
    window.lock_book()
