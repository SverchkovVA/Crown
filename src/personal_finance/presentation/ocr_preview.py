"""Проверка полей рядом с изображением и отдельный запрос финансовой подготовки."""

from decimal import Decimal

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFocusEvent, QImage, QMouseEvent, QPen, QPixmap, QResizeEvent
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
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from personal_finance.application.ocr_cards import OcrCardPreview
from personal_finance.application.ocr_review import OcrReviewDefaults, suggest_corrections
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
from personal_finance.presentation.inline_forms import containing_panel
from personal_finance.presentation.ocr_metadata import context_note


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
        self._fitting = False
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scene = QGraphicsScene(self)
        self._scene = scene
        self.setScene(scene)
        decoded = QImage.fromData(image.png)
        if decoded.isNull() or (decoded.width(), decoded.height()) != (image.width, image.height):
            raise ValidationError("Не удалось показать исходное изображение.")
        self._pixmap_item = scene.addPixmap(QPixmap.fromImage(decoded))
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
        if self._fitting:
            return
        self._fitting = True
        try:
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        finally:
            self._fitting = False

    def zoom(self, factor: float) -> None:
        target = self.transform().m11() * factor
        if 0.03 <= target <= 8:
            self._fit = False
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
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
        if not self._fit:
            self.centerOn(self.outline.rect().center())


