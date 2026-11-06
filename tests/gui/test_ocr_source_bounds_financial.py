"""Неполная область суммы не записывается даже без флажков подтверждения."""

from dataclasses import replace

import pytest

from personal_finance.application.ocr_cards import OcrCardPreview, OcrHistoryPreview
from personal_finance.application.ocr_review import OcrReviewDefaults
from personal_finance.domain.ocr import LoadedOcrImage, OcrBox
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter, GenericHistoryAdapter
from personal_finance.presentation.ocr_batch import OcrBatchDialog
from personal_finance.presentation.ocr_preview import OcrPreviewDialog
from tests.gui.test_ocr_batch_gui import dialog_for
from tests.ocr_fixtures import document, png_image


def clipped_source(lines, edge):
    image = png_image()
    original = document(lines, image_sha256=image.sha256)
    left, top = {"left": (0, 500), "right": (900, 500), "top": (650, 0), "bottom": (650, 1368)}[
        edge
    ]
    words = tuple(
        replace(word, box=OcrBox(left, top, 180, 32)) if "-50 RUB" in word.text else word
        for word in original.words
    )
    return LoadedOcrImage("3" * 64, image), replace(original, words=words)


def counts(session):
    with session.engine.connect() as connection:
        audits = connection.exec_driver_sql("SELECT count(*) FROM ocr_records").scalar_one()
    return len(session.ledger.operations()), audits


@pytest.mark.parametrize("edge", ("left", "right", "top", "bottom"))
def test_clipped_card_cannot_prepare_with_confirmations_disabled(qtbot, session, edge):
    loaded, source = clipped_source(("Детали операции", "-50 RUB", "27.10.2026"), edge)
    card = GenericCardAdapter().parse(source)
    dialog = OcrPreviewDialog(
        OcrCardPreview(loaded, card, UNIVERSAL_PROFILE),
        review_defaults=OcrReviewDefaults(reference_year=2026, confirm_fields=False),
        allow_financial_prepare=True,
    )
    qtbot.addWidget(dialog)
    assert not dialog.finish.isEnabled() and not dialog.prepare_button.isEnabled()
    dialog._validate(financial=True)
    assert dialog.reviewed is None and not dialog.financial_requested
    assert counts(session) == (0, 0) and card.document is source
    assert "снимок" in dialog.validation_note.text().casefold()


def test_clipped_row_is_excluded_and_cannot_enter_atomic_package(qtbot, session):
    loaded, source = clipped_source(
        (
            "История операций",
            "27.10.2026",
            "Учебное поступление +60 RUB",
            "Учебный магазин -50 RUB",
        ),
        "right",
    )
    preview = OcrHistoryPreview(loaded, GenericHistoryAdapter().parse(source), UNIVERSAL_PROFILE)
    dialog, account = dialog_for(qtbot, session, preview=preview)
    assert len(dialog.rows) == 2 and not dialog.rows[1].include.isChecked()
    dialog.rows[1].include.setChecked(True)
    dialog.prepare()
    assert dialog._job is None and counts(session) == (0, 0)
    assert "Ничего не записано" in dialog.notice.text()
    dialog.rows[1].include.setChecked(False)
    dialog.prepare()
    qtbot.waitUntil(lambda: dialog._job is None and dialog.saved_count == 1, timeout=15000)
    assert counts(session) == (1, 1)
    assert session.ledger.operations()[0].draft.amount.minor == 6000
    repeated = OcrBatchDialog(
        preview,
        session.ocr_importing,
        session.catalog.accounts(),
        session.catalog.categories(),
        dialog.preferences,
    )
    qtbot.addWidget(repeated)
    repeated.common_account.setCurrentIndex(repeated.common_account.findData(account.id))
    repeated.prepare()
    qtbot.waitUntil(lambda: repeated._job is None, timeout=15000)
    assert repeated.saved_count == 0 and counts(session) == (1, 1)
    session.check_integrity()
