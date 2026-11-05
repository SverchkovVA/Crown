from dataclasses import FrozenInstanceError

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QDialog

from personal_finance.domain.planning_scenarios import SavedPlanningScenario
from personal_finance.domain.errors import ValidationError
from personal_finance.presentation.planning_scenarios import SavedPlanningDialog, PlanningAction
from tests.unit.test_deposits import uid
from tests.unit.test_planning_scenarios import budget as scenario


def records():
    return [
        SavedPlanningScenario(
            uid(20), uid(21), "Первый", scenario(), False, "2026-10-30T00:00:00Z"
        ),
        SavedPlanningScenario(
            uid(22), uid(23), "Второй", scenario(), False, "2026-10-30T00:00:01Z"
        ),
        SavedPlanningScenario(
            uid(24), uid(25), "Архивный", scenario(), True, "2026-10-30T00:00:02Z"
        ),
    ]


def test_no_selection_never_falls_back_to_first_record(qtbot):
    dialog = SavedPlanningDialog(records())
    qtbot.addWidget(dialog)
    assert dialog.model.rowCount() == 2 and dialog.selected() is None
    assert dialog.buttons[PlanningAction.CREATE].isEnabled()
    assert all(
        not button.isEnabled()
        for key, button in dialog.buttons.items()
        if key is not PlanningAction.CREATE
    )
    dialog.table.selectionModel().setCurrentIndex(
        dialog.model.index(0, 0), QItemSelectionModel.SelectionFlag.NoUpdate
    )
    assert dialog.table.currentIndex().isValid() and dialog.selected() is None
    dialog.choose(PlanningAction.EDIT)
    assert dialog.request is None and dialog.result() != QDialog.DialogCode.Accepted


@pytest.mark.parametrize(
    "action",
    [
        PlanningAction.CALCULATE,
        PlanningAction.EDIT,
        PlanningAction.COPY,
        PlanningAction.RENAME,
        PlanningAction.HISTORY,
        PlanningAction.ARCHIVE,
    ],
)
def test_selected_action_returns_exact_frozen_version(qtbot, action):
    original = records()
    dialog = SavedPlanningDialog(original)
    qtbot.addWidget(dialog)
    dialog.table.selectRow(1)
    chosen = dialog.visible[1]
    dialog.choose(action)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.request.action is action and dialog.request.record is chosen
    assert dialog.records == tuple(sorted(original, key=lambda v: (v.name.casefold(), v.id)))
    with pytest.raises(FrozenInstanceError):
        dialog.request.action = PlanningAction.CREATE


def test_filter_rebuild_clears_selection_and_does_not_choose_hidden_record(qtbot):
    dialog = SavedPlanningDialog(records())
    qtbot.addWidget(dialog)
    dialog.table.selectRow(0)
    assert dialog.selected() is not None
    dialog.search.setText("ПЕРВЫЙ")
    assert len(dialog.visible) == 1 and dialog.visible[0].name == "Первый"
    assert dialog.selected() is None and not dialog.buttons[PlanningAction.EDIT].isEnabled()
    dialog.choose(PlanningAction.EDIT)
    assert dialog.request is None
    dialog.search.setText("нет такого")
    assert not dialog.visible and dialog.model.rowCount() == 0


def test_archived_selection_requests_restore_and_filter_keeps_data(qtbot):
    original = records()
    dialog = SavedPlanningDialog(original)
    qtbot.addWidget(dialog)
    dialog.archived.setChecked(True)
    assert len(dialog.visible) == 3
    index = next(i for i, v in enumerate(dialog.visible) if v.is_deleted)
    dialog.table.selectRow(index)
    assert dialog.buttons[PlanningAction.ARCHIVE].text() == "Вернуть из архива"
    dialog.choose(PlanningAction.ARCHIVE)
    assert dialog.request.action is PlanningAction.RESTORE and dialog.request.record.is_deleted
    assert original[-1].is_deleted


def test_create_is_explicit_without_selected_payload(qtbot):
    dialog = SavedPlanningDialog(records())
    qtbot.addWidget(dialog)
    dialog.table.selectRow(0)
    dialog.choose(PlanningAction.CREATE)
    assert dialog.request.action is PlanningAction.CREATE and dialog.request.record is None


