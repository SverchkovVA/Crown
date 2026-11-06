"""Сводные показатели не попадают в финансовый пакет, исходник сохраняется."""

import json

import pytest

from personal_finance.application.ocr_cards import OcrHistoryPreview
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.domain.ocr_settings import OcrPreferences
from personal_finance.infrastructure.ocr.generic import GenericHistoryAdapter
from personal_finance.presentation.ocr_batch import OcrBatchDialog
from tests.gui.test_ocr_batch_gui import dialog_for
from tests.ocr_fixtures import document, png_image


def preview(*, with_operations=False, position="inline"):
    image = png_image()
    lines = ["История операций", "27.10.2026"]
    if position == "inline":
        lines.extend(("Траты −5 009,00 RUB", "Доходы +5 600,00 RUB"))
    elif position == "before":
        lines.extend(("Траты", "−5 009,00 RUB", "Доходы", "+5 600,00 RUB"))
    else:
        lines.extend(("−5 009,00 RUB", "Траты", "+5 600,00 RUB", "Доходы"))
    if with_operations:
        lines.extend(("Учебный магазин −73,15 RUB", "Учебное поступление +149,07 RUB"))
    source = document(tuple(lines), image_sha256=image.sha256)
    return OcrHistoryPreview(
        LoadedOcrImage("3" * 64, image),
        GenericHistoryAdapter().parse(source),
        UNIVERSAL_PROFILE,
    )


@pytest.mark.parametrize("position", ["inline", "before", "after"])
def test_summary_only_cannot_start_financial_job(qtbot, session, position):
    dialog = OcrBatchDialog(
        preview(position=position),
        session.ocr_importing,
        session.catalog.accounts(),
        session.catalog.categories(),
        OcrPreferences(reference_year=2026, confirm_fields=False, review_before_save=False),
    )
    qtbot.addWidget(dialog)
    assert not dialog.rows and not dialog.save.isEnabled()
    dialog.prepare()
    assert dialog._job is None and not session.ledger.operations()
    with session.engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM ocr_records").scalar_one() == 0
    session.check_integrity()


@pytest.mark.parametrize("position", ["inline", "before", "after"])
def test_only_individual_rows_are_saved_audited_and_not_repeated(qtbot, session, position):
    source = preview(with_operations=True, position=position)
    dialog, account = dialog_for(qtbot, session, preview=source)
    assert len(dialog.rows) == 2 and source.history.unassigned_fragments == 2
    dialog.prepare()
    qtbot.waitUntil(lambda: dialog._job is None and dialog.saved_count == 2, timeout=15000)
    assert len(session.ledger.operations()) == 2
    assert (
        next(b.amount.minor for b in session.ledger.balances() if b.account_id == account.id)
        == 7592
    )
    with session.engine.connect() as connection:
        payloads = (
            connection.exec_driver_sql("SELECT evidence_json FROM ocr_records").scalars().all()
        )
    assert len(payloads) == 2 and all(
        json.loads(p)["adapter"] == "generic-history-v10" for p in payloads
    )
    data = [json.loads(payload) for payload in payloads]
    assert len({entry["document_sha256"] for entry in data}) == 1
    assert all(
        "Траты" not in " ".join(word["text"] for word in entry["words"])
        and "Доходы" not in " ".join(word["text"] for word in entry["words"])
        for entry in data
    )
    # В книге минимальный аудит полей; весь неизменённый документ связан хешем.
    assert any("Траты" in word.text for word in source.history.document.words)
    assert any("Доходы" in word.text for word in source.history.document.words)
    repeated = OcrBatchDialog(
        source,
        session.ocr_importing,
        session.catalog.accounts(),
        session.catalog.categories(),
        OcrPreferences(reference_year=2026, confirm_fields=False, review_before_save=False),
    )
    qtbot.addWidget(repeated)
    repeated.common_account.setCurrentIndex(repeated.common_account.findData(account.id))
    repeated.prepare()
    qtbot.waitUntil(lambda: repeated._job is None, timeout=15000)
    assert repeated.saved_count == 0 and len(session.ledger.operations()) == 2
    assert "Новых строк нет" in repeated.notice.text()
    session.check_integrity()
