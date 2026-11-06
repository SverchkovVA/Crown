"""Циклический Python GC освобождает обёртки Qt только в потоке приложения."""

import gc

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Slot
from shiboken6 import isValid


class GuiGarbageCollector(QObject):
    def __init__(self, application: QCoreApplication) -> None:
        super().__init__(application)
        self.active = False
        self._restore_automatic = False
        self._collecting = False
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.collect)
        application.aboutToQuit.connect(self.stop)

    def start(self) -> None:
        if self.active:
            return
        self._restore_automatic = gc.isenabled()
        gc.disable()
        self.active = True
        self.timer.start()

    @Slot()
    def collect(self) -> None:
        if not self.active:
            return
        if not QThread.isMainThread():
            raise RuntimeError("Сборка циклических объектов Qt допустима только в GUI-потоке.")
        if self._collecting:
            return
        self._collecting = True
        try:
            gc.collect()
        finally:
            self._collecting = False

    @Slot()
    def stop(self) -> None:
        if not self.active:
            return
        self.timer.stop()
        self.active = False
        if self._restore_automatic:
            gc.enable()


_collector: GuiGarbageCollector | None = None


def prepare_worker_gc() -> None:
    """Вызывается в GUI перед запуском задачи; счётчики ссылок Python не меняются."""
    global _collector
    application = QCoreApplication.instance()
    if application is None or not QThread.isMainThread():
        raise RuntimeError("Фоновая задача должна создаваться в потоке приложения Qt.")
    if _collector is None or not isValid(_collector):
        _collector = GuiGarbageCollector(application)
    _collector.start()
    # Убираем прежние недостижимые обёртки до передачи управления рабочему потоку.
    # Периодическая сборка остаётся в GUI, в том числе во время долгой задачи.
    _collector.collect()
