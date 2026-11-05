"""Формы внутри страницы с возвратом к предыдущему шагу."""

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from personal_finance.presentation.form_layout import prepare_inline_layout


class FormHost(QScrollArea):
    """Размер формы равен viewport; прокручивается только её содержимое."""

    def setWidget(self, widget: QWidget) -> None:
        super().setWidget(widget)
        self.fit_widget()

    def fit_widget(self) -> None:
        widget = self.widget()
        if widget is not None:
            widget.setGeometry(self.viewport().rect())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.fit_widget()


class InlineForms(QWidget):
    locationChanged = Signal(object)

    def __init__(self, page: QWidget, parent: QWidget) -> None:
        super().__init__(parent)
        self.page = page
        self.clearing = False
        self.navigator: Callable[[int], None] | None = None
        self._position = -1
        self._completions: dict[QScrollArea, Callable[[int], None]] = {}
        self.page_notice: QLabel | None = None
        self.header = QWidget(parent)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 8)
        self.back = QToolButton()
        self.forward = QToolButton()
        for widget, title, icon, delta, shortcut in (
            (self.back, "Назад", QStyle.StandardPixmap.SP_ArrowBack, -1, "Alt+←"),
            (self.forward, "Вперёд", QStyle.StandardPixmap.SP_ArrowForward, 1, "Alt+→"),
        ):
            widget.setProperty("navigationDirection", "back" if delta == -1 else "forward")
            widget.setText(title)
            widget.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            widget.setIcon(widget.style().standardIcon(icon))
            widget.setMinimumHeight(36)
            widget.setMinimumWidth(widget.fontMetrics().horizontalAdvance(title) + 42)
            widget.setAccessibleName(title)
            widget.setToolTip(title + " (" + shortcut + ")")
            widget.clicked.connect(lambda checked=False, d=delta: self._navigate(d))
            widget.setEnabled(False)
            header_layout.addWidget(widget)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.header.setMinimumHeight(40)
        self.heading = QLabel()
        self.heading.setWordWrap(True)
        self.heading.setObjectName("heading")
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.hide()
        self.stack = QStackedWidget()
        layout = QVBoxLayout(self)
        header_layout.addWidget(self.heading, 1)
        self.header.hide()
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.notice)
        self.hide()

    def message(self, text: str) -> None:
        target = self.notice if self._position >= 0 else self.page_notice
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
        if self.clearing:
            dialog.deleteLater()
            return
        self._discard_forward()
        self.message("")
        trigger = QApplication.focusWidget()
        owner = dialog.parentWidget()
        scroll = FormHost()
        scroll.setWidgetResizable(False)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        prepare_inline_layout(dialog)
        dialog.setMinimumSize(0, 0)
        dialog.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        root_layout = dialog.layout()
        if root_layout is not None:
            root_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        dialog.setParent(scroll, Qt.WindowType.Widget)
        scroll.setWidget(dialog)
        self.stack.addWidget(scroll)
        self.stack.setCurrentWidget(scroll)
        self._position = self.stack.indexOf(scroll)
        self._update_page()

        def complete(result: int) -> None:
            if self.stack.indexOf(scroll) < 0:
                return
            self._position = self.stack.indexOf(scroll)
            self._discard_forward()
            dialog.finished.disconnect(complete)
            self._completions.pop(scroll, None)
            if not dispose:
                scroll.takeWidget()
                dialog.setParent(owner, Qt.WindowType.Widget)
            self.stack.removeWidget(scroll)
            scroll.deleteLater()
            self._position = self.stack.count() - 1
            self.stack.setCurrentIndex(self._position)
            self._update_page()
            if (
                not self.clearing
                and trigger is not None
                and isValid(trigger)
                and trigger.isVisible()
                and trigger.isEnabled()
            ):
                trigger.setFocus(Qt.FocusReason.OtherFocusReason)
            finished(result)

        self._completions[scroll] = complete
        dialog.finished.connect(complete)
        dialog.show()
        scroll.fit_widget()
        if dialog.isVisible():
            _focus_form(dialog)

    def _update_page(self) -> None:
        scroll = self.stack.currentWidget() if self._position >= 0 else None
        dialog = scroll.widget() if isinstance(scroll, QScrollArea) else None
        self.message("")
        self.heading.setText(dialog.windowTitle() if dialog is not None else "")
        self.setVisible(dialog is not None)
        self.page.setVisible(dialog is None)
        self.header.setVisible(dialog is not None or self.stack.count() > 0)
        self.back.setEnabled(self._position >= 0)
        self.forward.setEnabled(self._position + 1 < self.stack.count())
        self.locationChanged.emit(scroll)

    def _navigate(self, direction: int) -> None:
        if self.navigator is not None:
            self.navigator(direction)
        elif -1 <= self._position + direction < self.stack.count():
            self.show_location(
                self.stack.widget(self._position + direction)
                if self._position + direction >= 0
                else None
            )

    def show_location(self, scroll: QWidget | None) -> None:
        index = -1 if scroll is None else self.stack.indexOf(scroll)
        if scroll is not None and index < 0:
            return
        self._position = index
        if index >= 0:
            self.stack.setCurrentIndex(index)
        self._update_page()
        if isinstance(scroll, QScrollArea) and isinstance(dialog := scroll.widget(), QDialog):
            _focus_form(dialog)

    def _discard_forward(self) -> None:
        for index in range(self.stack.count() - 1, self._position, -1):
            scroll = self.stack.widget(index)
            if not isinstance(scroll, QScrollArea):
                continue
            dialog = scroll.widget()
            complete = self._completions.pop(scroll, None)
            if isinstance(dialog, QDialog) and complete is not None:
                dialog.finished.disconnect(complete)
            self.stack.removeWidget(scroll)
            scroll.deleteLater()

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
            self._position = -1
            self._update_page()
        finally:
            self.clearing = False


def _focus_form(dialog: QDialog) -> None:
    focused = QApplication.focusWidget()
    if focused is not None and (focused is dialog or dialog.isAncestorOf(focused)):
        return
    target = dialog.nextInFocusChain()
    while target is not None and dialog.isAncestorOf(target):
        if (
            target.isVisible()
            and target.isEnabled()
            and target.focusPolicy() & Qt.FocusPolicy.TabFocus
        ):
            target.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        target = target.nextInFocusChain()
    dialog.setFocus(Qt.FocusReason.OtherFocusReason)


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
