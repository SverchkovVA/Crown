"""Проверка упаковки Qt/парсеров без OCR-движка и без финансовой книги."""

from decimal import Decimal

from PySide6.QtCore import QBuffer, QCoreApplication, QEvent, QIODevice
from PySide6.QtGui import QImage, QImageWriter

from personal_finance.application.ocr_cards import OcrCardPreview, OcrHistoryPreview
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
        generic = GenericHistoryAdapter().parse(vtb_history_document)
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
