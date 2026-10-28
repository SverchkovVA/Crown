from dataclasses import replace
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox

from personal_finance.application import vault as vault_module
from personal_finance.domain.ledger import OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.domain.query import OperationQuery
from personal_finance.infrastructure.persistence import migration_runner
from personal_finance.presentation.dialogs import NameDialog
from personal_finance.presentation.ledger_dialogs import OperationDialog, OperationHistoryDialog
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.query_dialog import QueryDialog
from personal_finance.presentation.tag_selector import TagDialog, TagSelector
from tests.conftest import PASSWORD

DAY = date(2026, 1, 15)


def data(session):
    tag = session.organization.add_tag("Поездка")
    account = session.catalog.add_account("Наличные", AccountKind.CASH)
    category = next(c.id for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE)
    first = session.ledger.add(
        OperationDraft(
            OperationKind.EXPENSE,
            DAY,
            account.id,
            Money(1000),
            category,
            tag_ids=(tag.id,),
            note="Билет",
        )
    )
    second = session.ledger.add(
        OperationDraft(
            OperationKind.EXPENSE, DAY, account.id, Money(2000), category, note="Продукты"
        )
    )
    return tag, account, first, second


def test_operation_editor_preserves_tags_and_history_resolves_names(qtbot, session):
    tag, _, first, _ = data(session)
    dialog = OperationDialog(
        session.catalog.accounts(),
        session.catalog.categories(),
        session.catalog.institutions(),
        existing=first,
        tags=session.organization.tags(),
        allow_tags=True,
    )
    qtbot.addWidget(dialog)
    assert dialog.tag_selector.values() == (tag.id,)
    dialog._validate()
    assert dialog.result_draft == first.draft
    hidden = OperationDialog(
        session.catalog.accounts(),
        session.catalog.categories(),
        session.catalog.institutions(),
        existing=first,
    )
    qtbot.addWidget(hidden)
    hidden._validate()
    assert hidden.result_draft.tag_ids == (tag.id,)
    history = OperationHistoryDialog([first], tags=session.organization.tags())
    qtbot.addWidget(history)
    assert "Теги: Поездка" in history.details.toPlainText()


