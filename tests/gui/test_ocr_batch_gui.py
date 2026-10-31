from dataclasses import replace

from PySide6.QtWidgets import QDialog

from personal_finance.application.ocr_cards import OcrHistoryPreview
from personal_finance.domain.ledger import OperationKind
from personal_finance.domain.models import AccountKind
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.domain.ocr_settings import OcrPreferences, TransferParty
from personal_finance.infrastructure.ocr.t_bank_history import TBankHistoryAdapter
from personal_finance.presentation.ocr_batch import BatchDecisionDialog, OcrBatchDialog
from personal_finance.presentation.ocr_settings import OcrSettingsDialog
from tests.ocr_fixtures import history_document, png_image


def source(values=("-12,3 Р", "+50,00 RUB")):
    image = png_image()
    return OcrHistoryPreview(
        LoadedOcrImage("3" * 64, image),
        TBankHistoryAdapter().parse(
            history_document(header="24 октября", values=values, image_sha256=image.sha256)
        ),
    )


def dialog_for(qtbot, session, preferences=None, preview=None):
    account = session.catalog.add_account("Вымышленный счёт списка", AccountKind.CASH)
    dialog = OcrBatchDialog(
        preview or source(),
        session.ocr_importing,
        session.catalog.accounts(),
        session.catalog.categories(),
        preferences
        or OcrPreferences(reference_year=2026, confirm_fields=False, review_before_save=False),
    )
    qtbot.addWidget(dialog)
    dialog.common_account.setCurrentIndex(dialog.common_account.findData(account.id))
    return dialog, account


def test_bulk_without_confirmations_saves_two_rows_once_in_worker(qtbot, session):
    dialog, account = dialog_for(qtbot, session)
    assert all(r.occurred_on.text() == "24.10.2026" for r in dialog.rows)
    assert dialog.checked is None and dialog.save.isEnabled()
    dialog.prepare()
    assert dialog._job is not None, [r.state.text() for r in dialog.rows]
    qtbot.waitUntil(lambda: dialog.saved_count == 2 and dialog._job is None, timeout=15000)
    assert len(session.ledger.operations()) == 2
    assert all(r.saved for r in dialog.rows)
    assert (
        next(b.amount.minor for b in session.ledger.balances() if b.account_id == account.id)
        == 3770
    )
    assert not dialog.save.isEnabled()
    session.check_integrity()


def test_invalid_row_keeps_other_edits_and_writes_nothing(qtbot, session):
    dialog, _ = dialog_for(qtbot, session)
    dialog.rows[0].amount.setText("−15,40")
    dialog.rows[1].occurred_on.setText("31.02.2026")
    dialog.prepare()
    assert not session.ledger.operations() and dialog._job is None
    assert dialog.rows[0].amount.text() == "−15,40"
    assert "Ничего не записано" in dialog.notice.text()


def test_cancel_final_list_preserves_inline_edits(qtbot, session, monkeypatch):
    prefs = OcrPreferences(reference_year=2026, confirm_fields=False, review_before_save=True)
    dialog, _ = dialog_for(qtbot, session, prefs)
    dialog.rows[0].amount.setText("−18,70")
    seen = []

    def reject(child):
        seen.append(True)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(BatchDecisionDialog, "exec", reject)
    dialog.prepare()
    assert dialog._job is not None, [r.state.text() for r in dialog.rows]
    qtbot.waitUntil(lambda: bool(seen) and dialog._job is None, timeout=10000)
    assert dialog.rows[0].amount.text() == "−18,70"
    assert not session.ledger.operations() and dialog.content.isEnabled()


def test_own_party_incoming_becomes_transfer_and_exclusion_does_not_write(qtbot, session):
    other = session.catalog.add_account("Вымышленный другой свой счёт", AccountKind.CASH)
    prefs = OcrPreferences(
        reference_year=2026,
        confirm_fields=False,
        review_before_save=False,
        transfer_parties=(TransferParty("Магазин", other.id),),
    )
    dialog, account = dialog_for(qtbot, session, prefs, source(("+71,4 RUB",)))
    assert dialog.rows[0].kind.currentData() == OperationKind.TRANSFER
    dialog.prepare()
    assert dialog._job is not None, [r.state.text() for r in dialog.rows]
    qtbot.waitUntil(lambda: dialog.saved_count == 1, timeout=10000)
    operation = session.ledger.operations()[0]
    assert operation.draft.kind == OperationKind.TRANSFER
    assert (
        operation.draft.account_id == other.id
        and operation.draft.destination_account_id == account.id
    )
    excluded, _ = dialog_for(
        qtbot,
        session,
        replace(prefs, transfer_parties=(TransferParty("Магазин", exclude=True),)),
        source(("+90,7 RUB",)),
    )
    assert not excluded.rows[0].include.isChecked() and not excluded.save.isEnabled()


def test_optional_flags_can_be_removed_and_one_operation_flag_is_absent(qtbot):
    from personal_finance.application.ocr_review import OcrReviewDefaults
    from personal_finance.presentation.ocr_preview import OcrPreviewDialog
    from tests.gui.test_ocr_preview import preview

    dialog = OcrPreviewDialog(preview(), review_defaults=OcrReviewDefaults(confirm_fields=False))
    qtbot.addWidget(dialog)
    assert dialog.checks == {} and not hasattr(dialog, "single") and dialog.finish.isEnabled()
    dialog.finish.click()
    assert dialog.reviewed is not None


def test_settings_dialog_saves_both_confirmation_switches(qtbot):
    dialog = OcrSettingsDialog(OcrPreferences(), [], allow_parties=False)
    qtbot.addWidget(dialog)
    dialog.confirm_fields.setChecked(False)
    dialog.review_before_save.setChecked(False)
    dialog._validate()
    assert not dialog.result_preferences.confirm_fields
    assert not dialog.result_preferences.review_before_save


def test_date_and_time_masks_accept_digits_without_typed_separators(qtbot):
    from personal_finance.application.ocr_review import OcrReviewDefaults
    from personal_finance.presentation.ocr_preview import OcrPreviewDialog
    from tests.gui.test_ocr_preview import preview

    dialog = OcrPreviewDialog(preview(), review_defaults=OcrReviewDefaults(confirm_fields=False))
    qtbot.addWidget(dialog)
    dialog.occurred_on.clear()
    qtbot.keyClicks(dialog.occurred_on, "24102026")
    dialog.local_time.clear()
    qtbot.keyClicks(dialog.local_time, "1435")
    assert dialog.occurred_on.text() == "24.10.2026" and dialog.local_time.text() == "14:35"
    assert dialog.finish.isEnabled()


def test_no_confirmation_mode_reports_exact_repeats_inline(qtbot, session, monkeypatch):
    first, account = dialog_for(qtbot, session)
    first.prepare()
    qtbot.waitUntil(lambda: first.saved_count == 2 and first._job is None, timeout=15000)
    second = OcrBatchDialog(
        source(),
        session.ocr_importing,
        session.catalog.accounts(),
        session.catalog.categories(),
        OcrPreferences(reference_year=2026, confirm_fields=False, review_before_save=False),
    )
    qtbot.addWidget(second)
    second.common_account.setCurrentIndex(second.common_account.findData(account.id))
    seen = []
    monkeypatch.setattr(BatchDecisionDialog, "exec", lambda _: seen.append(True))
    second.prepare()
    qtbot.waitUntil(lambda: second._job is None, timeout=15000)
    assert not seen and second.saved_count == 0
    assert len(session.ledger.operations()) == 2
    assert "Новых строк нет" in second.notice.text()
