"""Проверка полей рядом с изображением; запись в книгу ещё не подключена."""

from decimal import Decimal

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPen, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from personal_finance.application.ocr_cards import OcrCardPreview
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import OcrDocument, OcrImage
from personal_finance.domain.ocr_cards import (
    DIRECTION_LABELS,
    ISSUE_LABELS,
    CardCorrections,
    CashDirection,
    OcrField,
    ReviewedOcrCard,
    review_card,
)


def plain(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class SourceImageView(QGraphicsView):
    def __init__(self, image: OcrImage, document: OcrDocument) -> None:
        super().__init__()
        self.document = document
        self._fit = True
        scene = QGraphicsScene(self)
        self.setScene(scene)
        decoded = QImage.fromData(image.png)
        if decoded.isNull() or (decoded.width(), decoded.height()) != (image.width, image.height):
            raise ValidationError("Не удалось показать исходное изображение.")
        scene.addPixmap(QPixmap.fromImage(decoded))
        self.outline = QGraphicsRectItem()
        self.outline.setPen(QPen(QColor("#c93824"), 3))
        self.outline.setBrush(QColor(255, 190, 50, 45))
        scene.addItem(self.outline)
        self.outline.hide()
        scene.setSceneRect(0, 0, image.width, image.height)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setMinimumWidth(320)

    def fit_image(self) -> None:
        self._fit = True
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def zoom(self, factor: float) -> None:
        target = self.transform().m11() * factor
        if 0.03 <= target <= 8:
            self._fit = False
            self.scale(factor, factor)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._fit:
            self.fit_image()

    def show_evidence(self, indices: tuple[int, ...]) -> None:
        if not indices:
            self.outline.hide()
            return
        boxes = [self.document.words[i].box for i in indices]
        left = max(0, min(b.left for b in boxes) - 16)
        top = max(0, min(b.top for b in boxes) - 16)
        right = min(self.document.width, max(b.left + b.width for b in boxes) + 16)
        bottom = min(self.document.height, max(b.top + b.height for b in boxes) + 16)
        self.outline.setRect(QRectF(left, top, right - left, bottom - top))
        self.outline.show()
        self.centerOn(self.outline)


class OcrPreviewDialog(QDialog):
    def __init__(self, preview: OcrCardPreview, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.preview = preview
        self.reviewed: ReviewedOcrCard | None = None
        self.checks: dict[str, QCheckBox] = {}
        self.evidence_buttons: dict[str, QPushButton] = {}
        self.setWindowTitle("Проверка карточки Т-Банка")
        self.resize(1180, 720)
        self.setMinimumSize(920, 580)
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain(
                "Сверьте поля с изображением. Результат проверки останется в памяти; "
                "запись операции в книгу ещё не подключена."
            )
        )
        split = QSplitter(Qt.Orientation.Horizontal)
        image_panel = QWidget()
        image_layout = QVBoxLayout(image_panel)
        self.image_view = SourceImageView(preview.source.image, preview.card.document)
        image_layout.addWidget(self.image_view, 1)
        buttons = QHBoxLayout()
        for text, action in (
            ("Вписать", self.image_view.fit_image),
            ("Увеличить", lambda: self.image_view.zoom(1.3)),
            ("Уменьшить", lambda: self.image_view.zoom(1 / 1.3)),
        ):
            button = QPushButton(text)
            button.clicked.connect(action)
            buttons.addWidget(button)
        image_layout.addLayout(buttons)
        split.addWidget(image_panel)
        scroll = QScrollArea()
        scroll.setObjectName("ocrScroll")
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("ocrBody")
        form = QFormLayout(body)
        self.notice = plain(
            preview.card.blocked_reason
            or "Оценка слов не гарантирует правильность суммы или даты. "
            "Четыре обязательных поля нужно подтвердить вручную."
        )
        form.addRow(self.notice)
        card = preview.card
        self.amount = QLineEdit()
        self.amount.setMaxLength(80)
        if card.amount.value is not None:
            minor = card.amount.value
            self.amount.setText(f"{minor // 100:,}".replace(",", " ") + f",{minor % 100:02d}")
        self.currency = QComboBox()
        self.currency.addItem("Не определена", "")
        self.currency.addItem("RUB — российский рубль", "RUB")
        if card.currency.value == "RUB":
            self.currency.setCurrentIndex(1)
        self.direction = QComboBox()
        self.direction.addItem("Не определено", None)
        for direction, text in DIRECTION_LABELS.items():
            self.direction.addItem(text, direction)
        if card.direction.value is not None:
            self.direction.setCurrentIndex(self.direction.findData(card.direction.value))
        self.occurred_on = QLineEdit()
        self.occurred_on.setMaxLength(10)
        self.occurred_on.setPlaceholderText("ДД.ММ.ГГГГ")
        if card.occurred_on.value is not None:
            value = card.occurred_on.value
            self.occurred_on.setText(f"{value.day:02d}.{value.month:02d}.{value.year:04d}")
        self.local_time = QLineEdit()
        self.local_time.setMaxLength(5)
        self.local_time.setPlaceholderText("ЧЧ:ММ или пусто")
        if card.local_time.value is not None:
            self.local_time.setText(card.local_time.value.strftime("%H:%M"))
        self.description = QLineEdit(card.description.value or "")
        self.description.setMaxLength(500)
        for name, title in (
            ("amount", "Сумма без знака"),
            ("currency", "Валюта"),
            ("direction", "Движение денег"),
            ("occurred_on", "Дата"),
            ("local_time", "Время, если указано"),
            ("description", "Описание"),
        ):
            field = getattr(card, name)
            editor = getattr(self, name)
            panel = QWidget()
            column = QVBoxLayout(panel)
            column.setContentsMargins(0, 0, 0, 0)
            column.addWidget(editor)
            row = QHBoxLayout()
            show = QPushButton("Показать источник")
            show.setEnabled(bool(field.word_indices))
            show.clicked.connect(
                lambda checked=False, indices=field.word_indices: self.image_view.show_evidence(
                    indices
                )
            )
            self.evidence_buttons[name] = show
            row.addWidget(show)
            if name in ("amount", "currency", "direction", "occurred_on"):
                check = QCheckBox("Проверено")
                self.checks[name] = check
                row.addWidget(check)
                signal = (
                    editor.textChanged
                    if isinstance(editor, QLineEdit)
                    else editor.currentIndexChanged
                )
                signal.connect(lambda *args, check=check: check.setChecked(False))
                check.toggled.connect(self._update)
            column.addLayout(row)
            column.addWidget(plain(self._source_status(field)))
            form.addRow(title, panel)
        self.single = QCheckBox("Это одна проведённая операция")
        self.single.toggled.connect(self._update)
        scroll.setWidget(body)
        split.addWidget(scroll)
        split.setSizes([590, 530])
        layout.addWidget(split, 1)
        layout.addWidget(self.single)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Cancel).setText("Закрыть")
        self.finish = box.addButton("Завершить проверку", QDialogButtonBox.ButtonRole.AcceptRole)
        self.finish.clicked.connect(self._validate)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self._update()

    def _source_status[T](self, field: OcrField[T]) -> str:
        confidence = self.preview.card.confidence(field)
        score = f" · оценка слов {confidence:.1f}%" if isinstance(confidence, Decimal) else ""
        status = "; ".join(ISSUE_LABELS[i] for i in field.issues) or "Предложение OCR"
        return "Источник: " + status + score

    def _update(self) -> None:
        self.finish.setEnabled(
            not self.preview.card.blocked_reason
            and self.single.isChecked()
            and all(check.isChecked() for check in self.checks.values())
        )

    def _validate(self) -> None:
        self.reviewed = None
        raw_direction = self.direction.currentData()
        direction = CashDirection(raw_direction) if raw_direction in ("in", "out") else None
        try:
            self.reviewed = review_card(
                self.preview.card,
                CardCorrections(
                    self.amount.text(),
                    self.currency.currentData(),
                    direction,
                    self.occurred_on.text(),
                    self.local_time.text(),
                    self.description.text(),
                ),
                confirmed_fields=frozenset(
                    name for name, check in self.checks.items() if check.isChecked()
                ),
                single_completed_operation=self.single.isChecked(),
            )
        except ValidationError as error:
            QMessageBox.warning(self, "Проверьте поля", str(error))
            return
        self.accept()
