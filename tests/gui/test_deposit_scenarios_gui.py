from dataclasses import FrozenInstanceError

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QDialog

from personal_finance.domain.deposit_scenarios import SavedDepositScenario
from personal_finance.domain.errors import ValidationError
from personal_finance.presentation.deposit_scenarios import SavedScenariosDialog, ScenarioAction
from tests.unit.test_deposits import scenario, uid


def records():
    return [
        SavedDepositScenario(uid(20), uid(21), "Первый", scenario(), False, "2026-10-30T00:00:00Z"),
        SavedDepositScenario(uid(22), uid(23), "Второй", scenario(), False, "2026-10-30T00:00:01Z"),
        SavedDepositScenario(
            uid(24), uid(25), "Архивный", scenario(), True, "2026-10-30T00:00:02Z"
        ),
    ]


def test_no_selection_never_falls_back_to_first_record(qtbot):
    dialog = SavedScenariosDialog(records())
    qtbot.addWidget(dialog)
    assert dialog.model.rowCount() == 2 and dialog.selected() is None
    assert dialog.buttons[ScenarioAction.CREATE].isEnabled()
    assert all(
        not button.isEnabled()
        for key, button in dialog.buttons.items()
        if key is not ScenarioAction.CREATE
    )
    dialog.table.selectionModel().setCurrentIndex(
        dialog.model.index(0, 0), QItemSelectionModel.SelectionFlag.NoUpdate
    )
    assert dialog.table.currentIndex().isValid() and dialog.selected() is None
    dialog.choose(ScenarioAction.EDIT)
    assert dialog.request is None and dialog.result() != QDialog.DialogCode.Accepted


@pytest.mark.parametrize(
    "action",
    [
        ScenarioAction.CALCULATE,
        ScenarioAction.EDIT,
        ScenarioAction.COPY,
        ScenarioAction.RENAME,
        ScenarioAction.HISTORY,
        ScenarioAction.ARCHIVE,
    ],
)
def test_selected_action_returns_exact_frozen_version(qtbot, action):
    original = records()
    dialog = SavedScenariosDialog(original)
    qtbot.addWidget(dialog)
    dialog.table.selectRow(1)
    chosen = dialog.visible[1]
    dialog.choose(action)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.request.action is action and dialog.request.record is chosen
    assert dialog.records == tuple(sorted(original, key=lambda v: (v.name.casefold(), v.id)))
    with pytest.raises(FrozenInstanceError):
        dialog.request.action = ScenarioAction.CREATE


def test_filter_rebuild_clears_selection_and_does_not_choose_hidden_record(qtbot):
    dialog = SavedScenariosDialog(records())
    qtbot.addWidget(dialog)
    dialog.table.selectRow(0)
    assert dialog.selected() is not None
    dialog.search.setText("ПЕРВЫЙ")
    assert len(dialog.visible) == 1 and dialog.visible[0].name == "Первый"
    assert dialog.selected() is None and not dialog.buttons[ScenarioAction.EDIT].isEnabled()
    dialog.choose(ScenarioAction.EDIT)
    assert dialog.request is None
    dialog.search.setText("нет такого")
    assert not dialog.visible and dialog.model.rowCount() == 0


def test_archived_selection_requests_restore_and_filter_keeps_data(qtbot):
    original = records()
    dialog = SavedScenariosDialog(original)
    qtbot.addWidget(dialog)
    dialog.archived.setChecked(True)
    assert len(dialog.visible) == 3
    index = next(i for i, v in enumerate(dialog.visible) if v.is_deleted)
    dialog.table.selectRow(index)
    assert dialog.buttons[ScenarioAction.ARCHIVE].text() == "Вернуть из архива"
    dialog.choose(ScenarioAction.ARCHIVE)
    assert dialog.request.action is ScenarioAction.RESTORE and dialog.request.record.is_deleted
    assert original[-1].is_deleted


def test_create_is_explicit_without_selected_payload(qtbot):
    dialog = SavedScenariosDialog(records())
    qtbot.addWidget(dialog)
    dialog.table.selectRow(0)
    dialog.choose(ScenarioAction.CREATE)
    assert dialog.request.action is ScenarioAction.CREATE and dialog.request.record is None


def test_cancel_and_invalid_action_emit_no_request(qtbot):
    dialog = SavedScenariosDialog(records())
    qtbot.addWidget(dialog)
    dialog.choose("delete")
    assert dialog.request is None
    dialog.reject()
    assert dialog.request is None and dialog.result() == QDialog.DialogCode.Rejected


def test_selected_name_is_plain_text_and_duplicate_ids_refused(qtbot):
    original = records()
    record = SavedDepositScenario(
        uid(30), uid(31), "<b>Имя</b>", scenario(), False, "2026-10-30T00:00:00Z"
    )
    dialog = SavedScenariosDialog([record])
    qtbot.addWidget(dialog)
    dialog.table.selectRow(0)
    assert dialog.selection_info.textFormat() == Qt.TextFormat.PlainText
    assert "<b>Имя</b>" in dialog.selection_info.text()
    with pytest.raises(ValidationError):
        SavedScenariosDialog(original + [original[0]])


def test_enter_in_search_never_creates_or_applies_action(qtbot):
    dialog = SavedScenariosDialog(records())
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.search.setFocus()
    qtbot.keyClick(dialog.search, Qt.Key.Key_Return)
    assert dialog.request is None and dialog.result() != QDialog.DialogCode.Accepted
    assert all(not button.autoDefault() for button in dialog.buttons.values())
