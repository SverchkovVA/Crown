"""Проверка упаковки Qt/парсеров без OCR-движка и без финансовой книги."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from PySide6.QtCore import QBuffer, QCoreApplication, QEvent, QIODevice
from PySide6.QtGui import QImage, QImageWriter

from personal_finance.application.ocr_cards import (
    CardRecognition,
    OcrCardPreview,
    OcrHistoryPreview,
)
from personal_finance.domain.ocr import OcrBox, OcrDocument, OcrEngineInfo, OcrWord
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.domain.ocr_settings import OcrPreferences
from personal_finance.infrastructure.ocr.generic import GenericHistoryAdapter
from personal_finance.infrastructure.ocr.images import decode_ocr_image
from personal_finance.infrastructure.ocr.t_bank import TBankCardAdapter
from personal_finance.infrastructure.ocr.t_bank_history import TBankHistoryAdapter
from personal_finance.infrastructure.ocr.vtb import VtbCardAdapter
from personal_finance.infrastructure.ocr.vtb_history import VtbHistoryAdapter
from personal_finance.presentation.ocr_batch import OcrBatchDialog
from personal_finance.presentation.ocr_history import OcrHistoryDialog
from personal_finance.presentation.ocr_preview import OcrPreviewDialog


def check_packaged_ocr_preview() -> None:
    pixels = QImage(320, 120, QImage.Format.Format_RGB32)
    pixels.fill("white")
    for format in (b"PNG", b"JPEG"):
        buffer = QBuffer()
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not QImageWriter(
            buffer, format
        ).write(pixels):
            raise RuntimeError("Кодек проверочного изображения недоступен.")
        loaded = decode_ocr_image(buffer.data().data())
        document = OcrDocument(
            loaded.image.sha256,
            320,
            120,
            OcrEngineInfo("tesseract", "self-test-without-engine", (("rus", "1" * 64),)),
            (
                OcrWord("27.10.2026", OcrBox(10, 5, 100, 20), Decimal(95), (1, 1, 1), 1),
                OcrWord("Перевод", OcrBox(10, 30, 100, 20), Decimal(95), (1, 1, 2), 1),
                OcrWord("-1,23 RUB", OcrBox(200, 60, 100, 20), Decimal(95), (1, 1, 3), 1),
            ),
        )
        vtb_document = OcrDocument(
            document.image_sha256,
            document.width,
            document.height,
            document.engine,
            (
                OcrWord("Детали операции", OcrBox(10, 5, 160, 15), Decimal(95), (1, 1, 1), 1),
                OcrWord(
                    "Дата и время: 27.10.2026", OcrBox(10, 25, 160, 15), Decimal(95), (1, 1, 2), 1
                ),
                OcrWord("-1,23 RUB", OcrBox(200, 60, 100, 20), Decimal(95), (1, 1, 3), 1),
            ),
        )
        vtb_card = VtbCardAdapter().parse(vtb_document)
        vtb_history_document = OcrDocument(
            document.image_sha256,
            document.width,
            document.height,
            document.engine,
            (
                OcrWord("История операций", OcrBox(10, 5, 160, 15), Decimal(95), (1, 1, 1), 1),
                OcrWord("27.10.2026", OcrBox(10, 25, 160, 15), Decimal(95), (1, 1, 2), 1),
                OcrWord("Магазин", OcrBox(10, 60, 100, 20), Decimal(95), (1, 1, 3), 1),
                OcrWord("-1,23 RUB", OcrBox(200, 60, 100, 20), Decimal(95), (1, 1, 3), 2),
            ),
        )
        vtb_history = VtbHistoryAdapter().parse(vtb_history_document)
        generic_document = replace(
            vtb_history_document,
            words=(
                *vtb_history_document.words[:-1],
                replace(vtb_history_document.words[-1], text="−473 , 15"),
            ),
        )
        generic = GenericHistoryAdapter().parse(generic_document)
        if (
            generic.blocked_reason
            or len(generic.cards) != 1
            or generic.cards[0].amount.value != 47315
            or generic.cards[0].currency.value is not None
        ):
            raise RuntimeError("Неверный разбор дробной суммы общего профиля.")
        inline_document = replace(
            generic_document,
            words=(
                *generic_document.words[:2],
                OcrWord("Магазин", OcrBox(10, 60, 80, 20), Decimal(95), (1, 1, 3), 1),
                OcrWord("22.10.2026", OcrBox(105, 60, 95, 20), Decimal(95), (1, 1, 3), 2),
                OcrWord("473,15 RUB", OcrBox(210, 60, 100, 20), Decimal(95), (1, 1, 3), 3),
            ),
        )
        inline_history = GenericHistoryAdapter().parse(inline_document)
        if (
            inline_history.blocked_reason
            or len(inline_history.cards) != 1
            or inline_history.cards[0].amount.value != 47315
            or inline_history.cards[0].occurred_on.value != date(2026, 10, 22)
        ):
            raise RuntimeError("Дата рядом с суммой общего профиля не выделена.")
        for box in (
            OcrBox(0, 60, 100, 20),
            OcrBox(220, 60, 100, 20),
            OcrBox(200, 0, 100, 20),
            OcrBox(200, 100, 100, 20),
        ):
            clipped_document = replace(
                generic_document,
                words=(*generic_document.words[:-1], replace(generic_document.words[-1], box=box)),
            )
            clipped_history = GenericHistoryAdapter().parse(clipped_document)
            if (
                len(clipped_history.cards) != 1
                or clipped_history.cards[0].amount.value != 47315
                or not clipped_history.cards[0].blocked_reason
                or clipped_history.cards[0].document is not clipped_document
            ):
                raise RuntimeError("Неполная область суммы не защищена от финансовой записи.")
        generic_dialog = OcrBatchDialog(
            OcrHistoryPreview(loaded, generic, UNIVERSAL_PROFILE),
            None,
            [],
            [],
            OcrPreferences(confirm_fields=False, review_before_save=False),
        )
        try:
            generic_dialog.prepare()
            if (
                len(generic_dialog.reviewed_cards) != 1
                or generic_dialog.reviewed_cards[0].amount.minor != 47315
                or generic_dialog.saved_count != 0
                or generic_dialog._job is not None
            ):
                raise RuntimeError("Универсальный предпросмотр без записи недоступен.")
        finally:
            generic_dialog.close()
            generic_dialog.deleteLater()
            QCoreApplication.sendPostedEvents(generic_dialog, QEvent.Type.DeferredDelete)
        if (
            vtb_card.amount.value != 123
            or vtb_card.occurred_on.value is None
            or len(vtb_history.cards) != 1
        ):
            raise RuntimeError("Разбор проверочного формата ВТБ недоступен.")
        single = OcrPreviewDialog(OcrCardPreview(loaded, TBankCardAdapter().parse(document)))
        history = OcrHistoryDialog(OcrHistoryPreview(loaded, TBankHistoryAdapter().parse(document)))
        try:
            for check in single.checks.values():
                check.setChecked(True)
            single._validate()
            if (
                single.reviewed is None
                or single.reviewed.amount.minor != 123
                or history.model.rowCount() != 1
                or not history.review_button.isEnabled()
            ):
                raise RuntimeError("Предпросмотр карточки или истории не прошёл проверку.")
        finally:
            single.deleteLater()
            history.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def check_local_ocr_runtime(recognitions: tuple[CardRecognition, ...]) -> None:
    """Настоящий движок получает только PNG с вымышленными значениями."""
    import tempfile
    from datetime import time
    from pathlib import Path

    from matplotlib import get_data_path
    from PIL import Image, ImageDraw, ImageFont

    from personal_finance.application.ocr_cards import OcrCardPreview
    from personal_finance.domain.ocr_cards import CashDirection

    pixels = Image.new("RGB", (1080, 1400), "white")
    painter = ImageDraw.Draw(pixels)
    font = ImageFont.truetype(str(Path(get_data_path()) / "fonts/ttf/DejaVuSans.ttf"), 35)
    for y, caption in (
        (80, "Детали операции"),
        (180, "Покупка"),
        (290, "Списано: −473    ,    15"),
        (350, "Валюта: руб."),
        (400, "Дата операции: 22.10.2026 09:18"),
        (510, "Получатель: Вымышленный магазин"),
    ):
        painter.text((50, y), caption, font=font, fill="black")
    with tempfile.TemporaryDirectory(prefix="pf-ocr-self-test-") as directory:
        path = Path(directory) / "Синтетическая карточка.png"
        pixels.save(path, format="PNG")
        preview = recognitions[0].analyze(path)
    if not isinstance(preview, OcrCardPreview) or preview.bank_name != UNIVERSAL_PROFILE:
        raise RuntimeError("Не распознана одна проверочная карточка.")
    card = preview.card
    if (
        card.blocked_reason
        or card.amount.value != 47315
        or card.currency.value != "RUB"
        or card.direction.value != CashDirection.OUT
        or card.occurred_on.value != date(2026, 10, 22)
        or card.local_time.value != time(9, 18)
    ):
        raise RuntimeError("Проверочный OCR дал неверные поля.")

    # Отдельная тёмная история воспроизводит потерю строки при неверном чтении ₽.
    from personal_finance.application.ocr_cards import OcrHistoryPreview
    from personal_finance.application.ocr_review import OcrReviewDefaults, suggest_corrections

    history_pixels = Image.new("RGB", (1080, 1500), "#15171a")
    draw = ImageDraw.Draw(history_pixels)
    font_path = str(Path(get_data_path()) / "fonts/ttf/DejaVuSans.ttf")
    regular = ImageFont.truetype(font_path, 34)
    small = ImageFont.truetype(font_path, 29)
    draw.text((35, 30), "История операций", font=ImageFont.truetype(font_path, 40), fill="white")
    values = (
        ("Вымышленный магазин", "Покупки", "−123,45", 12345, CashDirection.OUT),
        ("Вымышленный перевод", "Переводы", "+678,90", 67890, CashDirection.IN),
        ("Тестовое кафе", "Питание", "−45,00", 4500, CashDirection.OUT),
        ("Тестовые продукты", "Продукты", "−99,01", 9901, CashDirection.OUT),
        ("Тестовая подписка", "Услуги", "−250,50", 25050, CashDirection.OUT),
        ("Возврат тестовой покупки", "Возврат", "+321,00", 32100, CashDirection.IN),
    )
    top = 120
    for index, (name, category, amount, _, _) in enumerate(values):
        if index in (0, 3):
            draw.text(
                (35, top), "24 октября" if index == 0 else "23 октября", font=regular, fill="white"
            )
            top += 65
        draw.text((35, top), name, font=small, fill="white")
        draw.text((1045, top), amount + " ₽", font=regular, fill="white", anchor="ra")
        draw.text((35, top + 44), category, font=small, fill="white")
        if index == 3:
            draw.text((35, top + 80), "+1 бонус", font=small, fill="white")
        top += 165
    with tempfile.TemporaryDirectory(prefix="pf-ocr-history-self-test-") as directory:
        path = Path(directory) / "Вымышленная тёмная история.png"
        history_pixels.save(path, format="PNG")
        history_preview = recognitions[0].analyze(path)
    if (
        not isinstance(history_preview, OcrHistoryPreview)
        or len(history_preview.history.cards) != 6
    ):
        raise RuntimeError("Не выделены шесть денежных строк тёмной истории.")
    defaults = OcrReviewDefaults(reference_year=2026)
    for index, (draft, expected) in enumerate(
        zip(history_preview.history.cards, values, strict=True)
    ):
        suggestion = suggest_corrections(draft, defaults)
        if (
            draft.amount.value != expected[3]
            or suggestion.direction != expected[4]
            or suggestion.currency != "RUB"
            or suggestion.occurred_on != ("24.10.2026" if index < 3 else "23.10.2026")
        ):
            raise RuntimeError("Неверные сумма, направление или дата тёмной истории.")
