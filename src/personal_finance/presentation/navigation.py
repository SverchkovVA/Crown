"""История экранов: возврат сохраняет открытую форму и не выполняет её действие."""

from collections.abc import Callable
from dataclasses import dataclass
from weakref import ReferenceType, ref

from PySide6.QtWidgets import QScrollArea
from shiboken6 import isValid


@dataclass(frozen=True)
class Location:
    section: int
    view: ReferenceType[QScrollArea] | None = None

    @classmethod
    def at(cls, section: int, view: QScrollArea | None) -> "Location":
        return cls(section, None if view is None else ref(view))


class NavigationHistory:
    def __init__(
        self,
        valid: Callable[[Location], bool],
        activate: Callable[[Location], None],
        changed: Callable[[], None],
    ) -> None:
        self.entries: list[Location] = []
        self.index = -1
        self.moving = False
        self._valid, self._activate, self._changed = valid, activate, changed

    def record(self, location: Location) -> None:
        if self.moving:
            return
        if self.index >= 0 and self.entries[self.index] == location:
            self._changed()
            return
        # Закрытие дочерней формы возвращает к уже открытому родителю.
        previous = next((i for i in range(self.index, -1, -1) if self.entries[i] == location), None)
        if (
            previous is not None
            and self.index >= 0
            and self.entries[self.index].section == location.section
        ):
            self.index = previous
        else:
            self.entries = self.entries[: self.index + 1]
            self.entries.append(location)
            self.index = len(self.entries) - 1
        self._changed()

    def target(self, direction: int) -> int | None:
        cursor = self.index + direction
        while 0 <= cursor < len(self.entries):
            if self._valid(self.entries[cursor]):
                return cursor
            cursor += direction
        return None

    def move(self, direction: int) -> None:
        if direction not in (-1, 1):
            return
        target = self.target(direction)
        if target is None:
            return
        self.index = target
        self.moving = True
        try:
            self._activate(self.entries[target])
        finally:
            self.moving = False
            self._changed()

    def reset(self) -> None:
        self.entries.clear()
        self.index = -1
        self._changed()


def living_view(location: Location) -> QScrollArea | None:
    view = None if location.view is None else location.view()
    return view if view is not None and isValid(view) else None
