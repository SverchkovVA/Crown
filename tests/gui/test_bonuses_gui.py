"""Настоящие одноразовые книги и рабочие потоки; настройки изолированы."""

import threading
from datetime import date

import pytest
from PySide6.QtCore import QSettings, QThread
from PySide6.QtWidgets import QDialog

from personal_finance.application import vault
from personal_finance.domain.bonuses import BonusDraft, BonusKind
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import CategoryKind
from personal_finance.infrastructure.ocr_preferences import LocalOcrPreferences
from personal_finance.infrastructure.persistence import migration_runner as migrations
from personal_finance.presentation import bonuses as m
from personal_finance.presentation import main_window
from tests.conftest import PASSWORD


@pytest.fixture
def context(tmp_path, qtbot, monkeypatch):
    monkeypatch.setattr(m.QMessageBox, "warning", lambda *args: pytest.fail(str(args[-1])))
    original = migrations.initialize_schema
    monkeypatch.setattr(migrations, "SUPPORTED_SCHEMAS", (*migrations.SUPPORTED_SCHEMAS, "0014"))
    monkeypatch.setattr(vault, "initialize_schema", lambda e: original(e, target="0014"))
    monkeypatch.setattr(
        main_window,
        "LocalOcrPreferences",
        lambda: LocalOcrPreferences(
            QSettings(str(tmp_path / "ocr.ini"), QSettings.Format.IniFormat)
        ),
    )
    session, _ = vault.create_vault(tmp_path / "synthetic.pfvault", "Синтетическая книга", PASSWORD)
    window = main_window.MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    dialog = m.BonusesDialog(window, session)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: "загружен" in dialog.status.text(), timeout=10000)
    yield window, session, dialog
    window._pool.waitForDone()
    qtbot.waitUntil(lambda: not window._busy, timeout=10000)
    dialog.reject()
    window.session = None
    session.close()


def loaded(qtbot, dialog, predicate=lambda: True):
    qtbot.waitUntil(
        lambda: not dialog._busy and "загружен" in dialog.status.text() and predicate(),
        timeout=10000,
    )


def test_program_and_entry_forms_save_in_worker_edit_void_and_restore(context, qtbot, monkeypatch):
    window, session, dialog = context
    threads = []
    save = dialog.service.save_program

    def save_program(*a, **kw):
        threads.append(QThread.currentThread() != window.thread())
        return save(*a, **kw)

    monkeypatch.setattr(dialog.service, "save_program", save_program)

    def name(form):
        form.name.setText("Синтетическая программа")
        form._accept()
        return form.result()

    monkeypatch.setattr(m.BonusNameForm, "exec", name)
    dialog.create_program()
    loaded(qtbot, dialog, lambda: len(dialog._programs) == 1)
    assert threads == [True] and window.bonuses_action.isEnabled()
    category = next(c for c in dialog._categories if c.kind is CategoryKind.EXPENSE)

    def entry(form):
        form.occurred.setText("31.10.2026")
        form.units.setText("1,25")
        form.category.setCurrentIndex(form.category.findData(category.id))
        form._accept()
        return form.result()

    money = session.ledger.operations(), session.ledger.balances()
    monkeypatch.setattr(m.BonusEntryForm, "exec", entry)
    dialog.create_entry()
    loaded(qtbot, dialog, lambda: len(dialog._entries) == 1)
    first = dialog._entries[0]
    assert first.draft.units_minor == 125 and first.draft.category_id == category.id
    dialog.tabs.setCurrentWidget(dialog.entry_page)
    assert not dialog.edit.isEnabled()
    dialog.entry_table.selectRow(0)

    def change(form):
        assert form.units.text() == "1,25"
        form.units.setText("2,25")
        form._accept()
        return form.result()

    monkeypatch.setattr(m.BonusEntryForm, "exec", change)
    dialog.edit_selected()
    loaded(qtbot, dialog, lambda: dialog._entries[0].draft.units_minor == 225)
    assert len(session.bonuses.history(first.id)) == 2
    assert not dialog.edit.isEnabled()
    dialog.entry_table.selectRow(0)
    dialog.archive_selected()
    loaded(qtbot, dialog, lambda: dialog._entries[0].is_deleted)
    assert dialog.visible_entries == [] and dialog._balances[first.draft.program_id] == 0
    dialog.show_deleted.setChecked(True)
    dialog.entry_table.selectRow(0)
    dialog.archive_selected()
    loaded(qtbot, dialog, lambda: not dialog._entries[0].is_deleted)
    assert len(session.bonuses.history(first.id)) == 4
    assert (session.ledger.operations(), session.ledger.balances()) == money
    session.check_integrity()


