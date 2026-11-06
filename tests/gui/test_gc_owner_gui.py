"""Циклические обёртки и состояние сборщика при работе фоновых задач."""

import gc
import threading
import weakref

from PySide6.QtCore import QCoreApplication, QObject, Qt

from personal_finance.presentation.gc_owner import GuiGarbageCollector, prepare_worker_gc


def test_cycles_created_before_worker_are_collected_on_gui(qtbot, qapp):
    main_thread = threading.get_ident()
    destroyed = []

    value = QObject()
    value.destroyed.connect(
        lambda: destroyed.append(threading.get_ident()), Qt.ConnectionType.DirectConnection
    )
    value.cycle = value
    ref = weakref.ref(value)
    del value
    prepare_worker_gc()
    assert ref() is None and destroyed == [main_thread]
    assert not gc.isenabled()


def test_timer_collects_cycles_while_worker_allocates_without_collecting_gui_objects(qtbot, qapp):
    collector = GuiGarbageCollector(qapp)
    collector.start()
    collector.timer.setInterval(10)
    destroyed = []
    main_thread = threading.get_ident()

    value = QObject()
    value.destroyed.connect(
        lambda: destroyed.append(threading.get_ident()), Qt.ConnectionType.DirectConnection
    )
    value.cycle = value
    ref = weakref.ref(value)
    del value
    finished = threading.Event()

    def allocate():
        for _ in range(3000):
            cycle = []
            cycle.append(cycle)
        finished.set()

    worker = threading.Thread(target=allocate)
    try:
        worker.start()
        qtbot.waitUntil(lambda: finished.is_set() and ref() is None, timeout=5000)
        assert destroyed == [main_thread]
    finally:
        worker.join(timeout=5)
        collector.stop()
        collector.deleteLater()


def test_stop_restores_preexisting_automatic_gc_state(qapp):
    original = gc.isenabled()
    for enabled in (False, True):
        gc.enable() if enabled else gc.disable()
        collector = GuiGarbageCollector(qapp)
        try:
            collector.start()
            collector.start()
            assert collector.active and not gc.isenabled()
            collector.stop()
            collector.stop()
            assert gc.isenabled() == enabled
        finally:
            collector.stop()
            collector.deleteLater()
    gc.enable() if original else gc.disable()


def test_worker_cannot_install_or_collect_gui_gc(qtbot, qapp):
    errors = []
    collector = GuiGarbageCollector(qapp)
    collector.start()

    def wrong_thread():
        for action in (prepare_worker_gc, collector.collect):
            try:
                action()
            except RuntimeError as error:
                errors.append(str(error))

    worker = threading.Thread(target=wrong_thread)
    try:
        worker.start()
        worker.join(timeout=5)
        assert len(errors) == 2
    finally:
        collector.stop()
        collector.deleteLater()
    assert QCoreApplication.instance() is qapp


def test_collection_callback_cannot_reenter_gc(qapp):
    collector = GuiGarbageCollector(qapp)
    collector.start()
    starts = []

    def observe(phase, info):
        if phase == "start":
            starts.append(phase)
            collector.collect()

    try:
        gc.callbacks.append(observe)
        collector.collect()
        assert starts == ["start"]
    finally:
        gc.callbacks.remove(observe)
        collector.stop()
        collector.deleteLater()
