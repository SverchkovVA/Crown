import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QThread
from PySide6.QtWidgets import QDialog

from personal_finance.presentation import main_window as module
from personal_finance.presentation.deposits import DepositForecastDialog, DepositScenarioDialog
from personal_finance.presentation.main_window import MainWindow
from tests.gui.test_deposits_gui import fill


def test_initial_rate_identity_survives_repeat_and_validation(qtbot):
    form = DepositScenarioDialog()
    qtbot.addWidget(form)
    fill(form)
    form._accept()
    first = form.result_scenario
    form._accept()
    assert form.result_scenario == first
    form.rate.setText("не ставка")
    form._accept()
    assert form.result_scenario is None
    form.rate.setText("50")
    form._accept()
    assert form.result_scenario == first


@pytest.mark.parametrize("enable", [False, True])
def test_result_edit_action_is_explicit_and_close_does_not_request_edit(qtbot, enable):
    form = DepositScenarioDialog()
    qtbot.addWidget(form)
    fill(form)
    form._accept()
    result = DepositForecastDialog(module.forecast_deposit(form.result_scenario), allow_edit=enable)
    qtbot.addWidget(result)
    assert (result.edit_button is not None) == enable
    assert not result.edit_requested
    if enable:
        qtbot.mouseClick(result.edit_button, Qt.MouseButton.LeftButton)
        assert result.result() == QDialog.DialogCode.Accepted and result.edit_requested
    else:
        result.reject()
        assert result.result() == QDialog.DialogCode.Rejected and not result.edit_requested


def test_several_recalculations_use_one_live_form_and_real_workers(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    forms = []
    reports = []
    threads = []
    calculate = module.forecast_deposit

    def counted(value):
        threads.append(QThread.currentThread())
        return calculate(value)

    def choose(form):
        forms.append(form)
        assert not window._busy
        if len(forms) == 1:
            fill(form)
        else:
            assert form is forms[0]
            assert form.rate.text() == "50"
        form.initial.setText(f"0,{2 * len(forms) - 1:02d}")
        form._accept()
        return QDialog.DialogCode.Accepted

    def show(result):
        assert not window._busy and not window._jobs and result.edit_button is not None
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert shiboken6.isValid(forms[0])
        reports.append(result.report)
        if len(reports) < 3:
            result.edit_button.click()
            return result.result()
        result.reject()
        return result.result()

    monkeypatch.setattr(module, "forecast_deposit", counted)
    monkeypatch.setattr(DepositScenarioDialog, "exec", choose)
    monkeypatch.setattr(DepositForecastDialog, "exec", show)
    window.request_deposit()
    qtbot.waitUntil(lambda: len(reports) == 3 and not window._busy, timeout=15000)
    qtbot.waitUntil(lambda: not shiboken6.isValid(forms[0]), timeout=15000)
    assert len(forms) == 3 and len(threads) == 3 and all(t != window.thread() for t in threads)
    assert [v.interest_minor for v in reports] == [1, 2, 3]
    assert len({v.scenario.rates[0].revision_id for v in reports}) == 1
    assert reports[0].scenario.initial.minor == 1
    assert window.deposit_action.isEnabled()


def test_cancel_after_return_does_not_recalculate_or_reuse_old_result(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    forms = []
    reports = []

    def choose(form):
        forms.append(form)
        if len(forms) == 1:
            fill(form)
            form._accept()
            return QDialog.DialogCode.Accepted
        assert form is forms[0] and form.result_scenario is not None
        return QDialog.DialogCode.Rejected

    def show(result):
        reports.append(result.report)
        result.edit_button.click()
        return result.result()

    monkeypatch.setattr(DepositScenarioDialog, "exec", choose)
    monkeypatch.setattr(DepositForecastDialog, "exec", show)
    window.request_deposit()
    qtbot.waitUntil(lambda: len(forms) == 2 and not window._busy, timeout=15000)
    assert len(reports) == 1 and not window._jobs
    qtbot.waitUntil(lambda: not shiboken6.isValid(forms[0]), timeout=15000)


def test_error_after_return_keeps_same_inputs_and_hides_internal_message(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    forms = []
    reports = []
    calls = []
    original = module.forecast_deposit

    def calculate(value):
        calls.append(value)
        if len(calls) == 2:
            raise RuntimeError("PRIVATE_INTERNAL_MARKER")
        return original(value)

    def choose(form):
        forms.append(form)
        if len(forms) == 1:
            fill(form)
        else:
            assert form is forms[0]
        if len(forms) == 3:
            assert form.initial.text() == "0,03"
            assert "Не удалось рассчитать" in form.feedback.text()
            assert "PRIVATE_INTERNAL_MARKER" not in form.feedback.text()
            return QDialog.DialogCode.Rejected
        form.initial.setText("0,01" if len(forms) == 1 else "0,03")
        form._accept()
        return QDialog.DialogCode.Accepted

    def show(result):
        reports.append(result.report)
        result.edit_button.click()
        return result.result()

    monkeypatch.setattr(module, "forecast_deposit", calculate)
    monkeypatch.setattr(DepositScenarioDialog, "exec", choose)
    monkeypatch.setattr(DepositForecastDialog, "exec", show)
    window.request_deposit()
    qtbot.waitUntil(lambda: len(forms) == 3 and not window._busy, timeout=15000)
    assert len(reports) == 1 and len(calls) == 2
    assert calls[0].rates[0].revision_id == calls[1].rates[0].revision_id
    qtbot.waitUntil(lambda: not shiboken6.isValid(forms[0]), timeout=15000)


def test_result_exception_disposes_input_and_result(qtbot, monkeypatch):
    form = DepositScenarioDialog()
    fill(form)
    form._accept()
    report = module.forecast_deposit(form.result_scenario)
    window = MainWindow()
    qtbot.addWidget(window)
    results = []

    def fail(result):
        results.append(result)
        raise RuntimeError("Вымышленный сбой окна")

    monkeypatch.setattr(DepositForecastDialog, "exec", fail)
    with pytest.raises(RuntimeError):
        window._show_deposit(form, report)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(form) and not shiboken6.isValid(results[0])


def test_real_modal_loop_returns_to_form_then_escape_keeps_single_forecast(qtbot, monkeypatch):
    from PySide6.QtCore import QTimer

    window = MainWindow()
    qtbot.addWidget(window)
    forms = []
    reports = []

    def choose(form):
        forms.append(form)
        if len(forms) == 1:

            def accept():
                fill(form)
                form._accept()

            QTimer.singleShot(0, accept)
        else:
            assert form is forms[0] and form.initial.text() == "0,01"
            QTimer.singleShot(0, lambda: qtbot.keyClick(form, Qt.Key.Key_Escape))
        return QDialog.exec(form)

    def show(result):
        reports.append(result.report)
        QTimer.singleShot(0, result.edit_button.click)
        return QDialog.exec(result)

    monkeypatch.setattr(DepositScenarioDialog, "exec", choose)
    monkeypatch.setattr(DepositForecastDialog, "exec", show)
    window.request_deposit()
    qtbot.waitUntil(lambda: len(forms) == 2 and not window._busy, timeout=15000)
    qtbot.waitUntil(lambda: not shiboken6.isValid(forms[0]), timeout=15000)
    assert len(reports) == 1 and not window._jobs
