"""Неоднозначная геометрия суммы не обходится общей сверкой полей."""

from dataclasses import replace

import pytest

from personal_finance.application.ocr_cards import OcrCardPreview
from personal_finance.application.ocr_review import OcrReviewDefaults
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.domain.ocr_cards import CashDirection
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter
from personal_finance.presentation.ocr_preview import OcrPreviewDialog
from tests.gui.test_ocr_source_bounds_financial import counts
from tests.ocr_fixtures import png_image
from tests.unit.test_ocr_amount_geometry import source_for


@pytest.mark.parametrize("confirm_fields", [True, False])
def test_spatially_ambiguous_amount_cannot_prepare_after_manual_entry(
    qtbot, session, confirm_fields
):
    image = png_image()
    source = replace(source_for("card"), image_sha256=image.sha256)
    words = source.words
    card = GenericCardAdapter().parse(source)
    dialog = OcrPreviewDialog(
        OcrCardPreview(LoadedOcrImage("3" * 64, image), card, UNIVERSAL_PROFILE),
        review_defaults=OcrReviewDefaults(reference_year=2026, confirm_fields=confirm_fields),
        allow_financial_prepare=True,
    )
    qtbot.addWidget(dialog)
    assert dialog.amount.text() == ""
    dialog.amount.setText("732,19")
    dialog.direction.setCurrentIndex(dialog.direction.findData(CashDirection.OUT))
    if confirm_fields:
        dialog.check_all.click()
    assert not dialog.finish.isEnabled() and not dialog.prepare_button.isEnabled()
    dialog._validate(financial=True)
    assert dialog.reviewed is None and not dialog.financial_requested
    assert "колонк" in dialog.validation_note.text().casefold()
    assert counts(session) == (0, 0) and source.words is words