class OptionalMaskedEdit(QLineEdit):
    """Маска вставляет разделители; пустое поле не превращается в дату или время."""

    def __init__(self, mask: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setInputMask(mask)
        self.setClearButtonEnabled(True)

    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        self.deselect()
        self.setCursorPosition(0)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        super().mousePressEvent(event)
        self.deselect()
        self.setCursorPosition(0)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        super().mouseDoubleClickEvent(event)
        self.deselect()
        self.setCursorPosition(0)

    def text(self) -> str:
        value = super().text()
        return value if any(character.isdigit() for character in value) else ""


class OcrPreviewDialog(QDialog):
    def __init__(
        self,
        preview: OcrCardPreview,
        parent: QWidget | None = None,
        *,
        allow_financial_prepare: bool = False,
        review_defaults: OcrReviewDefaults | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("warningsAtBottom", True)
        self.setProperty("ownsContentScrolling", True)
        self.preview = preview
        self.review_defaults = review_defaults or OcrReviewDefaults.for_today()
        suggestions = suggest_corrections(preview.card, self.review_defaults)
        self.reviewed: ReviewedOcrCard | None = None
        self.financial_requested = False
        self.checks: dict[str, QCheckBox] = {}
        self.evidence_buttons: dict[str, QPushButton] = {}
        self.setWindowTitle("Карточка операции")
        self.resize(1180, 720)
        self.setMinimumSize(920, 580)
        layout = QVBoxLayout(self)
        self.review_info = QWidget()
        self.review_info.setProperty("stickyNotice", True)
        information = QVBoxLayout(self.review_info)
        information.setContentsMargins(0, 0, 0, 0)
        summary_row = QHBoxLayout()
        self.summary = plain(
            f"Если нет в снимке: {self.review_defaults.currency} · "
            f"{self.review_defaults.reference_year}"
        )
        summary_row.addWidget(self.summary, 1)
        self.details_toggle = QToolButton()
        self.details_toggle.setText("Сведения")
        self.details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details_toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.details_toggle.setCheckable(True)
        self.details_toggle.setAccessibleName("Сведения об источнике и точности распознавания")
        self.details_toggle.setToolTip("Настройки, ограничения распознавания и оценки полей")
        summary_row.addWidget(self.details_toggle)
        information.addLayout(summary_row)
        self.info_body = QWidget()
        self.info_body.hide()
        info_layout = QVBoxLayout(self.info_body)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.addWidget(
            plain(
                "Проверка остаётся в памяти. "
                + (
                    "Запись в книгу — отдельным действием."
                    if allow_financial_prepare
                    else "Для финансового подтверждения откройте книгу текущей схемы."
                )
            )
        )
        self.details_toggle.toggled.connect(self._toggle_details)
        self.defaults_note = plain(
            f"Валюта и год по настройке: {self.review_defaults.currency}, "
            f"{self.review_defaults.reference_year}. Сверьте предложения с изображением. "
            "Исходные данные OCR сохраняются без изменений."
        )
        metadata_note = context_note(preview.card.metadata)
        if metadata_note:
            self.defaults_note.setText(self.defaults_note.text() + "\n" + metadata_note)
        info_layout.addWidget(self.defaults_note)
        split = QSplitter(Qt.Orientation.Horizontal)
        image_panel = QWidget()
        image_layout = QVBoxLayout(image_panel)
        self.image_view = SourceImageView(preview.source.image, preview.card.document)
        image_layout.addWidget(self.image_view, 1)
        buttons = QHBoxLayout()
        for text, action in (
            ("Целиком", self.image_view.fit_image),
            ("+ Увеличить", lambda: self.image_view.zoom(1.3)),
            ("− Уменьшить", lambda: self.image_view.zoom(1 / 1.3)),
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
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.notice = plain(
            preview.card.blocked_reason
            or "Оценка слов не гарантирует правильность суммы или даты. "
            + (
                "Сверьте сумму, валюту, движение денег и дату с изображением."
                if self.review_defaults.confirm_fields
                else "Флажки проверки полей отключены в настройках. "
                "Проверьте значения перед продолжением."
            )
        )
        info_layout.addWidget(self.notice)
        if allow_financial_prepare and not preview.card.amount.word_indices:
            layout.addWidget(
                plain(
                    "Финансовая подготовка недоступна: OCR не выделил область исходной суммы. "
                    "Проверку полей можно завершить без записи в книгу."
                )
            )
        card = preview.card
        self.amount = QLineEdit()
        self.amount.setMaxLength(80)
        if card.amount.value is not None:
            minor = card.amount.value
            self.amount.setText(f"{minor // 100:,}".replace(",", " ") + f",{minor % 100:02d}")
        self.currency = QComboBox()
        self.currency.addItem("Не определена", "")
        self.currency.addItem("RUB — российский рубль", "RUB")
        if suggestions.currency and self.currency.findData(suggestions.currency) < 0:
            self.currency.addItem(suggestions.currency + " — из источника", suggestions.currency)
        self.currency.setCurrentIndex(max(0, self.currency.findData(suggestions.currency)))
        self.direction = QComboBox()
        self.direction.addItem("Не определено", None)
        for direction, text in DIRECTION_LABELS.items():
            self.direction.addItem(text, direction)
        if suggestions.direction is not None:
            self.direction.setCurrentIndex(self.direction.findData(suggestions.direction))
        self.occurred_on = OptionalMaskedEdit("00.00.0000;_")
        self.occurred_on.setPlaceholderText("ДД.ММ.ГГГГ")
        self.occurred_on.setToolTip(
            "Введите 8 цифр даты: точки появятся сами. Неизвестную дату оставьте пустой."
        )
        self.occurred_on.setText(suggestions.occurred_on)
        self.local_time = OptionalMaskedEdit("00:00;_")
        self.local_time.setPlaceholderText("ЧЧ:ММ или пусто")
        self.local_time.setToolTip(
            "Введите 4 цифры времени: двоеточие появится само. Время можно оставить пустым."
        )
        self.local_time.setText(suggestions.local_time)
        self.description = QLineEdit(suggestions.description)
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
            heading = QHBoxLayout()
            title_label = plain(title)
            title_label.setBuddy(editor)
            heading.addWidget(title_label, 1)
            editor.setAccessibleName(title)
            editor.setAccessibleDescription(self._source_status(field))
            editor.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            row = QHBoxLayout()
            row.addWidget(editor, 1)
            show = QPushButton("Источник")
            show.setAccessibleName("Показать источник: " + title)
            show.setToolTip(self._source_status(field))
            show.setAccessibleDescription(self._source_status(field))
            show.setWhatsThis(self._source_status(field))
            show.setEnabled(bool(field.word_indices))
            show.clicked.connect(
                lambda checked=False, indices=field.word_indices: self.image_view.show_evidence(
                    indices
                )
            )
            self.evidence_buttons[name] = show
            row.addWidget(show)
            if self.review_defaults.confirm_fields and name in (
                "amount",
                "currency",
                "direction",
                "occurred_on",
            ):
                check = QCheckBox("Проверено")
                check.setAccessibleName(title + ": проверено")
                self.checks[name] = check
                heading.addWidget(check)
                check.toggled.connect(self._update)
            signal = (
                editor.textChanged if isinstance(editor, QLineEdit) else editor.currentIndexChanged
            )
            signal.connect(lambda *args, name=name: self._edited(name))
            column.addLayout(heading)
            column.addLayout(row)
            form.addRow(panel)
        form.addRow(self.info_body)
        scroll.setWidget(body)
        self.fields_scroll = scroll
        self._details_timer = QTimer(self)
        self._details_timer.setSingleShot(True)
        self._details_timer.timeout.connect(self._show_details)
        split.addWidget(scroll)
        split.setSizes([590, 530])
        layout.addWidget(split, 1)
        layout.addWidget(self.review_info)
        self.check_all = QPushButton("Проверить все поля")
        self.check_all.setAccessibleName("Подтвердить проверку всех обязательных полей карточки")
        self.check_all.setToolTip(
            "Нажмите после сверки суммы, валюты, движения денег и даты с изображением."
        )
        self.check_all.setVisible(self.review_defaults.confirm_fields)
        self.check_all.clicked.connect(self._check_all)
        self.validation_note = plain("")
        self.validation_note.setAccessibleName("Состояние проверки OCR")
        layout.addWidget(self.validation_note)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Cancel).setText("Закрыть")
        box.addButton(self.check_all, QDialogButtonBox.ButtonRole.ActionRole)
        self.check_all.setVisible(self.review_defaults.confirm_fields)
        self.finish = box.addButton("Завершить проверку", QDialogButtonBox.ButtonRole.AcceptRole)
        self.finish.clicked.connect(self._validate)
        self.finish.setObjectName("primary" if not allow_financial_prepare else "")
        self.prepare_button: QPushButton | None = None
        if allow_financial_prepare:
            self.prepare_button = box.addButton(
                "Подготовить операцию", QDialogButtonBox.ButtonRole.ActionRole
            )
            self.prepare_button.setObjectName("primary")
            self.prepare_button.clicked.connect(self._request_financial)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self._update()

    def _toggle_details(self, shown: bool) -> None:
        self.info_body.setVisible(shown)
        self.details_toggle.setArrowType(Qt.ArrowType.UpArrow if shown else Qt.ArrowType.DownArrow)
        if shown:
            self._details_timer.start(0)

    def _show_details(self) -> None:
        if self.details_toggle.isChecked():
            self.fields_scroll.verticalScrollBar().setValue(self.info_body.y())

    def _source_status[T](self, field: OcrField[T]) -> str:
        confidence = self.preview.card.confidence(field)
        score = f" · оценка слов {confidence:.1f}%" if isinstance(confidence, Decimal) else ""
        status = "; ".join(ISSUE_LABELS[i] for i in field.issues) or "Предложение OCR"
        return "Источник: " + status + score

    def _edited(self, name: str) -> None:
        self.reviewed = None
        self.financial_requested = False
        if name in self.checks:
            self.checks[name].setChecked(False)
        self._update()

    def _check_all(self) -> None:
        for check in self.checks.values():
            check.setChecked(True)

    def _checked_fields(self) -> frozenset[str]:
        return (
            frozenset(name for name, check in self.checks.items() if check.isChecked())
            if self.review_defaults.confirm_fields
            else frozenset({"amount", "currency", "direction", "occurred_on"})
        )

    def _review_fields(self) -> ReviewedOcrCard:
        raw_direction = self.direction.currentData()
        direction = CashDirection(raw_direction) if raw_direction in ("in", "out") else None
        return review_card(
            self.preview.card,
            CardCorrections(
                self.amount.text(),
                self.currency.currentData(),
                direction,
                self.occurred_on.text(),
                self.local_time.text(),
                self.description.text(),
            ),
            confirmed_fields=self._checked_fields(),
            single_completed_operation=True,
        )

    def _feedback(self, text: str, *, error: bool = False) -> None:
        name = "feedbackError" if error else "muted"
        if self.validation_note.objectName() != name:
            self.validation_note.setObjectName(name)
            self.validation_note.style().unpolish(self.validation_note)
            self.validation_note.style().polish(self.validation_note)
        self.validation_note.setText(text)

    def _update(self) -> None:
        ready = False
        if self.preview.card.blocked_reason:
            self._feedback(self.preview.card.blocked_reason, error=True)
        elif self.review_defaults.confirm_fields and len(self._checked_fields()) != 4:
            self._feedback(
                "Сверьте поля с изображением и нажмите «Проверить все поля». "
                "После правки проверьте поле снова."
            )
        else:
            try:
                self._review_fields()
            except ValidationError as error:
                self._feedback(str(error), error=True)
            else:
                ready = True
                self._feedback("Поля готовы. Продолжение сохраняет только результат этой проверки.")
        self.finish.setEnabled(ready)
        if self.prepare_button is not None:
            self.prepare_button.setEnabled(ready and bool(self.preview.card.amount.word_indices))

    def _request_financial(self) -> None:
        if self.prepare_button is None or not self.prepare_button.isEnabled():
            return
        self._validate(financial=True)

    def _validate(self, *, financial: bool = False) -> None:
        self.reviewed = None
        self.financial_requested = False
        if financial and (self.prepare_button is None or not self.prepare_button.isEnabled()):
            return
        try:
            self.reviewed = self._review_fields()
        except ValidationError as error:
            self._feedback(str(error), error=True)
            if containing_panel(self) is None:
                QMessageBox.warning(self, "Проверьте поля", str(error))
            return
        self.financial_requested = financial
        self.accept()
