import csv
import json
from io import StringIO

import pytest
from PySide6.QtWidgets import QComboBox, QFileDialog, QPushButton

from personal_finance.domain.deposits import forecast_deposit
from personal_finance.domain.exporting import ExportFormat
from personal_finance.presentation.deposit_scenarios import ScenarioAction
from personal_finance.presentation.deposits import DepositForecastDialog, DepositScenarioDialog
from personal_finance.presentation.main_window import MainWindow
from tests.gui.test_deposits_gui import fill
from tests.unit.test_deposits import scenario


@pytest.mark.parametrize("saved", [False, True])
def test_export_uses_visible_payouts_in_one_snapshot(qtbot, session, tmp_path, monkeypatch, saved):
    conditions = scenario()
    before = session.ledger.operations(), session.ledger.balances()
    if saved:
        session.deposit_scenarios.save("Скрытое имя сценария", conditions)
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    window.show()
    panel = window._report_forms

    def current():
        return panel.stack.currentWidget().widget()

    try:
        if saved:
            window.saved_deposits_action.trigger()
            qtbot.waitUntil(lambda: not window._busy, timeout=15000)
            listing = current()
            listing.table.selectRow(0)
            listing.buttons[ScenarioAction.CALCULATE].click()
        else:
            window.deposit_action.trigger()
            options = current()
            assert isinstance(options, DepositScenarioDialog)
            fill(options)
            options._accept()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        result = current()
        assert isinstance(result, DepositForecastDialog)
        if saved:
            assert result.report == forecast_deposit(conditions)
        assert result.export_button is not None and not result.isWindow()

        target = tmp_path / "forecast.json"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))

        def export(format):
            result.export_button.click()
            form = current()
            assert form.windowTitle() == "Экспорт прогнозных выплат"
            selector = form.findChild(QComboBox)
            selector.setCurrentIndex(selector.findData(format.value))
            assert not target.exists()
            form.findChild(QPushButton).click()
            qtbot.waitUntil(lambda: not window._busy, timeout=15000)
            assert current() is result

        export(ExportFormat.JSON)
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["format"] == "crown-deposit-forecast"
        assert payload["interest_minor"] == str(result.report.interest_minor)
        assert [row["date"] for row in payload["payouts"]] == [
            row.day.isoformat() for row in result.payouts
        ]
        assert all(rate.revision_id not in str(payload) for rate in result.report.scenario.rates)
        assert "Скрытое" not in str(payload)

        target = tmp_path / "forecast.csv"
        export(ExportFormat.CSV)
        rows = list(csv.DictReader(StringIO(target.read_text(encoding="utf-8-sig")), delimiter=";"))
        assert len(rows) == len(result.payouts)
        assert rows[-1]["Проценты"].replace(" ", "") == (
            result.table.model().item(len(rows) - 1, 1).text().replace(" ", "")
        )
        assert before == (session.ledger.operations(), session.ledger.balances())
    finally:
        window.lock_book()
