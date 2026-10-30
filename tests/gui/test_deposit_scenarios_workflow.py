import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import QDialog

from personal_finance.domain.deposit_scenarios import SavedDepositScenario
from personal_finance.domain.errors import ValidationError
from personal_finance.presentation import deposit_scenarios_workflow as m
from personal_finance.presentation.deposit_scenarios import ScenarioAction, ScenarioRequest
from personal_finance.presentation.main_window import MainWindow
from tests.unit.test_deposits import scenario, uid


def record():
    return SavedDepositScenario(
        uid(50), uid(51), "Пример", scenario(), False, "2026-10-30T00:00:00Z"
    )


@pytest.fixture
def context(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    session = SimpleNamespace(
        mutex=threading.RLock(),
        ensure_open=Mock(),
        close=Mock(),
        deposit_scenarios=Mock(),
        ledger=None,
    )
    window.session = session
    controller = m.DepositScenariosController(window, session)
    yield window, session, controller
    window._pool.waitForDone()
    window.session = None


def test_name_dialog_requires_name_and_preserves_user_input(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    dialog = m.ScenarioNameDialog("", window)
    qtbot.addWidget(dialog)
    dialog.name.setText("   ")
    dialog._accept()
    assert dialog.result() != QDialog.DialogCode.Accepted and dialog.feedback.text()
    dialog.name.setText("Сценарий")
    dialog._accept()
    assert dialog.result() == QDialog.DialogCode.Accepted and dialog.name.text() == "Сценарий"


def test_history_never_edits_old_revision_and_uses_selected_row(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    one = record()
    two = replace(one, revision_id=uid(52), name="Вторая версия")
    dialog = m.ScenarioHistoryDialog([one, two], window)
    qtbot.addWidget(dialog)
    assert dialog.request is None and not dialog.copy.isEnabled()
    dialog.choose(ScenarioAction.COPY)
    assert dialog.request is None
    dialog.table.selectRow(1)
    dialog.choose(ScenarioAction.EDIT)
    assert dialog.request is None
    dialog.choose(ScenarioAction.COPY)
    assert dialog.request.record is two and dialog.request.action is ScenarioAction.COPY
    assert one.name == "Пример"


def test_archive_runs_off_gui_and_passes_exact_revision(context, qtbot, monkeypatch):
    window, session, controller = context
    value = record()
    calls = []

    def archive(*args, **kwargs):
        assert QThread.currentThread() != window.thread()
        session.ensure_open.assert_called()
        calls.append((args, kwargs))
        return replace(value, is_deleted=True)

    session.deposit_scenarios.archive.side_effect = archive
    saved = []
    monkeypatch.setattr(controller, "_saved", saved.append)
    controller.handle(ScenarioRequest(ScenarioAction.ARCHIVE, value))
    qtbot.waitUntil(lambda: not window._busy and len(saved) == 1)
    assert calls[0][0] == (value.id, value.revision_id)
    assert calls[0][1]["archived"] is True and calls[0][1]["command_id"]
    assert not value.is_deleted and saved[0].is_deleted


def test_failed_worker_is_delivered_after_job_cleanup(context, qtbot):
    window, session, controller = context
    result = []

    def fail():
        raise ValidationError("Проверьте условия")

    def received(message):
        assert not window._busy and not window._jobs
        result.append(message)

    controller._job(fail, lambda _: pytest.fail("unexpected success"), received, "Проверка…")
    qtbot.waitUntil(lambda: bool(result))
    assert result == ["Проверьте условия"]


def test_late_worker_result_cannot_open_dialog_for_another_book(context, qtbot):
    window, session, controller = context
    entered, finish = threading.Event(), threading.Event()
    delivered = []

    def action():
        entered.set()
        assert finish.wait(5)
        return 1

    controller._job(action, delivered.append, delivered.append, "Проверка…")
    qtbot.waitUntil(entered.is_set)
    window.session = None
    finish.set()
    qtbot.waitUntil(lambda: not window._busy)
    assert delivered == []


@pytest.mark.parametrize("kind", ["new", "edit", "copy", "rename"])
def test_save_uses_exact_expected_revision_and_new_command(context, qtbot, monkeypatch, kind):
    window, session, controller = context
    value = record()
    calls, saved = [], []

    def save(*args, **kwargs):
        assert QThread.currentThread() != window.thread()
        calls.append((args, kwargs))
        return value

    session.deposit_scenarios.save.side_effect = save
    session.deposit_scenarios.rename.side_effect = save
    monkeypatch.setattr(controller, "_saved", saved.append)

    def name_form(dialog):
        dialog.name.setText("Новое название")
        dialog._accept()
        return dialog.result()

    monkeypatch.setattr(m.ScenarioNameDialog, "exec", name_form)
    controller.save_name(
        value.scenario,
        None if kind == "new" else value,
        copy=kind == "copy",
        rename=kind == "rename",
    )
    qtbot.waitUntil(lambda: not window._busy and len(saved) == 1)
    args, kwargs = calls[0]
    assert kwargs["command_id"]
    if kind == "rename":
        assert args == (value.id, value.revision_id, "Новое название")
    else:
        assert args == ("Новое название", value.scenario)
        assert kwargs["identifier"] == (value.id if kind == "edit" else None)
        assert kwargs["expected_revision_id"] == (value.revision_id if kind == "edit" else None)


def test_save_error_keeps_same_name_form_and_retries_without_overwrite(context, qtbot, monkeypatch):
    window, session, controller = context
    value = record()
    forms, saved = [], []
    session.deposit_scenarios.save.side_effect = [ValidationError("Название занято"), value]
    monkeypatch.setattr(controller, "_saved", saved.append)

    def name_form(dialog):
        forms.append(dialog)
        if len(forms) == 1:
            dialog.name.setText("Занятое")
        else:
            assert dialog is forms[0] and dialog.name.text() == "Занятое"
            assert dialog.feedback.text() == "Название занято"
            dialog.name.setText("Свободное")
        dialog._accept()
        return dialog.result()

    monkeypatch.setattr(m.ScenarioNameDialog, "exec", name_form)
    controller.save_name(value.scenario, None)
    qtbot.waitUntil(lambda: not window._busy and len(saved) == 1)
    assert len(forms) == 2 and session.deposit_scenarios.save.call_count == 2


def test_cancel_edit_loads_exact_conditions_and_never_saves(context, monkeypatch):
    window, session, controller = context
    value = record()
    opened = []

    def form(dialog):
        dialog._accept()
        assert dialog.result_scenario == value.scenario
        dialog.reject()
        opened.append(True)
        return dialog.result()

    monkeypatch.setattr(m.DepositScenarioDialog, "exec", form)
    monkeypatch.setattr(controller, "open", lambda: None)
    controller.edit(value)
    assert opened and not session.deposit_scenarios.save.called


def test_result_requires_explicit_save_click(context, qtbot):
    window, session, controller = context
    report = m.forecast_deposit(record().scenario)
    dialog = m.SaveForecastDialog(report, window, editing=True)
    qtbot.addWidget(dialog)
    assert not dialog.save_requested and not dialog.edit_requested
    assert dialog.save_button.text() in dialog.storage_note.text()
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert dialog.save_requested and not dialog.edit_requested
    assert report.scenario == record().scenario


def test_real_book_workflow_preserves_history_and_financial_journal(qtbot, tmp_path, monkeypatch):
    from personal_finance.application.vault import create_vault, open_vault
    from tests.conftest import PASSWORD

    session, _ = create_vault(tmp_path / "workflow.pfvault", "Синтетическая книга", PASSWORD)
    window = MainWindow()
    qtbot.addWidget(window)
    window.session = session
    controller = m.DepositScenariosController(window, session)
    saved = []
    monkeypatch.setattr(controller, "_saved", saved.append)
    monkeypatch.setattr(controller, "open", lambda: None)
    names = iter(["Первый", "Изменённый", "Копия", "Новое имя", "Копия истории"])

    def choose_name(dialog):
        dialog.name.setText(next(names))
        dialog._accept()
        return dialog.result()

    monkeypatch.setattr(m.ScenarioNameDialog, "exec", choose_name)
    try:
        value = scenario()
        original_ledger = session.ledger.operations()
        controller.save_name(value, None)
        qtbot.waitUntil(lambda: len(saved) == 1 and not window._busy)
        first = saved[-1]
        changed = replace(value, initial=replace(value.initial, minor=value.initial.minor + 100))
        controller.save_name(changed, first)
        qtbot.waitUntil(lambda: len(saved) == 2 and not window._busy)
        second = saved[-1]
        assert second.id == first.id and second.revision_id != first.revision_id

        controller.save_name(second.scenario, second, copy=True)
        qtbot.waitUntil(lambda: len(saved) == 3 and not window._busy)
        copied = saved[-1]
        assert copied.id != first.id and copied.scenario == second.scenario

        controller.save_name(second.scenario, second, rename=True)
        qtbot.waitUntil(lambda: len(saved) == 4 and not window._busy)
        renamed = saved[-1]
        assert renamed.id == first.id and renamed.name == "Новое имя"

        controller.handle(ScenarioRequest(ScenarioAction.ARCHIVE, renamed))
        qtbot.waitUntil(lambda: len(saved) == 5 and not window._busy)
        assert saved[-1].is_deleted
        controller.handle(ScenarioRequest(ScenarioAction.RESTORE, saved[-1]))
        qtbot.waitUntil(lambda: len(saved) == 6 and not window._busy)
        assert not saved[-1].is_deleted

        controller.save_name(first.scenario, first, copy=True)
        qtbot.waitUntil(lambda: len(saved) == 7 and not window._busy)
        assert saved[-1].id != first.id and saved[-1].scenario == value
        history = session.deposit_scenarios.history(first.id)
        assert len(history) == 5 and first in history and second in history
        assert (
            session.ledger.operations() == original_ledger and session.catalog.institutions() == []
        )
        session.check_integrity()
        session.close()
        reopened = open_vault(tmp_path / "workflow.pfvault", PASSWORD)
        try:
            assert reopened.deposit_scenarios.history(first.id) == history
            assert len(reopened.deposit_scenarios.items()) == 3
            reopened.check_integrity()
        finally:
            reopened.close()
    finally:
        window.session = None
        session.close()


def test_form_recalculation_saves_only_last_explicit_result(context, qtbot, monkeypatch):
    window, session, controller = context
    forms, reports, saved, threads = [], [], [], []
    value = record()
    session.deposit_scenarios.save.return_value = value
    monkeypatch.setattr(controller, "_saved", saved.append)
    calculate = m.forecast_deposit

    def worker(scenario_value):
        threads.append(QThread.currentThread())
        return calculate(scenario_value)

    def fill(form):
        forms.append(form)
        assert not window._busy
        if len(forms) == 1:
            form.load_scenario(value.scenario)
        else:
            assert form is forms[0]
        form.initial.setText(str(2000 * len(forms)))
        form._accept()
        return form.result()

    def result(dialog):
        assert not window._busy
        reports.append(dialog.report)
        if len(reports) == 1:
            dialog.edit_button.click()
        else:
            dialog.save_button.click()
        return dialog.result()

    def name(dialog):
        dialog.name.setText("Сохранить последний")
        dialog._accept()
        return dialog.result()

    monkeypatch.setattr(m, "forecast_deposit", worker)
    monkeypatch.setattr(m.DepositScenarioDialog, "exec", fill)
    monkeypatch.setattr(m.SaveForecastDialog, "exec", result)
    monkeypatch.setattr(m.ScenarioNameDialog, "exec", name)
    controller.edit(None)
    qtbot.waitUntil(lambda: len(saved) == 1 and not window._busy)
    assert len(forms) == len(reports) == len(threads) == 2
    assert all(thread != window.thread() for thread in threads)
    assert reports[0].scenario.initial.minor == 200000
    assert reports[1].scenario.initial.minor == 400000
    assert session.deposit_scenarios.save.call_count == 1
    assert session.deposit_scenarios.save.call_args.args[1] == reports[1].scenario


def test_cancel_name_after_forecast_does_not_save(context, monkeypatch):
    window, session, controller = context
    opened = []
    monkeypatch.setattr(m.ScenarioNameDialog, "exec", lambda _: QDialog.DialogCode.Rejected)
    monkeypatch.setattr(controller, "open", lambda: opened.append(True))
    controller.save_name(record().scenario, None)
    assert opened == [True] and not session.deposit_scenarios.save.called


def test_unexpected_worker_error_does_not_echo_payload(context, qtbot):
    window, session, controller = context
    delivered = []

    def fail():
        raise RuntimeError("synthetic_private_payload_marker")

    controller._job(
        fail, lambda _: pytest.fail("unexpected success"), delivered.append, "Проверка…"
    )
    qtbot.waitUntil(lambda: bool(delivered) and not window._busy)
    assert "synthetic_private_payload_marker" not in delivered[0]


def test_stale_controller_or_busy_window_never_schedules_action(context, monkeypatch):
    window, session, controller = context
    called = []
    monkeypatch.setattr(controller, "edit", lambda *args, **kwargs: called.append(True))
    window._busy = True
    controller.handle(ScenarioRequest(ScenarioAction.CREATE, None))
    window._busy = False
    window.session = None
    controller.handle(ScenarioRequest(ScenarioAction.CREATE, None))
    assert not called and not session.deposit_scenarios.save.called


@pytest.mark.parametrize("saved", [False, True])
def test_result_explains_whether_conditions_are_already_saved(context, qtbot, saved):
    window, session, controller = context
    dialog = m.DepositForecastDialog(
        m.forecast_deposit(record().scenario), window, conditions_saved=saved
    )
    qtbot.addWidget(dialog)
    assert ("сохранённой версии" in dialog.storage_note.text()) is saved
    assert ("на диске не сохраняются" in dialog.storage_note.text()) is not saved
