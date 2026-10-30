from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from personal_finance.presentation import main_window as module
from personal_finance.presentation.main_window import MainWindow


@pytest.mark.parametrize("supported", [False, True])
def test_saved_scenarios_menu_follows_book_capability_and_busy(qtbot, supported):
    window = MainWindow()
    qtbot.addWidget(window)
    assert not window.saved_deposits_action.isEnabled()
    assert window.deposit_action.isEnabled()
    session = SimpleNamespace(
        ledger=None, deposit_scenarios=Mock() if supported else None, close=Mock()
    )
    try:
        window.session = session
        window._update_report_action()
        assert window.saved_deposits_action.isEnabled() is supported
        window._busy = True
        window._update_report_action()
        assert not window.saved_deposits_action.isEnabled()
        assert not window.deposit_action.isEnabled()
    finally:
        window.session = None
        window._busy = False


def test_saved_scenarios_request_never_opens_for_unsupported_or_busy_book(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    controller = Mock()
    monkeypatch.setattr(module, "DepositScenariosController", controller)
    window.request_saved_deposits()
    controller.assert_not_called()
    session = SimpleNamespace(ledger=None, deposit_scenarios=None, close=Mock())
    try:
        window.session = session
        window.request_saved_deposits()
        controller.assert_not_called()
        session.deposit_scenarios = Mock()
        window._busy = True
        window.request_saved_deposits()
        controller.assert_not_called()
        window._busy = False
        window.request_saved_deposits()
        controller.assert_called_once_with(window, session)
        controller.return_value.open.assert_called_once_with()
    finally:
        window.session = None
        window._busy = False
