"""Формы внутри страницы с возвратом к предыдущему шагу."""

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QMessageBox,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class InlineForms(QWidget):
    def __init__(self, page: QWidget, parent: QWidget) -> None:
        super().__init__(parent)
        self.page = page
        self.heading = QLabel()
        self.heading.setObjectName("heading")
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.hide()
        self.stack = QStackedWidget()
        layout = QVBoxLayout(self)
        layout.addWidget(self.heading)
        layout.addWidget(self.notice)
        layout.addWidget(self.stack, 1)
        self.hide()

    def message(self, text: str) -> None:
        self.notice.setText(text)
        self.notice.setVisible(bool(text))

    def open_form(
        self,
        dialog: QDialog,
        finished: Callable[[int], None],
        *,
        dispose: bool = True,
    ) -> None:
        owner = dialog.parentWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        dialog.setMinimumSize(0, 0)
        dialog.setParent(scroll, Qt.WindowType.Widget)
        scroll.setWidget(dialog)
        self.stack.addWidget(scroll)
        self.stack.setCurrentWidget(scroll)
        self._update_page()

        def complete(result: int) -> None:
            if self.stack.indexOf(scroll) < 0:
                return
            dialog.finished.disconnect(complete)
            if not dispose:
                scroll.takeWidget()
                dialog.setParent(owner, Qt.WindowType.Widget)
            self.stack.removeWidget(scroll)
            scroll.deleteLater()
            self.stack.setCurrentIndex(self.stack.count() - 1)
            self._update_page()
            finished(result)

        dialog.finished.connect(complete)
        dialog.show()

    def _update_page(self) -> None:
        scroll = self.stack.currentWidget()
        dialog = scroll.widget() if isinstance(scroll, QScrollArea) else None
        self.message("")
        self.heading.setText(dialog.windowTitle() if dialog is not None else "")
        self.setVisible(dialog is not None)
        self.page.setVisible(dialog is None)

    def clear(self) -> None:
        while (scroll := self.stack.currentWidget()) is not None:
            self.stack.removeWidget(scroll)
            scroll.deleteLater()
        self._update_page()


def present_form(
    dialog: QDialog,
    panel: InlineForms | None,
    finished: Callable[[int], None] | None = None,
    *,
    dispose: bool = True,
) -> None:
    if panel is not None:
        panel.open_form(dialog, finished or (lambda _: None), dispose=dispose)
        return
    try:
        result = dialog.exec()
        if finished is not None:
            finished(result)
    finally:
        if dispose:
            dialog.deleteLater()


def form_warning(dialog: QDialog, title: str, message: str) -> None:
    owner = dialog.parentWidget()
    while owner is not None:
        if isinstance(owner, InlineForms):
            owner.message(message)
            return
        owner = owner.parentWidget()
    QMessageBox.warning(dialog, title, message)
