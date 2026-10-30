import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from personal_finance.application.ocr import Ocr
from personal_finance.application.ocr_cards import CardRecognition
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.domain.ocr_cards import CashDirection
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter
from personal_finance.presentation.ocr_preview import OptionalMaskedEdit
from tests.ocr_fixtures import image
from tests.unit.test_generic_ocr import document
from tests.unit.test_ocr_card_fallback import Engine


@pytest.mark.parametrize("tail", ["₽", "Р", "P"])
def test_primary_operation_ignores_account_balance_and_phone(tail):
    card = GenericCardAdapter().parse(
        document(
            (
                "16 октября, 14:34",
                "Переводы",
                "-74 " + tail,
                "Перевод со счёта",
                "Основной",
                "2 468,13 ₽",
                "Реквизиты",
                "Номер телефона",
                "+7 000 000-00-00",
            )
        )
    )
    assert not card.blocked_reason
    assert card.amount.value == 7400 and card.direction.value == CashDirection.OUT
    assert card.adapter == "generic-card-v5"


def test_missing_primary_never_uses_account_balance_or_phone():
    card = GenericCardAdapter().parse(
        document(
            (
                "16 октября, 14:34",
                "Переводы",
                "Перевод со счета Справка",
                "Основной",
                "2 468,13 ₽",
                "Номер телефона",
                "+7 000 000-00-00",
            )
        )
    )
    assert card.amount.value is None and GenericCardAdapter.can_retry(card)


def test_universal_missing_primary_retries_and_keeps_one_complete_document():
    before = document(("Перевод", "16 октября", "Перевод со счета", "Основной", "2 468,13 ₽"))
    after = document(("Перевод", "16 октября", "-74 ₽", "Перевод со счета", "2 468,13 ₽"))
    first, second = Engine(before), Engine(after)
    loaded = LoadedOcrImage("3" * 64, image())
    service = CardRecognition(
        Ocr(first),
        lambda _: loaded,
        GenericCardAdapter(),
        bank_name=UNIVERSAL_PROFILE,
        fallback_ocr=Ocr(second),
        retry_missing_card=GenericCardAdapter.can_retry,
    )
    result = service.preview(Path("unused.png"))
    assert not result.card.blocked_reason and result.card.amount.value == 7400
    assert second.calls == 1 and result.card.document == second.result.__class__(
        loaded.image.sha256,
        loaded.image.width,
        loaded.image.height,
        after.engine,
        after.words,
    )


@pytest.mark.parametrize("marker", ["В обработке", "Отменена", "Комиссия: 1 ₽"])
def test_retry_does_not_remove_explicit_status_or_fee(marker):
    value = GenericCardAdapter().parse(
        document(
            (
                "Перевод",
                "Перевод со счёта",
                "Основной",
                "2 468,13 ₽",
                marker,
            )
        )
    )
    assert value.blocked_reason and not GenericCardAdapter.can_retry(value)


@pytest.mark.parametrize("mask,value", [("00.00.0000;_", "16.10.2026"), ("00:00;_", "14:34")])
def test_date_time_click_and_double_click_begin_at_first_digit(qtbot, mask, value):
    field = OptionalMaskedEdit(mask)
    qtbot.addWidget(field)
    field.setText(value)
    field.resize(300, 40)
    field.show()
    QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=QPoint(180, 20))
    assert field.cursorPosition() == 0
    QTest.mouseDClick(field, Qt.MouseButton.LeftButton, pos=QPoint(180, 20))
    assert field.cursorPosition() == 0 and field.text() == value


def test_row_focus_regression_runs_in_separate_process_without_stack_overflow():
    root = Path(__file__).resolve().parents[2]
    code = """
import os, warnings
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from personal_finance.presentation.ocr_batch import OcrBatchDialog
from personal_finance.domain.ocr_settings import OcrPreferences
from tests.gui.test_ocr_batch_gui import source
warnings.filterwarnings('ignore', category=DeprecationWarning)
app=QApplication([])
dialog=OcrBatchDialog(source(),None,[],[],OcrPreferences(reference_year=2026))
dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen,True)
dialog.show();app.setActiveWindow(dialog);app.processEvents()
for n in range(30):
    index=n%2
    field=dialog.rows[index].occurred_on
    QTest.mouseClick(field,Qt.MouseButton.LeftButton,pos=QPoint(60,10))
    app.processEvents()
    assert (dialog.table.currentRow(),dialog.table.currentColumn())==(index,1)
    assert field.cursorPosition()==0
dialog.close()
print('row-focus-passed')
"""
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", code],
        cwd=root,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert result.returncode == 0, result.stderr[-2500:]
    assert "row-focus-passed" in result.stdout
