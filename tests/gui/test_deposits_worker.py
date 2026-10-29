import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QDialog

from personal_finance.domain.models import MAX_MINOR
from personal_finance.presentation import main_window as module
from personal_finance.presentation.deposits import DepositForecastDialog, DepositScenarioDialog
from personal_finance.presentation.main_window import MainWindow
from tests.gui.test_analytics_gui import fingerprints
from tests.gui.test_deposits_gui import fill


@pytest.mark.parametrize("use_book", [False, True])
def test_forecast_worker_without_book_or_without_changing_book(
    qtbot, session, monkeypatch, use_book
):
    window = MainWindow()
    qtbot.addWidget(window)
    if use_book:
        window.activate_session(session)
    before = fingerprints(session)
    assert window.deposit_action.isEnabled()
    threads = []
    seen = []
    original = module.forecast_deposit

    def calculate(value):
        threads.append(QThread.currentThread())
        return original(value)

    def choose(dialog):
        fill(dialog)
        dialog._accept()
        return QDialog.DialogCode.Accepted

    def show(dialog):
        assert not window._busy and not window._jobs
        assert QThread.currentThread() == window.thread()
        seen.append(dialog.report)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(module, "forecast_deposit", calculate)
    monkeypatch.setattr(DepositScenarioDialog, "exec", choose)
    monkeypatch.setattr(DepositForecastDialog, "exec", show)
    window.request_deposit()
    assert window._busy and not window.deposit_action.isEnabled()
    qtbot.waitUntil(lambda: not window._busy and bool(seen), timeout=15000)
    assert len(threads) == 1 and threads[0] != window.thread()
    assert seen[0].interest_minor == 1 and fingerprints(session) == before
    if use_book:
        window.lock_book()
    assert window.deposit_action.isEnabled()


def test_calculation_validation_error_keeps_same_form_and_inputs(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    forms = []
    seen = []

    def choose(dialog):
        forms.append(dialog)
        if len(forms) == 1:
            fill(dialog)
            dialog.initial.setText(str(MAX_MINOR // 100) + ",07")
            dialog.capitalize.setChecked(True)
            dialog._accept()
            assert dialog.result_scenario.initial.minor == MAX_MINOR
            return QDialog.DialogCode.Accepted
        assert dialog is forms[0] and dialog.capitalize.isChecked()
        assert dialog.initial.text() == str(MAX_MINOR // 100) + ",07"
        assert "диапазон" in dialog.feedback.text()
        assert not window._busy
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(DepositScenarioDialog, "exec", choose)
    monkeypatch.setattr(DepositForecastDialog, "exec", lambda dialog: seen.append(dialog))
    window.request_deposit()
    qtbot.waitUntil(lambda: not window._busy and len(forms) == 2, timeout=15000)
    assert not seen


def test_cancel_and_busy_forecast_start_no_worker(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    calls = []
    monkeypatch.setattr(
        DepositScenarioDialog,
        "exec",
        lambda dialog: calls.append(dialog) or QDialog.DialogCode.Rejected,
    )
    window.request_deposit()
    assert len(calls) == 1 and not window._jobs
    window._busy = True
    window._update_report_action()
    window.request_deposit()
    assert not window.deposit_action.isEnabled() and len(calls) == 1 and not window._jobs
    window._busy = False
    window._update_report_action()


def test_unexpected_calculation_error_is_safe_and_preserves_form(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    forms = []
    marker = "НЕ_ПОКАЗЫВАТЬ_ТЕХНИЧЕСКИЕ_ДАННЫЕ"

    def fail(value):
        raise RuntimeError(marker)

    def choose(dialog):
        forms.append(dialog)
        if len(forms) == 1:
            fill(dialog)
            dialog._accept()
            return QDialog.DialogCode.Accepted
        assert dialog is forms[0] and marker not in dialog.feedback.text()
        assert "Не удалось рассчитать" in dialog.feedback.text() and dialog.initial.text() == "0,01"
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(module, "forecast_deposit", fail)
    monkeypatch.setattr(DepositScenarioDialog, "exec", choose)
    window.request_deposit()
    qtbot.waitUntil(lambda: not window._busy and len(forms) == 2, timeout=15000)
