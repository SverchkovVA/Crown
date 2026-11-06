"""Постоянно видимые действия формы, сохраняющие нативные роли кнопок Qt."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)


class ActionFooter(QFrame):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("inlineFooter")
        self._adapting = False

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._adapting or not self.isVisible():
            return
        self._adapting = True
        try:
            for box in self.findChildren(QDialogButtonBox):
                buttons = [b for b in box.buttons() if not b.isHidden()]
                width = (
                    sum(b.sizeHint().width() for b in buttons) + max(0, len(buttons) - 1) * 8 + 16
                )
                orientation = (
                    Qt.Orientation.Vertical if box.width() < width else Qt.Orientation.Horizontal
                )
                if box.orientation() != orientation:
                    box.setOrientation(orientation)
        finally:
            self._adapting = False


def _style_buttons(box: QDialogButtonBox) -> None:
    for button in box.buttons():
        if button.text().replace("&", "").strip() == "Закрыть":
            button.hide()
    for standard, icon in (
        (QDialogButtonBox.StandardButton.Close, QStyle.StandardPixmap.SP_DialogCloseButton),
        (QDialogButtonBox.StandardButton.Cancel, QStyle.StandardPixmap.SP_DialogCancelButton),
        (QDialogButtonBox.StandardButton.Save, QStyle.StandardPixmap.SP_DialogSaveButton),
        (QDialogButtonBox.StandardButton.Ok, QStyle.StandardPixmap.SP_DialogApplyButton),
    ):
        button = box.button(standard)
        if button is not None:
            button.setIcon(button.style().standardIcon(icon))
    if not any(b.objectName() == "primary" for b in box.buttons()):
        candidates = [
            b
            for b in box.buttons()
            if box.buttonRole(b)
            in (
                QDialogButtonBox.ButtonRole.AcceptRole,
                QDialogButtonBox.ButtonRole.YesRole,
            )
        ]
        if candidates:
            candidates[0].setObjectName("primary")


def prepare_inline_layout(dialog: QDialog) -> None:
    if dialog.property("inlineLayoutPrepared"):
        return
    for button in dialog.findChildren(QWidget):
        if button.property("inlineCloseAction"):
            button.hide()
    layout = dialog.layout()
    if not isinstance(layout, QVBoxLayout):
        return
    # Действия остаются детьми dialog: существующие обработчики и findChild работают.
    items = []
    stretches: dict[int, int] = {}
    while layout.count():
        stretch = layout.stretch(0)
        item = layout.takeAt(0)
        if item is not None:
            items.append(item)
            stretches[id(item)] = stretch
    actions = [
        item
        for item in items
        if isinstance((widget := item.widget()), QDialogButtonBox)
        or (widget is not None and widget.property("stickyActions"))
    ]
    if not actions:
        for item in items:
            layout.addItem(item)
        return
    content = QWidget(dialog)
    content.setObjectName("inlineBody")
    body = QVBoxLayout(content)
    body.setContentsMargins(0, 0, 0, 0)
    sticky = []
    for item in items:
        widget = item.widget()
        if item in actions or (
            widget is not None
            and (
                widget.property("stickyNotice")
                or (dialog.property("warningsAtBottom") and isinstance(widget, QLabel))
            )
        ):
            sticky.append(item)
        elif widget is not None:
            body.addWidget(widget, stretches[id(item)])
        elif (child_layout := item.layout()) is not None:
            body.addLayout(child_layout, stretches[id(item)])
        else:
            body.addItem(item)
    scroll = QScrollArea(dialog)
    scroll.setObjectName("inlineContent")
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setMinimumSize(0, 0)
    scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    scroll.setWidget(content)
    footer = ActionFooter(dialog)
    bottom = QVBoxLayout(footer)
    bottom.setContentsMargins(0, 10, 0, 0)
    for item in sticky:
        widget = item.widget()
        if widget is not None:
            if isinstance(widget, QDialogButtonBox):
                _style_buttons(widget)
            bottom.addWidget(widget)
    if dialog.property("ownsContentScrolling"):
        # Таблица и поля уже имеют собственную прокрутку. Внешний QScrollArea
        # повторно вычисляет heightForWidth растянутого splitter и создаёт
        # пустую вертикальную область даже при нулевом числе строк.
        scroll.takeWidget()
        scroll.deleteLater()
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        layout.addWidget(content, 1)
    else:
        layout.addWidget(scroll, 1)
    layout.addWidget(footer)
    dialog.setProperty("inlineLayoutPrepared", True)
