"""Проверка действий прогноза при чужой Qt-обёртке в результате addButton."""

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPushButton

from personal_finance.domain.deposits import forecast_deposit
from personal_finance.presentation.deposit_scenarios_workflow import SaveForecastDialog
from personal_finance.presentation.main_window import MainWindow
from tests.unit.test_deposits import scenario


@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("bad_return_on", [1, 2])
def test_forecast_actions_preserve_widgets_when_button_box_returns_foreign_wrapper(
    qtbot, monkeypatch, editing, bad_return_on
):
    window = MainWindow()
    qtbot.addWidget(window)
    foreign_wrapper = QThread(window)
    original = QDialogButtonBox.addButton
    calls = []

    def unreliable_return(box, *args):
        result = original(box, *args)
        calls.append(args)
        return foreign_wrapper if len(calls) == bad_return_on else result

    monkeypatch.setattr(QDialogButtonBox, "addButton", unreliable_return)
    dialog = SaveForecastDialog(forecast_deposit(scenario()), window, editing=editing)
    qtbot.addWidget(dialog)
    assert isinstance(dialog.edit_button, QPushButton)
    assert isinstance(dialog.save_button, QPushButton)
    assert len(calls) == 2
    expected_save = "Сохранить изменения…" if editing else "Сохранить сценарий…"
    assert dialog.save_button.text() == expected_save
    assert expected_save in dialog.storage_note.text()
    box = dialog.findChild(QDialogButtonBox)
    assert box.buttonRole(dialog.save_button) == QDialogButtonBox.ButtonRole.ActionRole
    assert box.buttonRole(dialog.edit_button) == QDialogButtonBox.ButtonRole.ActionRole
    if bad_return_on == 1:
        dialog.edit_button.click()
        assert dialog.edit_requested and not dialog.save_requested
    else:
        dialog.save_button.click()
        assert dialog.save_requested and not dialog.edit_requested
    assert dialog.result() == QDialog.DialogCode.Accepted
