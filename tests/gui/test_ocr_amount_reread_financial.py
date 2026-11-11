"""Спорная сумма не проходит в журнал при отключённых флажках."""

import pytest

from personal_finance.application.ocr import Ocr
from personal_finance.application.ocr_cards import OcrHistoryPreview
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.infrastructure.ocr.amount_reread import HistoryAmountReread
from personal_finance.infrastructure.ocr.generic import GenericHistoryAdapter
from tests.gui.test_ocr_batch_gui import dialog_for
from tests.unit.test_ocr_amount_reread import RegionEngine, example
from tests.unit.test_ocr_dated_amount_reread import DatedRegionEngine, dated_example


@pytest.mark.parametrize(
    ("source", "engine", "balance"),
    [(example, RegionEngine, 5000), (dated_example, DatedRegionEngine, -4000)],
    ids=["standalone-amount", "dated-amount-with-currency"],
)
def test_reread_conflict_does_not_enter_atomic_batch(qtbot, session, source, engine, balance):
    image, document = source()
    history = HistoryAmountReread(Ocr(engine()))(image, GenericHistoryAdapter().parse(document))
    preview = OcrHistoryPreview(LoadedOcrImage(image.sha256, image), history, UNIVERSAL_PROFILE)
    dialog, account = dialog_for(qtbot, session, preview=preview)
    assert len(dialog.rows) == 2
    assert not dialog.rows[0].include.isChecked() and dialog.rows[1].include.isChecked()
    dialog.prepare()
    qtbot.waitUntil(lambda: dialog._job is None and dialog.saved_count == 1, timeout=15000)
    assert len(session.ledger.operations()) == 1
    assert (
        next(b.amount.minor for b in session.ledger.balances() if b.account_id == account.id)
        == balance
    )
    with session.engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM ocr_records").scalar_one() == 1
