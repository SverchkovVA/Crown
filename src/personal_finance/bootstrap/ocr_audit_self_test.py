"""Финансовый OCR-контракт на вымышленных словах без настоящего движка."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from personal_finance.application.vault import VaultSession
from personal_finance.domain.imports import account_movement
from personal_finance.domain.ledger import Operation
from personal_finance.domain.ocr import OcrBox, OcrDocument, OcrEngineInfo, OcrWord
from personal_finance.domain.ocr_cards import CardCorrections, CashDirection, review_card
from personal_finance.domain.ocr_imports import (
    OcrEvidence,
    OcrImportResult,
    OcrImportStatus,
    OcrSelection,
    canonical,
    make_ocr_evidence,
)
from personal_finance.infrastructure.ocr.t_bank import TBankCardAdapter


def check_audited_ocr_link(
    session: VaultSession, operation: Operation, account_id: str, command_id: str
) -> tuple[OcrSelection, OcrImportResult]:
    service = session.ocr_importing
    if service is None or session.ledger is None:
        raise RuntimeError("OCR-подтверждение недоступно в новой книге.")
    movement = account_movement(operation.draft, account_id)
    minor = abs(movement)
    amount = f"{minor // 100},{minor % 100:02d}"
    document = OcrDocument(
        "a" * 64,
        500,
        200,
        OcrEngineInfo(
            "tesseract", "self-test-without-engine", (("rus", "1" * 64), ("eng", "2" * 64))
        ),
        (
            OcrWord("Операция выполнена", OcrBox(20, 5, 450, 30), Decimal(95), (1, 1, 1), 1),
            OcrWord(
                ("+" if movement > 0 else "-") + amount + " RUB",
                OcrBox(250, 55, 200, 30),
                Decimal(95),
                (1, 1, 2),
                1,
            ),
            OcrWord(
                operation.draft.occurred_on.strftime("%d.%m.%Y"),
                OcrBox(20, 105, 200, 30),
                Decimal(95),
                (1, 1, 3),
                1,
            ),
        ),
    )
    checked = review_card(
        TBankCardAdapter().parse(document),
        CardCorrections(
            amount,
            "RUB",
            CashDirection.IN if movement > 0 else CashDirection.OUT,
            operation.draft.occurred_on.strftime("%d.%m.%Y"),
        ),
        confirmed_fields=frozenset({"amount", "currency", "direction", "occurred_on"}),
        single_completed_operation=True,
    )
    selection = OcrSelection(
        make_ocr_evidence("b" * 64, "Т-Банк", checked),
        account_id,
        operation.draft,
        existing_operation_id=operation.id,
        expected_revision_id=operation.revision_id,
    )
    before = session.ledger.operations(include_deleted=True)
    if service.preview(selection).status != OcrImportStatus.POSSIBLE:
        raise RuntimeError("Не найдено движение для связи OCR.")
    result = service.confirm(selection, command_id=command_id)
    if (
        not result.linked
        or result.operation != operation
        or service.confirm(selection, command_id=command_id) != result
    ):
        raise RuntimeError("Квитанция OCR не сохраняет связь и повтор команды.")
    if (
        service.preview(selection).status != OcrImportStatus.DUPLICATE
        or session.ledger.operations(include_deleted=True) != before
    ):
        raise RuntimeError("OCR-связь изменила финансовые операции или не защищена от повтора.")
    session.check_integrity()
    return selection, result


def check_ocr_workflow(
    session: VaultSession, selection: OcrSelection, command_id: str
) -> tuple[OcrSelection, tuple[OcrImportResult, ...]]:
    from PySide6.QtGui import QImageReader

    from personal_finance.domain.ocr_settings import OcrPreferences, TransferParty
    from personal_finance.presentation import main_window

    service, settings = session.ocr_importing, session.ocr_settings
    if service is None or not service.supports_batches or settings is None:
        raise RuntimeError("Настройки и пакетный OCR недоступны в новой книге.")
    preferences = OcrPreferences(
        confirm_fields=False,
        review_before_save=False,
        transfer_parties=(TransferParty("Вымышленный отправитель", selection.account_id),),
    )
    settings.save(preferences, None)
    if settings.load()[0] != preferences:
        raise RuntimeError("Параметры OCR не сохранились.")
    payload = selection.evidence.data
    payload["image_sha256"] = "7" * 64
    payload["file_sha256"] = "8" * 64
    batch_selection = replace(selection, evidence=OcrEvidence(canonical(payload)))
    before = session.ledger.operations() if session.ledger is not None else []
    result = service.confirm_many((batch_selection,), command_id=command_id)
    if service.confirm_many((batch_selection,), command_id=command_id) != result:
        raise RuntimeError("Не сохранился повтор пакетной команды OCR.")
    if session.ledger is None or session.ledger.operations() != before:
        raise RuntimeError("Связь пакета изменила финансовые факты.")
    folder = Path(main_window.__file__).with_name("icons")
    for name in (
        "check.svg",
        "check-disabled.svg",
        "radio.svg",
        "radio-disabled.svg",
        "chevron-down.svg",
        "chevron-up.svg",
        "chevron-disabled.svg",
    ):
        if not (folder / name).is_file() or QImageReader(str(folder / name)).read().isNull():
            raise RuntimeError("В сборке отсутствует индикатор интерфейса.")
    session.check_integrity()
    return batch_selection, result
