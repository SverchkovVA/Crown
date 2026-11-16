from contextlib import closing
from threading import Event

from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from personal_finance.application.vault import open_vault
from personal_finance.domain.models import AccountKind
from personal_finance.presentation.main_window import MainWindow
from tests.conftest import PASSWORD


def _window(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "idle.ini"), QSettings.Format.IniFormat)
    window = MainWindow(idle_lock_settings=settings)
    qtbot.addWidget(window)
    window.show()
    return window


def _key_event():
    return QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)


def test_idle_lock_is_opt_in_and_unlocks_after_the_timer(session, qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    root = session.root
    window.activate_session(session)
    timer = window._idle_lock_timer
    assert not timer.isActive()
    try:
        window.idle_lock_combo.setCurrentIndex(window.idle_lock_combo.findData(5))
        assert timer.isActive() and timer.interval() == 300_000
        other = QWidget()
        qtbot.addWidget(other)
        timer.start(4000)
        QApplication.sendEvent(other, _key_event())
        assert timer.interval() == 4000
        QApplication.sendEvent(window, _key_event())
        assert timer.interval() == 300_000

        timer.start(25)
        qtbot.waitUntil(lambda: window.session is None, timeout=3000)
        assert window.root_stack.currentIndex() == 0 and not timer.isActive()
        assert "бездействия" in window._welcome_forms.page_notice.text()
        with closing(open_vault(root, PASSWORD)) as reopened:
            reopened.check_integrity()

        another = _window(qtbot, tmp_path)
        assert another.idle_lock_combo.currentData() == 5
        assert not another._idle_lock_timer.isActive()
    finally:
        window.lock_book()


def test_idle_lock_waits_for_the_active_write(session, qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    root = session.root
    window.activate_session(session)
    window.idle_lock_combo.setCurrentIndex(window.idle_lock_combo.findData(5))
    writing = Event()
    resume = Event()

    def write():
        writing.set()
        if not resume.wait(10):
            raise RuntimeError("Не получено завершение учебной паузы.")
        return session.catalog.add_account("Учебный счёт после таймера", AccountKind.CASH)

    try:
        window._job(write, lambda _: None, "Учебная запись")
        qtbot.waitUntil(writing.is_set, timeout=5000)
        window._lock_when_idle()
        assert window.session is session and window._busy
        assert window._idle_lock_timer.interval() == 250
        resume.set()
        qtbot.waitUntil(lambda: window.session is None, timeout=10000)
        with closing(open_vault(root, PASSWORD)) as reopened:
            assert reopened.catalog.accounts()[0].name == "Учебный счёт после таймера"
    finally:
        resume.set()
        qtbot.waitUntil(lambda: not window._busy, timeout=10000)
        window.lock_book()


def test_idle_lock_starts_in_workspace_and_waits_for_modal_input(session, qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    timer = window._idle_lock_timer
    window.idle_lock_combo.setCurrentIndex(window.idle_lock_combo.findData(5))
    window.activate_session(session, show_workspace=False)
    assert not timer.isActive()
    try:
        window.root_stack.setCurrentIndex(1)
        assert timer.isActive()
        dialog = QDialog(window)
        dialog.setModal(True)
        dialog.show()
        qtbot.waitUntil(lambda: QApplication.activeModalWidget() is dialog, timeout=3000)
        try:
            window._lock_when_idle()
            assert window.session is session and timer.interval() == 250
        finally:
            dialog.reject()
        window.idle_lock_combo.setCurrentIndex(0)
        assert not timer.isActive()
        window._lock_when_idle()
        assert window.session is session
    finally:
        window.lock_book()
