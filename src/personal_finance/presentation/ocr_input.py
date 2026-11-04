"""Явный выбор локального снимка в журнале операций."""

from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
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
        self.notice = QLabel("Перетащите PNG/JPEG, выберите файлы или вставьте снимок.")
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        choose = QPushButton("Выбрать файлы…")
        paste = QPushButton("Вставить из буфера")
        self.recognize = QPushButton("Распознать")
        self.recognize.setObjectName("primary")
        close = QPushButton("Свернуть")
        choose.clicked.connect(self.choose_files)
        paste.clicked.connect(self.paste)
        self.recognize.clicked.connect(self.submit)
        close.clicked.connect(self.hide)
        self.files.currentIndexChanged.connect(self._update)
        layout = QVBoxLayout(self)
        layout.addWidget(self.notice)
        row = QHBoxLayout()
        row.addWidget(self.mode)
        row.addWidget(self.files, 1)
        row.addWidget(self.recognize)
        layout.addLayout(row)
        actions = QHBoxLayout()
        actions.addWidget(choose)
        actions.addWidget(paste)
        actions.addStretch()
        actions.addWidget(close)
        layout.addLayout(actions)
        self._update()

    def _update(self) -> None:
        self.recognize.setEnabled(self.files.currentIndex() >= 0)

    def reset(self) -> None:
        self._source = None
        self.files.clear()
        self.notice.setText("Перетащите PNG/JPEG, выберите файлы или вставьте снимок.")
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
