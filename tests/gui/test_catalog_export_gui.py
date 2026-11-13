import json

from PySide6.QtWidgets import QFileDialog

from personal_finance.domain.catalog_export import CatalogSection
from personal_finance.domain.exporting import ExportFormat
from personal_finance.domain.models import AccountKind, CategoryKind
from personal_finance.presentation.catalog_export import CatalogExportOptions, CatalogExportPreview
from personal_finance.presentation.main_window import MainWindow
from tests.gui.test_planning_scenarios_workflow import current
from tests.integration.test_deposit_scenario_upgrade import create_at


def test_export_catalog_links_and_names_selection_without_migration(tmp_path, monkeypatch, qtbot):
    root = tmp_path / "legacy.pfvault"
    session, _ = create_at(monkeypatch, root, "0002")
    before = session.schema_revision
    bank = session.catalog.add_institution("Учебный банк")
    account = session.catalog.add_account("Учебный счёт", AccountKind.DEBIT, bank.id)
    parent = session.catalog.add_category("Учебные расходы", CategoryKind.EXPENSE)
    child = session.catalog.add_category("Подкатегория", CategoryKind.EXPENSE, parent.id)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    target = tmp_path / "catalog.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    try:
        assert window.catalog_export_action.isEnabled() and session.ledger is None
        window.catalog_export_action.trigger()
        options = current(window, CatalogExportOptions)
        options.format.setCurrentIndex(options.format.findData(ExportFormat.JSON.value))
        options.preview.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        preview = current(window, CatalogExportPreview)
        assert preview.table.model().item(0, 2).text() == ""
        preview.save.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        result = json.loads(target.read_text(encoding="utf-8"))
        rows = result["rows"]
        by_number = {row["number"]: row for row in rows}
        (account_row,) = (row for row in rows if row["section"] == "accounts")
        assert by_number[account_row["related"]]["section"] == "banks"
        assert account_row["currency"] == "RUB" and account_row["kind"] == "debit"
        assert any(
            by_number[row["related"]]["section"] == "categories"
            for row in rows
            if row["section"] == "categories" and "related" in row
        )
        assert not any("name" in row for row in rows)
        assert all(value.id not in str(result) for value in (bank, account, parent, child))

        target = tmp_path / "accounts.csv"
        options.format.setCurrentIndex(options.format.findData(ExportFormat.CSV.value))
        options.names.setChecked(True)
        options.sections[CatalogSection.BANKS].setChecked(False)
        options.sections[CatalogSection.CATEGORIES].setChecked(False)
        options.preview.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        preview = current(window, CatalogExportPreview)
        assert preview.table.model().rowCount() == 1
        assert preview.table.model().item(0, 2).text() == "Учебный счёт"
        assert preview.table.model().item(0, 3).text() == "Банковский счёт"
        assert preview.table.model().item(0, 5).text() == ""
        preview.save.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        contents = target.read_text(encoding="utf-8-sig")
        assert "Учебный счёт" in contents and "Учебный банк" not in contents
        assert "Банковский счёт" in contents and "debit" not in contents
        assert session.schema_revision == before == "0002"
        assert session.catalog.accounts() == [account]
    finally:
        window.lock_book()
