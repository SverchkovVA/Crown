"""Плотная OCR-таблица: управляемая ширина и колесо без случайной смены полей."""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QResizeEvent, QShowEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QHeaderView, QTableWidget, QWidget


class OcrReviewTable(QTableWidget):
    def __init__(self, rows: int, columns: int) -> None:
        super().__init__(rows, columns)
        self._widths: tuple[int, ...] = ()
        self._fitting = False
        self.setWordWrap(False)
        self.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setStretchLastSection(False)
        self.horizontalHeader().sectionResized.connect(self._column_resized)
        self.setToolTip(
            "Колесо над таблицей — влево/вправо. Shift + колесо — вверх/вниз. "
            "Ширину столбца можно изменить за границу заголовка."
        )
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        app.installEventFilter(self)

    def configure_columns(self, widths: tuple[int, ...]) -> None:
        if len(widths) != self.columnCount():
            raise ValueError("Число размеров не совпадает с числом столбцов")
        self._widths = widths
        self._fit_columns()

    def _column_resized(self, column: int, old: int, width: int) -> None:
        if self._widths and not self._fitting:
            values = list(self._widths)
            values[column] = max(48, width)
            self._widths = tuple(values)

    def _fit_columns(self) -> None:
        if not self._widths or self._fitting:
            return
        visible = [i for i in range(self.columnCount()) if not self.isColumnHidden(i)]
        extra = max(0, self.viewport().width() - sum(self._widths[i] for i in visible))
        # Свободное место распределяется; один последний столбец не раздувается.
        share, rest = divmod(extra, max(1, len(visible)))
        self._fitting = True
        try:
            for position, column in enumerate(visible):
                self.setColumnWidth(column, self._widths[column] + share + (position < rest))
        finally:
            self._fitting = False

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        widgets = [
            self.cellWidget(row, column)
            for row in range(self.rowCount())
            for column in range(self.columnCount())
        ]
        height = 0
        for widget in widgets:
            if widget is not None:
                widget.ensurePolished()
                height = max(height, widget.sizeHint().height(), widget.minimumHeight())
        if height:
            self.verticalHeader().setMinimumSectionSize(height + 2)
            self.verticalHeader().setDefaultSectionSize(height + 2)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit_columns()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            isinstance(event, QWheelEvent)
            and isinstance(watched, QWidget)
            and (watched is self or self.isAncestorOf(watched))
            and QApplication.activePopupWidget() is None
        ):
            vertical = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            bar = self.verticalScrollBar() if vertical else self.horizontalScrollBar()
            pixels = event.pixelDelta()
            angles = event.angleDelta()
            delta = pixels.y() if vertical else (pixels.x() or pixels.y())
            if delta:
                bar.setValue(bar.value() - delta)
            else:
                angle = angles.y() if vertical else (angles.x() or angles.y())
                bar.setValue(bar.value() - angle * max(1, bar.singleStep()) * 3 // 120)
            event.accept()
            return True
        return super().eventFilter(watched, event)
