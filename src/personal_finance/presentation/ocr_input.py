"""Явный выбор локального снимка в журнале операций."""

from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import require_dimensions


def image_paths(data: QMimeData) -> list[Path]:
    return [
        Path(url.toLocalFile())
        for url in data.urls()
        if url.isLocalFile()
        and not url.host()
        and Path(url.toLocalFile()).suffix.casefold() in {".png", ".jpg", ".jpeg"}
    ]


class OcrInputPanel(QFrame):
    submitted = Signal(object, bool)

    def __init__(self, supports_history: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ocrInput")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAcceptDrops(True)
        self._source: QImage | None = None
        self.mode = QComboBox()
        self.mode.setAccessibleName("Вид изображения")
        self.mode.addItem("Карточка операции", False)
        if supports_history:
            self.mode.addItem("История операций", True)
        self.files = QComboBox()
        self.files.setAccessibleName("Снимок для распознавания")
        self.files.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.files.setMinimumContentsLength(16)
        self.files.setPlaceholderText("Снимок не выбран")
        self.notice = QLabel("Изображение ещё не выбрано.")
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        self.recognize = QPushButton("Распознать")
        self.recognize.setObjectName("primary")
        close = QPushButton("Свернуть")
        self.recognize.clicked.connect(self.submit)
        close.clicked.connect(self.hide)
        self.files.currentIndexChanged.connect(self._update)
        layout = QVBoxLayout(self)
        self.drop_zone = ImageDropZone(self)
        layout.addWidget(self.drop_zone)
        row = QHBoxLayout()
        row.addWidget(self.mode)
        row.addWidget(self.files, 1)
        row.addWidget(self.recognize)
        layout.addLayout(row)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(close)
        layout.addLayout(actions)
        layout.addWidget(self.notice)
        self._update()

    def _update(self) -> None:
        self.recognize.setEnabled(self.files.currentIndex() >= 0)

    def reset(self) -> None:
        self._source = None
        self.files.clear()
        self.notice.setText("Изображение ещё не выбрано.")
        self.hide()

    def choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Выберите изображения для OCR", "", "Изображения (*.png *.jpg *.jpeg)"
        )
        if paths:
            self.set_files([Path(path) for path in paths])

    def set_files(self, paths: list[Path]) -> None:
        self._source = None
        self.files.clear()
        for path in dict.fromkeys(paths):
            self.files.addItem(path.name, path)
        self.files.setCurrentIndex(0)
        self.notice.setText(
            f"Снимков в списке: {self.files.count()}. Выберите снимок для проверки."
        )

    def paste(self) -> None:
        clipboard = QApplication.clipboard()
        data = clipboard.mimeData()
        if data is not None and (paths := image_paths(data)):
            self.set_files(paths)
            return
        image = clipboard.image() if data is not None and data.hasImage() else QImage()
        if image.isNull():
            self.notice.setText("В буфере нет изображения или локальных файлов PNG/JPEG.")
            return
        try:
            require_dimensions(image.width(), image.height())
        except ValidationError as error:
            self.notice.setText(str(error))
            return
        self._source = image
        self.files.clear()
        self.files.addItem("Изображение из буфера")
        self.files.setCurrentIndex(0)
        self.notice.setText("Снимок из буфера готов к распознаванию.")

    def submit(self) -> None:
        source = self._source if self._source is not None else self.files.currentData()
        if isinstance(source, (Path, QImage)):
            self.submitted.emit(source, bool(self.mode.currentData()))

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if image_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if paths := image_paths(event.mimeData()):
            self.set_files(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class ImageDropZone(QFrame):
    def __init__(self, panel: "OcrInputPanel") -> None:
        super().__init__(panel)
        self.panel = panel
        self.setObjectName("ocrDropZone")
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Область загрузки: перетащите изображение сюда")
        self.setMinimumHeight(120)
        layout = QVBoxLayout(self)
        self.title = QLabel("Перетащите изображение сюда")
        self.title.setObjectName("dropTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.title)
        help_text = QLabel("PNG или JPEG из Проводника · можно выбрать несколько файлов")
        help_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        help_text.setWordWrap(True)
        help_text.setObjectName("muted")
        layout.addWidget(help_text)
        row = QHBoxLayout()
        row.addStretch()
        choose = QPushButton("Выбрать файлы…")
        choose.setIcon(choose.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        choose.clicked.connect(panel.choose_files)
        paste = QPushButton("Вставить из буфера")
        paste.setToolTip("Вставить изображение или локальный файл из буфера обмена")
        paste.clicked.connect(panel.paste)
        row.addWidget(choose)
        row.addWidget(paste)
        row.addStretch()
        layout.addLayout(row)

    def _active(self, active: bool) -> None:
        self.setProperty("dragActive", active)
        self.title.setText(
            "Отпустите, чтобы выбрать изображение" if active else "Перетащите изображение сюда"
        )
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if image_paths(event.mimeData()):
            self._active(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._active(False)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        self._active(False)
        if paths := image_paths(event.mimeData()):
            self.panel.set_files(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.panel.choose_files()
            event.accept()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.panel.choose_files()
            event.accept()
        elif event.matches(QKeySequence.StandardKey.Paste):
            self.panel.paste()
            event.accept()
        else:
            super().keyPressEvent(event)
