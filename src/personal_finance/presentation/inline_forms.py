"""Формы внутри страницы с возвратом к предыдущему шагу."""

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
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
        self.clearing = False
        self.page_notice: QLabel | None = None
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
        target = self.notice if self.stack.count() else self.page_notice
        if target is not None:
            target.setText(text)
            target.setVisible(bool(text))

    def open_form(
        self,
        dialog: QDialog,
        finished: Callable[[int], None],
        *,
        dispose: bool = True,
    ) -> None:
        self.message("")
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
        self.clearing = True
        try:
            while isinstance(scroll := self.stack.currentWidget(), QScrollArea):
                dialog = scroll.widget()
                if isinstance(dialog, QDialog):
                    dialog.reject()
                if self.stack.indexOf(scroll) >= 0:
                    self.stack.removeWidget(scroll)
                    scroll.deleteLater()
            self._update_page()
        finally:
            self.clearing = False


def present_form(
    dialog: QDialog,
    panel: InlineForms | None,
    finished: Callable[[int], None] | None = None,
    *,
    dispose: bool = True,
) -> None:
    panel = panel or containing_panel(dialog)
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


def containing_panel(widget: QWidget) -> InlineForms | None:
    owner = widget.parentWidget()
    while owner is not None:
        if isinstance(owner, InlineForms):
            return owner
        owner = owner.parentWidget()
    return None


def form_warning(dialog: QDialog, title: str, message: str) -> None:
    panel = containing_panel(dialog)
    if panel is not None:
        panel.message(message)
    else:
        QMessageBox.warning(dialog, title, message)


def confirm_form(
    panel: InlineForms,
    parent: QWidget,
    title: str,
    question: str,
    action_label: str,
    accepted: Callable[[], None],
) -> None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    layout = QVBoxLayout(dialog)
    text = QLabel(question)
    text.setTextFormat(Qt.TextFormat.PlainText)
    text.setWordWrap(True)
    layout.addWidget(text)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
    ok.setText(action_label)
    ok.setAutoDefault(False)
    cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
    cancel.setText("Отмена")
    cancel.setDefault(True)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    def completed(result: int) -> None:
        if result == QDialog.DialogCode.Accepted:
            accepted()

    present_form(dialog, panel, completed)