def test_negative_adjustment_round_trips_and_invalid_date_keeps_form(context, qtbot):
    _, session, dialog = context
    program = session.bonuses.save_program("Программа")
    record = session.bonuses.save_entry(
        BonusDraft(program.id, date(2026, 10, 31), BonusKind.ADJUSTMENT, -125)
    )
    form = m.BonusEntryForm([program], [], dialog, current=record)
    qtbot.addWidget(form)
    assert form.units.text() == "−1,25"
    form.occurred.setText("31.02.2026")
    form._accept()
    assert form.result() != QDialog.DialogCode.Accepted and form.feedback.text()
    assert form.units.text() == "−1,25"
    form.occurred.setText("31.10.2026")
    form._accept()
    assert form.value == record.draft


def test_seed_batch_is_explicit_and_cannot_be_recorded_twice(context, qtbot):
    window, session, original = context
    program = session.bonuses.save_program("Программа")
    category = next(c for c in session.catalog.categories() if c.kind is CategoryKind.EXPENSE)
    seeds = (
        m.BonusSeed(125, "31.10.2026", "a" * 64, "Продукты", category.id),
        m.BonusSeed(275, "31.10.2026", "b" * 64, "Продукты", category.id),
    )
    dialog = m.BonusesDialog(window, session, seeds=seeds)
    qtbot.addWidget(dialog)
    loaded(qtbot, dialog, lambda: dialog._seed_ready)
    assert all(not w[0].isChecked() for w in dialog._seed_widgets)
    dialog.save_seeds()
    assert session.bonuses.entries() == [] and dialog.seed_feedback.text()
    dialog._select_all_seeds()
    dialog.seed_program.setCurrentIndex(dialog.seed_program.findData(program.id))
    dialog.save_seeds()
    loaded(qtbot, dialog, lambda: len(dialog._entries) == 2)
    assert session.bonuses.balances()[program.id] == 400
    assert all(not w[0].isEnabled() for w in dialog._seed_widgets)
    dialog._select_all_seeds()
    dialog.save_seeds()
    assert len(session.bonuses.entries()) == 2
    dialog.reject()
    assert not original._closing


def test_report_keeps_programs_separate_and_filters_clear_selection(context, qtbot):
    _, session, dialog = context
    one = session.bonuses.save_program("Первая")
    two = session.bonuses.save_program("Вторая")
    for p, units in ((one, 125), (two, 275)):
        session.bonuses.save_entry(BonusDraft(p.id, date.today(), BonusKind.ACCRUAL, units))
    dialog.reload()
    loaded(qtbot, dialog, lambda: len(dialog._programs) == 2)
    assert dialog.report_table.model().rowCount() == 2
    dialog.program_table.selectRow(0)
    assert dialog.edit.isEnabled()
    dialog.program_filter.setCurrentIndex(dialog.program_filter.findData(two.id))
    assert not dialog.edit.isEnabled() and len(dialog.visible_programs) == 1
    assert dialog.report_table.model().rowCount() == 1
    assert dialog.report_table.model().index(0, 2).data() == "2,75"


def test_worker_failure_preserves_form_and_retries_same_command(context, qtbot, monkeypatch):
    _, _, dialog = context
    save = dialog.service.save_program
    calls, forms = [], []

    def retry(value, **kwargs):
        calls.append(kwargs["command_id"])
        if len(calls) == 1:
            raise ValidationError("Повторите действие")
        return save(value, **kwargs)

    def name(form):
        forms.append(form)
        if len(forms) == 1:
            form.name.setText("Тестовая программа")
        else:
            assert form is forms[0]
            assert form.feedback.text() == "Повторите действие"
            assert form.name.text() == "Тестовая программа"
        form._accept()
        return form.result()

    monkeypatch.setattr(dialog.service, "save_program", retry)
    monkeypatch.setattr(m.BonusNameForm, "exec", name)
    dialog.create_program()
    loaded(qtbot, dialog, lambda: len(dialog._programs) == 1)
    assert len(forms) == 2 and calls[0] == calls[1]


def test_dialog_cannot_close_during_worker_and_late_result_never_changes_book(context, qtbot):
    window, _, dialog = context
    entered, finish = threading.Event(), threading.Event()
    done = []

    def action():
        entered.set()
        assert finish.wait(5)
        return 1

    dialog._job(action, done.append, "Проверка…")
    qtbot.waitUntil(entered.is_set)
    assert not dialog.close_button.isEnabled()
    dialog.reject()
    assert not dialog._closing
    window.session = None
    finish.set()
    qtbot.waitUntil(lambda: not window._busy)
    assert done == [] and dialog._closing


def test_old_book_does_not_offer_unavailable_bonus_journal(context, qtbot):
    window, session, _ = context
    before = session.bonuses
    session.bonuses = None
    window._update_report_action()
    assert not window.bonuses_action.isEnabled()
    session.bonuses = before
