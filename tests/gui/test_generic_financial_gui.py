from dataclasses import replace

import pytest
from PySide6.QtWidgets import QDialog

from personal_finance.application.ocr_cards import OcrCardPreview, OcrHistoryPreview
from personal_finance.domain.ledger import OperationKind
from personal_finance.domain.models import AccountKind
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.domain.ocr_settings import OcrPreferences, TransferParty
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter, GenericHistoryAdapter
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.ocr_batch import OcrBatchDialog
from personal_finance.presentation.ocr_preview import OcrPreviewDialog
from tests.gui.test_ocr_batch_gui import dialog_for
from tests.integration.test_generic_ocr_upgrade import schema11 as schema11
from tests.ocr_fixtures import document, history_document, png_image


def history(values=("-100", "+20")):
    image = png_image()
    doc = history_document(header="24 октября", values=values, image_sha256=image.sha256)
    return OcrHistoryPreview(
        LoadedOcrImage("3" * 64, image), GenericHistoryAdapter().parse(doc), UNIVERSAL_PROFILE
    )


def single():
    image = png_image()
    doc = document(("Детали операции", "-12,50 RUB", "03.01.2026"), image_sha256=image.sha256)
    return OcrCardPreview(
        LoadedOcrImage("3" * 64, image), GenericCardAdapter().parse(doc), UNIVERSAL_PROFILE
    )


def window(qtbot, session=None):
    value = MainWindow()
    qtbot.addWidget(value)
    if session is not None:
        value.activate_session(session)
    return value


def test_schema12_history_receives_real_financial_service(qtbot, session, monkeypatch):
    value = window(qtbot, session)
    seen = []
    monkeypatch.setattr(
        OcrBatchDialog,
        "exec",
        lambda dialog: seen.append(dialog.importing) or QDialog.DialogCode.Rejected,
    )
    value._show_ocr_preview(history())
    assert seen == [session.ocr_importing]
    value.lock_book()


@pytest.mark.parametrize("has_legacy", [False, True])
def test_no_book_or_legacy11_stays_readonly(qtbot, schema11, monkeypatch, has_legacy):
    value = window(qtbot, schema11 if has_legacy else None)
    seen = []
    monkeypatch.setattr(
        OcrBatchDialog,
        "exec",
        lambda dialog: seen.append(dialog.importing) or QDialog.DialogCode.Rejected,
    )
    value._show_ocr_preview(history())
    assert seen == [None] and not schema11.ledger.operations()
    if has_legacy:
        value.lock_book()


@pytest.mark.parametrize("current", [False, True])
def test_card_financial_prepare_is_scoped_to_schema12(
    qtbot, session, schema11, monkeypatch, current
):
    chosen = session if current else schema11
    value = window(qtbot, chosen)
    seen = []
    monkeypatch.setattr(
        OcrPreviewDialog,
        "exec",
        lambda dialog: (
            seen.append(dialog.prepare_button is not None) or QDialog.DialogCode.Rejected
        ),
    )
    value._show_ocr_preview(single())
    assert seen == [current] and not chosen.ledger.operations()
    value.lock_book()


def test_general_batch_defaults_are_audited_and_two_rows_net_minus80(qtbot, session):
    dialog, account = dialog_for(qtbot, session, preview=history())
    assert len(dialog.rows) == 2 and all(
        row.occurred_on.text() == "24.10.2026" for row in dialog.rows
    )
    dialog.prepare()
    assert dialog._job is not None, [row.state.text() for row in dialog.rows]
    qtbot.waitUntil(lambda: dialog.saved_count == 2 and dialog._job is None, timeout=15000)
    assert (
        next(v.amount.minor for v in session.ledger.balances() if v.account_id == account.id)
        == -8000
    )
    with session.engine.connect() as c:
        rows = c.exec_driver_sql("SELECT bank,evidence_json FROM ocr_records").fetchall()
    import json

    assert len(rows) == 2 and all(bank == UNIVERSAL_PROFILE for bank, _ in rows)
    for _, payload in rows:
        data = json.loads(payload)
        assert data["fields"]["currency"]["value"] is None
        assert data["fields"]["occurred_on"]["value"] is None
        assert {"currency", "occurred_on"} <= set(data["changed_fields"])
    session.check_integrity()
    repeated, _ = dialog_for(qtbot, session, preview=history())
    repeated.common_account.setCurrentIndex(repeated.common_account.findData(account.id))
    repeated.prepare()
    qtbot.waitUntil(lambda: repeated._job is None, timeout=15000)
    assert repeated.saved_count == 0 and len(session.ledger.operations()) == 2


def test_general_own_party_is_transfer_and_excluded_party_never_writes(qtbot, session):
    other = session.catalog.add_account("Вымышленный другой свой", AccountKind.CASH)
    prefs = OcrPreferences(
        reference_year=2026,
        confirm_fields=False,
        review_before_save=False,
        transfer_parties=(TransferParty("Магазин", other.id),),
    )
    dialog, account = dialog_for(qtbot, session, prefs, history(("+20",)))
    assert dialog.rows[0].kind.currentData() == OperationKind.TRANSFER
    dialog.prepare()
    qtbot.waitUntil(lambda: dialog.saved_count == 1 and dialog._job is None, timeout=15000)
    result = session.ledger.operations()[0]
    assert (
        result.draft.kind == OperationKind.TRANSFER
        and result.draft.account_id == other.id
        and result.draft.destination_account_id == account.id
    )
    excluded, _ = dialog_for(
        qtbot,
        session,
        replace(prefs, transfer_parties=(TransferParty("Магазин", exclude=True),)),
        history(("+30",)),
    )
    assert not excluded.rows[0].include.isChecked() and not excluded.save.isEnabled()
    excluded.prepare()
    assert excluded._job is None and len(session.ledger.operations()) == 1


def test_foreign_currency_does_not_silently_become_default(qtbot, session):
    dialog, _ = dialog_for(qtbot, session, preview=history(("-10 USD", "+20 RUB")))
    assert len(dialog.rows) == 2
    assert dialog.preview.history.cards[0].currency.value == "USD"
    assert (
        not dialog.rows[0].include.isChecked() and "валюта" in dialog.rows[0].state.text().lower()
    )
    dialog.prepare()
    qtbot.waitUntil(lambda: dialog.saved_count == 1 and dialog._job is None, timeout=15000)
    assert (
        len(session.ledger.operations()) == 1
        and session.ledger.operations()[0].draft.amount.minor == 2000
    )
    dialog.rows[0].include.setChecked(True)
    dialog.prepare()
    assert dialog._job is None and len(session.ledger.operations()) == 1


def test_legacy_schema_explains_new_folder_for_generic_card(qtbot, schema11, monkeypatch):
    value = window(qtbot, schema11)
    seen = []
    monkeypatch.setattr(
        OcrPreviewDialog,
        "exec",
        lambda dialog: seen.append(dialog.notice.text()) or QDialog.DialogCode.Rejected,
    )
    value._show_ocr_preview(single())
    assert len(seen) == 1 and "новую папку" in seen[0]
    assert "исходная книга не обновляется" in seen[0]
    value.lock_book()
