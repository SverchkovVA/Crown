import json

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QFileDialog

from personal_finance.domain.exporting import ExportFormat
from personal_finance.domain.models import AccountKind
from personal_finance.presentation.investment_export import InvestmentExportDialog
from personal_finance.presentation.investment_portfolio import (
    InvestmentPortfolioDialog,
    InvestmentPortfolioOptions,
)
from personal_finance.presentation.main_window import MainWindow
from tests.gui.test_planning_scenarios_workflow import current
from tests.unit.test_investment_positions import inputs
from tests.unit.test_investments import TODAY


def test_portfolio_export_confirms_exact_snapshot_and_excludes_names(
    session, tmp_path, qtbot, monkeypatch
):
    account = session.catalog.add_account("Учебный счёт для экспорта", AccountKind.INVESTMENT)
    position = session.investment_positions.save(inputs(account))
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    target = tmp_path / "portfolio.json"
    try:
        window.investment_portfolio_action.trigger()
        options = current(window, InvestmentPortfolioOptions)
        options.as_of.setDate(QDate(TODAY.year, TODAY.month, TODAY.day))
        options.calculate.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        report = current(window, InvestmentPortfolioDialog)
        report.export.click()
        form = current(window, InvestmentExportDialog)
        assert not form.include_names.isChecked() and form.preview.model().columnCount() == 4
        form.include_names.setChecked(True)
        assert form.preview.model().columnCount() == 5
        assert form.preview.model().item(0, 1).text() == account.name
        form.include_names.setChecked(False)
        form.format.setCurrentIndex(form.format.findData(ExportFormat.JSON.value))
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
        form.save.click()
        qtbot.waitUntil(lambda: not window._busy and target.exists(), timeout=15000)
        raw = target.read_bytes()
        assert json.loads(raw)["positions"][0]["market_minor"] == "1110"
        assert "Учебный".encode() not in raw and account.id.encode() not in raw
        assert session.investment_positions.items() == [position]
    finally:
        window.lock_book()
