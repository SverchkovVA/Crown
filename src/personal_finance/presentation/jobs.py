"""Длительные локальные действия вне потока интерфейса."""

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from personal_finance.domain.errors import FinanceError


class JobSignals(QObject):
    success = Signal(object)
    failure = Signal(str)
    finished = Signal()


class Job(QRunnable):
    def __init__(self, action: Callable[[], object]) -> None:
        super().__init__()
        self.action = action
        self.signals = JobSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.success.emit(self.action())
        except FinanceError as exc:
            self.signals.failure.emit(str(exc))
        except OSError:
            self.signals.failure.emit(
                "Не удалось прочитать или записать файл. Проверьте доступ и свободное место."
            )
        except Exception:
            # SQL, пользовательские значения и ключи не попадают в сообщения и журналы.
            self.signals.failure.emit(
                "Не удалось завершить действие. Исходная книга сохранена. "
                "Не используйте эту сборку для реальных данных."
            )
        finally:
            self.action = lambda: None
            self.signals.finished.emit()
