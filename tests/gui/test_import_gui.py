from dataclasses import replace
from datetime import date

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFileDialog, QMessageBox

from personal_finance.application import vault as vault_module
from personal_finance.domain.imports import ImportSelection, ImportStatus
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.infrastructure.importing.statements import CsvMapping, parse_csv
from personal_finance.infrastructure.persistence import migration_runner
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
    "15.01.2026;-100,00;Тестовая покупка;test-1\n"
    "16.01.2026;20,00;Тестовый доход;test-2\n"
).encode()


def data(session):
    account = session.catalog.add_account("Тестовый счёт импорта", AccountKind.CASH)
    categories = session.catalog.categories()
    expense = next(c.id for c in categories if c.kind == CategoryKind.EXPENSE)
    income = next(c.id for c in categories if c.kind == CategoryKind.INCOME)
    mapping = CsvMapping("Дата", "Сумма", "Описание", "ID")
    options = ImportOptions(account.id, expense, income, mapping)
    document = parse_csv(SOURCE, mapping)
    return account, categories, options, document


def preview_dialog(qtbot, session, *, duplicate=False, missing_category=False):
    account, categories, options, document = data(session)
    if duplicate:
        session.ledger.add(
            OperationDraft(
                OperationKind.EXPENSE,
                date(2026, 1, 15),
                account.id,
                Money(10000),
                options.expense_category_id,
            )
        )
    if missing_category:
        options = replace(options, expense_category_id=None, income_category_id=None)
    preview = session.importing.preview(document, account.id)
    dialog = ImportPreviewDialog(
        preview,
        options,
        session.catalog.accounts(),
        categories,
        session.catalog.institutions(),
        session.organization.tags(),
        session.ledger.refund_candidates(),
    )
    qtbot.addWidget(dialog)
    return dialog


def test_options_choose_explicit_account_categories_and_columns_without_writing(qtbot, session):
    account, categories, _, _ = data(session)
    dialog = ImportOptionsDialog(
        SOURCE, is_csv=True, accounts=session.catalog.accounts(), categories=categories
    )
    qtbot.addWidget(dialog)
    dialog.account.setCurrentIndex(dialog.account.findData(account.id))
    dialog.expense_category.setCurrentIndex(1)
    dialog.income_category.setCurrentIndex(1)
    dialog._validate()
    assert dialog.result() == QDialog.DialogCode.Accepted and dialog.options is not None
    assert dialog.options.mapping.date_column == "Дата"
    assert dialog.options.mapping.id_column == "ID"
    assert not session.ledger.operations()


def test_preview_requires_category_and_confirm_only_returns_selections(qtbot, session):
    dialog = preview_dialog(qtbot, session)
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
    dialog._validate()
    assert len(dialog.selections) == 2
    assert not session.ledger.operations()
    assert [value.draft.amount.minor for value in dialog.selections] == [10000, 2000]


def test_possible_duplicate_is_not_selected_automatically(qtbot, session):
    dialog = preview_dialog(qtbot, session, duplicate=True)
    assert dialog.preview.lines[0].status == ImportStatus.POSSIBLE
    assert dialog.table.item(0, 0).checkState() == Qt.CheckState.Unchecked
    assert len(dialog._chosen()) == 1
    dialog.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    assert dialog._chosen()[0].acknowledged_duplicate
    dialog.reject()
    assert len(session.ledger.operations()) == 1


def test_editing_preview_uses_normal_operation_form_without_writing(qtbot, session, monkeypatch):
    dialog = preview_dialog(qtbot, session, missing_category=True)
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
    dialog.table.selectRow(0)

    def accept_editor(editor):
        assert editor.windowTitle() == "Настроить строку импорта"
        assert editor.kind.findData(OperationKind.OPENING.value) == -1
        assert editor.amount.text() == "100,00"
        editor.category.setCurrentIndex(1)
        editor._validate()
        return editor.result()

    monkeypatch.setattr(OperationDialog, "exec", accept_editor)
    dialog.edit_selected()
    dialog.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    chosen = dialog._chosen()
    assert chosen[0].draft.amount == Money(10000)
    assert not session.ledger.operations()


