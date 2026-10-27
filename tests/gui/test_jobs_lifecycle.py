"""Результаты и освобождение фоновых задач принадлежат GUI-потоку."""

import gc
from threading import Event

from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import QMessageBox

from personal_finance.domain.errors import ValidationError
from personal_finance.presentation.main_window import MainWindow


def test_callbacks_and_signal_cleanup_stay_in_gui_even_with_worker_gc(qtbot, qapp):
    window = MainWindow()
    qtbot.addWidget(window)
    worker_threads = []
    callback_threads = []
    callback_cleanup = []
    signal_cleanup = []

    class Completion:
        def __call__(self, result):
            callback_threads.append(QThread.currentThread() == qapp.thread())
            assert result == 42

        def __del__(self):
            callback_cleanup.append(QThread.currentThread() == qapp.thread())

    def action():
        worker_threads.append(QThread.currentThread() != qapp.thread())
        gc.collect()
        return 42

    for _ in range(4):
        window._job(action, Completion(), "Проверка владения задачей")
        signals = next(iter(window._jobs)).signals
        signals.destroyed.connect(
            lambda: signal_cleanup.append(QThread.currentThread() == qapp.thread()),
            Qt.ConnectionType.DirectConnection,
        )
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        assert not window._jobs and not window._job_callbacks
        qtbot.waitUntil(lambda: len(signal_cleanup) == len(worker_threads), timeout=15000)
    assert worker_threads == [True] * 4
    assert callback_threads == [True] * 4
    assert callback_cleanup == [True] * 4
    assert signal_cleanup == [True] * 4


def test_failed_worker_reports_on_gui_and_releases_completion(qtbot, qapp, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    errors = []
    callbacks = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args: errors.append((QThread.currentThread() == qapp.thread(), args[-1])),
    )

    def fail():
        gc.collect()
        raise ValidationError("Проверочный отказ")

    window._job(fail, lambda result: callbacks.append(result), "Проверка отказа")
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert errors == [(True, "Проверочный отказ")]
    assert callbacks == []
    assert not window._jobs and not window._job_callbacks


def test_close_waits_for_worker_before_releasing_gui_owner(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    started = Event()
    release = Event()
    results = []

    def action():
        started.set()
        if not release.wait(timeout=10):
            raise RuntimeError("Тест не разрешил завершение задачи")
        return 42

    window._job(action, results.append, "Проверка закрытия")
    try:
        qtbot.waitUntil(started.is_set, timeout=5000)
        assert window.close() is False
        assert window.isVisible()
        assert "Дождитесь" in window.statusBar().currentMessage()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert results == [42]
    assert window.close() is True
