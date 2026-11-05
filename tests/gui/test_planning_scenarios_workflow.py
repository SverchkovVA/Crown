"""Подготовленные сценарии встроенного процесса; новая схема не включается."""

import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QDialog

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.planning_scenarios import PlanningKind, SavedPlanningScenario
from personal_finance.presentation import planning_scenarios_workflow as module
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.planning_scenarios import PlanningAction, PlanningRequest
from tests.unit.test_deposits import uid
from tests.unit.test_planning_scenarios import goal


def record():
    return SavedPlanningScenario(
        uid(50),
        uid(51),
        "Цель для проверки",
        goal(),
        False,
        "2026-11-05T00:00:00Z",
    )


@pytest.fixture
def context(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    session = SimpleNamespace(
        mutex=threading.RLock(),
        ensure_open=Mock(),
        close=Mock(),
        ledger=None,
        deposit_scenarios=None,
    )
    service = Mock()
    window.session = session
    controller = module.PlanningScenariosController(window, session, service, window._report_forms)
    yield window, session, service, controller
    window._pool.waitForDone()
    window._report_forms.clear()
    window.session = None


def current(window, kind):
    form = window._report_forms.stack.currentWidget().widget()
    assert isinstance(form, kind) and not form.isWindow()
    return form


def test_name_is_validated_without_losing_input(context):
    window, _, _, _ = context
    form = module.PlanningNameDialog("", window)
    form.name.setText("   ")
    form._accept()
    assert form.result() != QDialog.DialogCode.Accepted and form.feedback.text()
    form.name.setText("ΐ" * 120)
    form._accept()
    assert form.result() != QDialog.DialogCode.Accepted and form.name.text() == "ΐ" * 120
    form.name.setText("  Цель  ")
    form._accept()
    assert form.result() == QDialog.DialogCode.Accepted and form.name.text() == "Цель"


@pytest.mark.parametrize("kind", ["new", "edit", "copy", "rename"])
def test_inline_save_runs_off_gui_and_uses_exact_version(context, qtbot, monkeypatch, kind):
    window, session, service, controller = context
    calls, saved = [], []
    value = record()

    def store(*args, **kwargs):
        assert QThread.currentThread() != window.thread()
        session.ensure_open.assert_called()
        calls.append((args, kwargs))
        return value

    service.save.side_effect = service.rename.side_effect = store
    monkeypatch.setattr(controller, "_saved", saved.append)
    controller.save_name(
        value.scenario,
        None if kind == "new" else value,
        copy=kind == "copy",
        rename=kind == "rename",
    )
    form = current(window, module.PlanningNameDialog)
    form.name.setText("Название")
    form._accept()
    qtbot.waitUntil(lambda: not window._busy and len(saved) == 1)
    args, options = calls[0]
    assert options["command_id"]
    if kind == "rename":
        assert args == (value.id, value.revision_id, "Название")
    else:
        assert args == ("Название", value.scenario)
        assert options["identifier"] == (value.id if kind == "edit" else None)
        assert options["expected_revision_id"] == (value.revision_id if kind == "edit" else None)


@pytest.mark.parametrize("change_name", [False, True])
def test_failed_save_preserves_form_and_exact_retry_command(
    context, qtbot, monkeypatch, change_name
):
    window, _, service, controller = context
    value = record()
    saved = []
    service.save.side_effect = [ValidationError("Повторите сохранение"), value]
    monkeypatch.setattr(controller, "_saved", saved.append)
    controller.save_name(value.scenario, None)
    form = current(window, module.PlanningNameDialog)
    form.name.setText("Черновик")
    form._accept()
    qtbot.waitUntil(lambda: not window._busy and window._report_forms.stack.count() == 1)
    assert current(window, module.PlanningNameDialog) is form
    assert form.name.text() == "Черновик" and form.feedback.text() == "Повторите сохранение"
    if change_name:
        form.name.setText("Исправленное")
    form._accept()
    qtbot.waitUntil(lambda: not window._busy and len(saved) == 1)
    first, second = service.save.call_args_list
    assert (first.kwargs["command_id"] != second.kwargs["command_id"]) == change_name


def test_archive_uses_selected_revision_in_worker(context, qtbot, monkeypatch):
    window, _, service, controller = context
    value = record()
    calls, saved = [], []

    def archive(*args, **kwargs):
        assert QThread.currentThread() != window.thread()
        calls.append((args, kwargs))
        return replace(value, is_deleted=True)

    service.archive.side_effect = archive
    monkeypatch.setattr(controller, "_saved", saved.append)
    controller.handle(PlanningRequest(PlanningAction.ARCHIVE, value.kind, value))
    qtbot.waitUntil(lambda: not window._busy and len(saved) == 1)
    args, options = calls[0]
    assert args == (value.id, value.revision_id)
    assert options["archived"] is True and options["command_id"]
    assert not value.is_deleted and saved[0].is_deleted


def test_late_worker_result_cannot_open_form_for_another_book(context, qtbot):
    window, _, _, controller = context
    entered, finish = threading.Event(), threading.Event()
    delivered = []

    def action():
        entered.set()
        assert finish.wait(5)
        return 1

    controller._job(action, delivered.append, delivered.append, "Подготовка…")
    qtbot.waitUntil(entered.is_set)
    window.session = None
    finish.set()
    qtbot.waitUntil(lambda: not window._busy)
    assert delivered == [] and window._report_forms.stack.count() == 0


def test_finance_errors_arrive_after_worker_cleanup(context, qtbot):
    window, _, _, controller = context
    received = []

    def fail():
        raise ValidationError("Проверьте условия")

    def failed(message):
        assert not window._busy and not window._jobs
        received.append(message)

    controller._job(fail, lambda _: pytest.fail("unexpected success"), failed, "Подготовка…")
    qtbot.waitUntil(lambda: bool(received))
    assert received == ["Проверьте условия"]


def test_cancel_never_saves_and_clearing_panel_never_reopens_list(context, monkeypatch):
    window, _, service, controller = context
    controller.save_name(goal(), None)
    form = current(window, module.PlanningNameDialog)
    monkeypatch.setattr(controller, "open", lambda: None)
    form.reject()
    assert not service.save.called and window._report_forms.stack.count() == 0
    controller.save_name(goal(), None)
    window._report_forms.clear()
    assert not service.save.called and window._report_forms.stack.count() == 0


def test_mismatched_kind_does_not_use_another_record(context):
    window, _, service, controller = context
    controller.handle(PlanningRequest(PlanningAction.ARCHIVE, PlanningKind.BUDGET, record()))
    assert not service.archive.called and not window._busy