def test_existing_link_in_preview_does_not_create_money(qtbot, session):
    dialog = preview_dialog(qtbot, session, duplicate=True)
    line = dialog.preview.lines[0]
    operation = line.candidates[0]
    dialog._links[line.row.position] = operation
    dialog._drafts[line.row.position] = operation.draft
    dialog.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    dialog._render(line.row.position)
    selected = dialog._chosen()[0]
    assert selected == ImportSelection(
        1, operation.draft, True, operation.id, operation.revision_id
    )
    assert "Связь" in dialog.table.item(0, 4).text()
    assert len(session.ledger.operations()) == 1


def test_main_window_runs_read_parse_confirm_in_worker_steps_after_previous_cleanup(
    qtbot, session, tmp_path, monkeypatch
):
    account, categories, options, _ = data(session)
    source = tmp_path / "synthetic.csv"
    source.write_bytes(SOURCE)
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(source), ""))

    def configure(dialog):
        dialog.account.setCurrentIndex(dialog.account.findData(account.id))
        dialog.expense_category.setCurrentIndex(
            dialog.expense_category.findData(options.expense_category_id)
        )
        dialog.income_category.setCurrentIndex(
            dialog.income_category.findData(options.income_category_id)
        )
        dialog._validate()
        return dialog.result()

    def confirm(dialog):
        assert not window._busy and not window._jobs
        dialog._validate()
        return dialog.result()

    monkeypatch.setattr(ImportOptionsDialog, "exec", configure)
    monkeypatch.setattr(ImportPreviewDialog, "exec", confirm)
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]))
    window.import_statement()
    qtbot.waitUntil(lambda: bool(messages) and not window._busy, timeout=20000)
    assert len(session.ledger.operations()) == 2 and session.ledger.balances()[0].amount == Money(
        -8000
    )
    assert (
        not window._jobs
        and not window._job_callbacks
        and not window._job_results
        and not window._job_deferred
    )
    assert "Создано операций: 2" in messages[0]
    window.lock_book()
    assert not window.import_button.isEnabled()


def test_possible_duplicate_checkbox_is_clickable_in_visible_table(qtbot, session):
    dialog = preview_dialog(qtbot, session, duplicate=True)
    dialog.show()
    item = dialog.table.item(0, 0)
    rect = dialog.table.visualItemRect(item)
    qtbot.mouseClick(
        dialog.table.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(rect.x() + 8, rect.center().y()),
    )
    assert item.checkState() == Qt.CheckState.Checked
    assert dialog._chosen()[0].acknowledged_duplicate


def test_wrong_direction_is_rejected_before_confirming_preview(qtbot, session):
    dialog = preview_dialog(qtbot, session)
    draft = dialog._drafts[1]
    income = next(c.id for c in session.catalog.categories() if c.kind == CategoryKind.INCOME)
    dialog._drafts[1] = replace(draft, kind=OperationKind.INCOME, category_id=income)
    dialog._update_actions()
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
    assert "направление" in dialog.summary.text()
    assert not session.ledger.operations()


def test_schema7_keeps_tags_but_import_button_is_disabled(qtbot, tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module,
            "initialize_schema",
            lambda e: migration_runner.initialize_schema(e, target="0007"),
        )
        old, _ = vault_module.create_vault(
            tmp_path / "old7.pfvault", "Старая тестовая книга", PASSWORD
        )
    try:
        old.catalog.add_account("Прежний счёт", AccountKind.CASH)
        window = MainWindow()
        qtbot.addWidget(window)
        window.activate_session(old)
        assert window.filter_button.isEnabled() and window.save_filter_button.isEnabled()
        assert not window.import_button.isEnabled() and old.importing is None
        assert old.schema_revision == "0007"
        window.lock_book()
    finally:
        old.close()
