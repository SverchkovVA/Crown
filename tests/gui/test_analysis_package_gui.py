import json
from dataclasses import replace
from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QFileDialog

from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.presentation.analysis_package import (
    AnalysisPackageOptions,
    AnalysisPackagePreview,
)
from personal_finance.presentation.main_window import MainWindow
from tests.gui.test_planning_scenarios_workflow import current


def test_save_uses_confirmed_package_and_starts_without_optional_data(
    session, qtbot, tmp_path, monkeypatch
):
    account = session.catalog.add_account("Проверочный счёт с именем", AccountKind.CASH)
    category = session.catalog.add_category("Проверочная категория", CategoryKind.EXPENSE)
    day = date(2026, 11, 12)
    draft = OperationDraft(OperationKind.EXPENSE, day, account.id, Money(800), category.id)
    session.ledger.add(draft)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    target = tmp_path / "snapshot.json"
    try:
        window.analysis_package_action.trigger()
        form = current(window, AnalysisPackageOptions)
        assert not form.categories.isChecked() and not form.portfolio.isChecked()
        form.start.setDate(QDate(2026, 11, 13))
        form.end.setDate(QDate(2026, 11, 12))
        form.preview.click()
        assert form.feedback.isVisible() and current(window, AnalysisPackageOptions) is form
        form.start.setDate(QDate(2026, 11, 1))
        form.categories.setChecked(True)
        form.preview.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        preview = current(window, AnalysisPackagePreview)
        assert preview.table.model().item(0, 3).text() == "8,00"
        session.ledger.add(replace(draft, amount=Money(500)))
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
        preview.save.click()
        qtbot.waitUntil(lambda: not window._busy and target.is_file(), timeout=15000)
        raw = target.read_bytes()
        result = json.loads(raw)
        assert result["totals"][0]["expense_minor"] == "800"
        assert result["categories"][0]["category"] == "Категория 1" and "portfolio" not in result
        assert account.id.encode() not in raw and category.name.encode() not in raw
        assert len(session.ledger.operations()) == 2
    finally:
        window.lock_book()
