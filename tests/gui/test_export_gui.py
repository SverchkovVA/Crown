import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from personal_finance.application import vault as vault_module
from personal_finance.domain.exporting import ExportFormat, ExportSettings, serialize_journal_export
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.domain.query import OperationQuery
from personal_finance.infrastructure.persistence import migration_runner
from personal_finance.presentation import main_window as window_module
from personal_finance.presentation.export_dialogs import ExportOptionsDialog, ExportPreviewDialog
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.query_dialog import QueryDialog
from tests.conftest import PASSWORD


@pytest.fixture(autouse=True)
def isolated_app_data(tmp_path, monkeypatch):
    monkeypatch.setattr(window_module, "user_data_dir", lambda *a, **k: str(tmp_path / "app-data"))


def data(session):
    account = session.catalog.add_account("<b>Тестовый счёт & имя</b>", AccountKind.CASH)
    categories = session.catalog.categories()
    for kind, category_kind, minor in (
        (OperationKind.EXPENSE, CategoryKind.EXPENSE, 10000),
        (OperationKind.INCOME, CategoryKind.INCOME, 2000),
    ):
        category = next(c.id for c in categories if c.kind == category_kind)
        session.ledger.add(
            OperationDraft(
                kind,
                date(2026, 1, 15),
                account.id,
                Money(minor),
                category,
                note="ПРИМЕЧАНИЕ_ДЛЯ_ПРОВЕРКИ",
            )
        )
    return account


def settings_dialog(qtbot, session):
    dialog = ExportOptionsDialog(
        OperationQuery(),
        session.catalog.accounts(),
        session.catalog.categories(),
        session.catalog.institutions(),
        session.organization.tags(),
        allow_tags=True,
    )
    qtbot.addWidget(dialog)
    return dialog


def test_export_defaults_exclude_notes_tags_and_conditions_change_only_export(
    qtbot, session, monkeypatch
):
    data(session)
    dialog = settings_dialog(qtbot, session)
    assert dialog.names.isChecked() and not dialog.notes.isChecked() and not dialog.tags.isChecked()
    selected = OperationQuery(kinds=(OperationKind.EXPENSE,))

    def select(query_dialog):
        query_dialog.result_query = selected
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QueryDialog, "exec", select)
    dialog.edit_query()
    dialog._validate()
    assert dialog.options.settings.query == selected
    assert not dialog.options.settings.include_notes and not dialog.options.settings.include_tags
    assert len(session.ledger.operations()) == 2


@pytest.mark.parametrize("format", list(ExportFormat))
def test_preview_excludes_unselected_fields_and_displays_names_literally(qtbot, session, format):
    account = data(session)
    preview = session.exporting.preview(ExportSettings())
    dialog = ExportPreviewDialog(preview, format)
    qtbot.addWidget(dialog)
    headers = [
        dialog.model.horizontalHeaderItem(i).text() for i in range(dialog.model.columnCount())
    ]
    assert "Примечание" not in headers and "ID тегов JSON" not in headers
    column = headers.index("Счёт")
    item = dialog.model.item(0, column)
    assert item.text() == account.name and "&lt;b&gt;" in item.toolTip()
    assert "<b>" not in item.toolTip()
    assert dialog.model.rowCount() == 2


def test_large_preview_is_bounded_but_serialization_keeps_all_rows(qtbot, session):
    data(session)
    preview = session.exporting.preview(ExportSettings())
    source = preview.operations[0]
    operations = tuple(
        replace(source, id=str(uuid4()), revision_id=str(uuid4())) for _ in range(205)
    )
    preview = replace(preview, operations=operations)
    dialog = ExportPreviewDialog(preview, ExportFormat.JSON)
    qtbot.addWidget(dialog)
    assert dialog.model.rowCount() == 200 and "205" in dialog.limit.text()
    assert (
        len(json.loads(serialize_journal_export(preview, ExportFormat.JSON))["operations"]) == 205
    )


def test_export_pipeline_saves_reviewed_snapshot_without_book_changes(
    qtbot, session, tmp_path, monkeypatch
):
    data(session)
    before, balances = session.ledger.operations(include_deleted=True), session.ledger.balances()
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    original_query = window._active_query
    destination = tmp_path / "reviewed.json"

    def options(dialog):
        dialog.format.setCurrentIndex(dialog.format.findData(ExportFormat.JSON.value))
        dialog.query = OperationQuery(kinds=(OperationKind.EXPENSE,))
        dialog._validate()
        return dialog.result()

    def preview(dialog):
        assert not destination.exists() and not window._busy and not window._jobs
        assert dialog.model.rowCount() == 1
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(ExportOptionsDialog, "exec", options)
    monkeypatch.setattr(ExportPreviewDialog, "exec", preview)

    def filename(*args, **kwargs):
        assert Path(args[2]).parent == tmp_path / "app-data" / "exports"
        assert Path(args[2]).parent.is_dir()
        return str(destination), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", filename)
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]))
    window.export_journal()
    qtbot.waitUntil(lambda: bool(messages) and not window._busy, timeout=20000)
    exported = json.loads(destination.read_bytes())
    assert len(exported["operations"]) == 1 and exported["operations"][0]["amount_minor"] == "10000"
    assert "ПРИМЕЧАНИЕ_ДЛЯ_ПРОВЕРКИ".encode() not in destination.read_bytes()
    assert (
        session.ledger.operations(include_deleted=True) == before
        and session.ledger.balances() == balances
    )
    assert window._active_query == original_query
    assert not window._jobs and not window._job_callbacks and not window._job_results
    window.lock_book()
    assert not window.export_button.isEnabled()


@pytest.mark.parametrize("phase", ["options", "preview", "filename"])
def test_cancel_any_export_step_does_not_create_file(qtbot, session, tmp_path, monkeypatch, phase):
    data(session)
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    before = session.ledger.operations()
    seen = []

    def options(dialog):
        seen.append("options")
        if phase == "options":
            return QDialog.DialogCode.Rejected
        dialog._validate()
        return dialog.result()

    def preview(dialog):
        seen.append("preview")
        return QDialog.DialogCode.Rejected if phase == "preview" else QDialog.DialogCode.Accepted

    def filename(*args, **kwargs):
        seen.append("filename")
        return "", ""

    monkeypatch.setattr(ExportOptionsDialog, "exec", options)
    monkeypatch.setattr(ExportPreviewDialog, "exec", preview)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", filename)
    window.export_journal()
    qtbot.waitUntil(lambda: not window._busy, timeout=20000)
    assert seen[-1] == phase
    assert not list(tmp_path.glob("*.csv")) and session.ledger.operations() == before
    window.lock_book()


@pytest.mark.parametrize("schema,enabled", [("0002", False), ("0003", True)])
def test_legacy_book_export_availability(qtbot, tmp_path, monkeypatch, schema, enabled):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module,
            "initialize_schema",
            lambda e: migration_runner.initialize_schema(e, target=schema),
        )
        old, _ = vault_module.create_vault(
            tmp_path / "old.pfvault", "Старая тестовая книга", PASSWORD
        )
    try:
        window = MainWindow()
        qtbot.addWidget(window)
        window.activate_session(old)
        assert window.export_button.isEnabled() == enabled
        assert (old.exporting is not None) == enabled and old.schema_revision == schema
        window.lock_book()
    finally:
        old.close()
