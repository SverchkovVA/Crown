"""Выбор тегов без изменения назначения или денежных значений."""

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.organization import Tag


class CheckList(QListWidget):
    def __init__(self, choices: list[tuple[str, str]], selected: tuple[str, ...] = ()) -> None:
        super().__init__()
        for identifier, title in choices:
            item = QListWidgetItem(title, self)
            item.setData(Qt.ItemDataRole.UserRole, identifier)
            item.setToolTip("<p>" + escape(title) + "</p>")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if identifier in selected else Qt.CheckState.Unchecked
            )

    def values(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                self.item(index).data(Qt.ItemDataRole.UserRole)
                for index in range(self.count())
                if self.item(index).checkState() == Qt.CheckState.Checked
            )
        )


class TagDialog(QDialog):
    def __init__(
        self, tags: list[Tag], selected: tuple[str, ...], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Теги операции")
        self.resize(440, 450)
        layout = QVBoxLayout(self)
        info = QLabel(
            "Теги помогают искать операции и не меняют распределение по категориям. "
            "Архивный тег можно сохранить или убрать; новые теги создаются в «Теги и фильтры»."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        self.choices = CheckList(
            [
                (tag.id, tag.name + (" (архив)" if tag.is_deleted else ""))
                for tag in tags
                if not tag.is_deleted or tag.id in selected
            ],
            selected,
        )
        layout.addWidget(self.choices, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Применить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class TagSelector(QWidget):
    def __init__(self, tags: list[Tag], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tags = tags
        self._selected: tuple[str, ...] = ()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.choose = QPushButton("Выбрать теги…")
        self.summary = QLabel("Без тегов")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.choose)
        layout.addWidget(self.summary, 1)
        self.choose.clicked.connect(self._choose)

    def values(self) -> tuple[str, ...]:
        return self._selected

    def set_values(self, selected: tuple[str, ...]) -> None:
        self._selected = tuple(sorted(selected))
        names = [
            tag.name + (" (архив)" if tag.is_deleted else "")
            for tag in self._tags
            if tag.id in selected
        ]
        self.summary.setText(
            ("; ".join(names) or "Без тегов") if len(names) <= 3 else f"Выбрано тегов: {len(names)}"
        )
        self.summary.setToolTip("<p>" + escape("; ".join(names)) + "</p>")

    def _choose(self) -> None:
        dialog = TagDialog(self._tags, self._selected, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_values(dialog.choices.values())