def test_archived_tag_only_appears_if_already_assigned_and_cancel_keeps_selection(
    qtbot, session, monkeypatch
):
    tag, _, _, _ = data(session)
    archived = session.organization.archive_tag(tag.id, tag.revision_id)
    adding = TagDialog([archived], ())
    existing = TagDialog([archived], (tag.id,))
    qtbot.addWidget(adding)
    qtbot.addWidget(existing)
    assert adding.choices.count() == 0 and existing.choices.values() == (tag.id,)
    selector = TagSelector([archived])
    qtbot.addWidget(selector)
    selector.set_values((tag.id,))
    monkeypatch.setattr(TagDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    selector._choose()
    assert selector.values() == (tag.id,)


def test_query_editor_roundtrips_and_rejects_reversed_dates(qtbot, session, monkeypatch):
    tag, account, _, _ = data(session)
    query = OperationQuery(
        DAY, DAY, account_ids=(account.id,), tag_ids=(tag.id,), text="Билет", include_deleted=True
    )
    dialog = QueryDialog(
        query,
        session.catalog.accounts(),
        session.catalog.categories(),
        session.catalog.institutions(),
        session.organization.tags(),
    )
    qtbot.addWidget(dialog)
    dialog._validate()
    assert dialog.result_query == query
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    dialog.start.setDate(QDate(2026, 2, 1))
    dialog._validate()
    assert dialog.result_query is None and "Начало периода" in errors[-1]


def test_filter_changes_journal_but_not_balances_and_save_apply_update_works(
    qtbot, session, monkeypatch
):
    tag, _, first, second = data(session)
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.navigation.setCurrentRow(0)
    window.show()
    window._set_query(OperationQuery(tag_ids=(tag.id,)))
    assert window.operation_model.rowCount() == 1
    assert window._journal_operations == [first]
    assert window.account_model.item(0, 4).text() == "-30,00"

    def name(dialog):
        dialog.setTextValue("Поездки")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(NameDialog, "exec", name)
    qtbot.mouseClick(window.save_filter_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    saved = session.organization.filters()[0]
    window.saved_filter_combo.setCurrentIndex(window.saved_filter_combo.findData(saved.id))
    assert window.apply_filter_button.isEnabled()
    window.reset_query()
    assert window.operation_model.rowCount() == 2
    window.apply_saved_filter()
    assert window._journal_operations == [first]
    window._set_query(OperationQuery(text="Продукты"))
    qtbot.mouseClick(window.update_filter_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert session.organization.filters()[0].query == OperationQuery(text="Продукты")
    assert window._journal_operations == [second]
    assert len(session.organization.history("saved_filter", saved.id)) == 2
    window.lock_book()
    assert window._active_query == OperationQuery() and window._tag_records == []
    assert window.saved_filter_combo.count() == 1 and not window.save_filter_button.isEnabled()


def test_tag_page_archives_and_restores_in_worker(qtbot, session, monkeypatch):
    tag, _, first, _ = data(session)
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    window.show()
    window.organization_page.tag_table.selectRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    qtbot.mouseClick(window.organization_page.archive_tag, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert window.organization_page.tag_model.rowCount() == 0
    window.organization_page.archived.setChecked(True)
    assert window.organization_page.tag_model.rowCount() == 1
    window.organization_page.tag_table.selectRow(0)
    assert window.organization_page.restore_tag.isEnabled()
    qtbot.mouseClick(window.organization_page.restore_tag, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert session.organization.tags()[0].id == tag.id
    assert session.ledger.history(first.id) == [first]
    session.check_integrity()


def test_plain_names_and_escaped_tooltips_do_not_render_markup(qtbot, session):
    tag, _, first, _ = data(session)
    changed = session.organization.rename_tag(tag.id, tag.revision_id, "<b>Тег & имя</b>")
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    assert window.book_title.textFormat() == Qt.TextFormat.PlainText
    row = window._journal_operations.index(first)
    item = window.operation_model.item(row, 8)
    assert item.text() == changed.name
    assert "&lt;b&gt;" in item.toolTip() and "<b>" not in item.toolTip()
    window.lock_book()


def test_basic_filter_works_on_schema6_but_persistence_and_tags_are_disabled(
    qtbot, tmp_path, monkeypatch
):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module,
            "initialize_schema",
            lambda e: migration_runner.initialize_schema(e, target="0006"),
        )
        old, _ = vault_module.create_vault(
            tmp_path / "old6.pfvault", "Старая тестовая книга", PASSWORD
        )
    try:
        account = old.catalog.add_account("Счёт", AccountKind.CASH)
        old.ledger.add(OperationDraft(OperationKind.OPENING, DAY, account.id, Money(100)))
        window = MainWindow()
        qtbot.addWidget(window)
        window.activate_session(old)
        assert window.filter_button.isEnabled() and not window.save_filter_button.isEnabled()
        window._set_query(OperationQuery(kinds=(OperationKind.EXPENSE,)))
        assert window.operation_model.rowCount() == 0
        assert old.schema_revision == "0006"
        window.lock_book()
    finally:
        old.close()


def test_long_refund_form_scrolls_and_keeps_save_buttons_inside_window(qtbot, session):
    tag, _, first, _ = data(session)
    categories = [
        value.id for value in session.catalog.categories() if value.kind == CategoryKind.EXPENSE
    ]
    session.ledger.replace(
        first.id,
        first.revision_id,
        replace(
            first.draft,
            category_id=None,
            parts=(
                OperationPart(categories[0], Money(600)),
                OperationPart(categories[1], Money(400)),
            ),
        ),
    )
    dialog = OperationDialog(
        session.catalog.accounts(),
        session.catalog.categories(),
        session.catalog.institutions(),
        kind=OperationKind.REFUND,
        allow_refunds=True,
        refund_candidates=session.ledger.refund_candidates(),
        refund_of_id=first.id,
        tags=session.organization.tags(),
        allow_tags=True,
    )
    qtbot.addWidget(dialog)
    dialog.resize(650, 450)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.form_scroll.verticalScrollBar().maximum() > 0)
    dialog.form_scroll.ensureWidgetVisible(dialog.tag_selector)
    assert dialog.form_scroll.verticalScrollBar().value() > 0
    buttons = dialog.findChild(QDialogButtonBox)
    assert buttons is not None and dialog.rect().contains(buttons.geometry())
    dialog.tag_selector.set_values((tag.id,))
    dialog._validate()
    assert dialog.result_draft is not None and dialog.result_draft.tag_ids == (tag.id,)
    assert len(dialog.result_draft.parts) == 2