def test_cancel_and_invalid_action_emit_no_request(qtbot):
    dialog = SavedPlanningDialog(records())
    qtbot.addWidget(dialog)
    dialog.choose("delete")
    assert dialog.request is None
    dialog.reject()
    assert dialog.request is None and dialog.result() == QDialog.DialogCode.Rejected


def test_selected_name_is_plain_text_and_duplicate_ids_refused(qtbot):
    original = records()
    record = SavedPlanningScenario(
        uid(30), uid(31), "<b>Имя</b>", scenario(), False, "2026-10-30T00:00:00Z"
    )
    dialog = SavedPlanningDialog([record])
    qtbot.addWidget(dialog)
    dialog.table.selectRow(0)
    assert dialog.selection_info.textFormat() == Qt.TextFormat.PlainText
    assert "<b>Имя</b>" in dialog.selection_info.text()
    with pytest.raises(ValidationError):
        SavedPlanningDialog(original + [original[0]])


def test_enter_in_search_never_creates_or_applies_action(qtbot):
    dialog = SavedPlanningDialog(records())
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.search.setFocus()
    qtbot.keyClick(dialog.search, Qt.Key.Key_Return)
    assert dialog.request is None and dialog.result() != QDialog.DialogCode.Accepted
    assert all(not button.autoDefault() for button in dialog.buttons.values())


def test_switching_kind_resets_selection_and_does_not_mix_records(qtbot):
    from personal_finance.domain.planning_scenarios import PlanningKind
    from tests.unit.test_planning_scenarios import goal

    goal_record = SavedPlanningScenario(
        uid(80), uid(81), "Первый", goal(), False, "2026-11-05T00:00:00Z"
    )
    dialog = SavedPlanningDialog([*records(), goal_record])
    qtbot.addWidget(dialog)
    dialog.table.selectRow(0)
    dialog.kind.setCurrentIndex(dialog.kind.findData(PlanningKind.SAVINGS_GOAL.value))
    assert dialog.visible == (goal_record,) and dialog.selected() is None
    assert not dialog.buttons[PlanningAction.EDIT].isEnabled()
    dialog.table.selectRow(0)
    dialog.choose(PlanningAction.EDIT)
    assert dialog.request.kind == PlanningKind.SAVINGS_GOAL
    assert dialog.request.record == goal_record


def test_create_uses_selected_kind_without_borrowing_another_record(qtbot):
    from personal_finance.domain.planning_scenarios import PlanningKind

    dialog = SavedPlanningDialog(records(), kind=PlanningKind.SAVINGS_GOAL)
    qtbot.addWidget(dialog)
    assert not dialog.visible
    dialog.choose(PlanningAction.CREATE)
    assert dialog.request.kind == PlanningKind.SAVINGS_GOAL and dialog.request.record is None


def test_history_returns_selected_immutable_version_and_never_an_edit_request(qtbot):
    from dataclasses import replace

    from personal_finance.presentation.planning_scenarios import PlanningHistoryDialog

    first = records()[0]
    second = replace(first, revision_id=uid(90), name="Изменённый")
    dialog = PlanningHistoryDialog([first, second])
    qtbot.addWidget(dialog)
    assert not dialog.calculate.isEnabled() and not dialog.copy.isEnabled()
    dialog.choose(PlanningAction.EDIT)
    assert dialog.request is None
    dialog.table.selectRow(0)
    dialog.choose(PlanningAction.COPY)
    assert dialog.request.record is first and dialog.request.action is PlanningAction.COPY


def test_history_rejects_mixed_entities_kinds_and_duplicate_versions(qtbot):
    from dataclasses import replace

    from personal_finance.presentation.planning_scenarios import PlanningHistoryDialog
    from tests.unit.test_planning_scenarios import goal

    first = records()[0]
    for values in (
        [],
        [first, first],
        records()[:2],
        [first, replace(first, revision_id=uid(91), scenario=goal())],
    ):
        with pytest.raises(ValidationError):
            PlanningHistoryDialog(values)
