"""Опытный запуск предпросмотра с явно заданным локальным runtime."""

import argparse
import json
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QLibraryInfo, QTranslator
from PySide6.QtWidgets import QApplication

from personal_finance.application.ocr import Ocr
from personal_finance.application.ocr_cards import CardRecognition
from personal_finance.domain.ocr import OcrEngineInfo
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter, GenericHistoryAdapter
from personal_finance.infrastructure.ocr.images import load_ocr_image
from personal_finance.infrastructure.ocr.t_bank import TBankCardAdapter
from personal_finance.infrastructure.ocr.t_bank_history import TBankHistoryAdapter
from personal_finance.infrastructure.ocr.tesseract import TesseractEngine, TesseractRuntime
from personal_finance.infrastructure.ocr.vtb import VtbCardAdapter
from personal_finance.infrastructure.ocr.vtb_history import VtbHistoryAdapter
from personal_finance.presentation.main_window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description="Опытная локальная проверка и подтверждение OCR")
    parser.add_argument(
        "--runtime", type=Path, required=True, help="Каталог проверенного OCR-кандидата"
    )
    options = parser.parse_args()
    # Доверенные хеши из репозитория, а не изменяемый манифест рядом с внешним EXE.
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "packaging/ocr/runtime-candidate.json").read_text(
            encoding="utf-8"
        )
    )
    engine = OcrEngineInfo(
        "tesseract", manifest["version"], tuple(tuple(v) for v in manifest["models"])
    )
    runtime = TesseractRuntime(
        options.runtime.resolve(), engine, tuple(tuple(v) for v in manifest["file_sha256"])
    )
    app = QApplication(sys.argv[:1])
    app.setApplicationName("PersonalFinance")
    app.setApplicationDisplayName("Личные финансы")
    translator = QTranslator(app)
    if not translator.load(
        "qtbase_ru", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    ):
        return 1
    app.installTranslator(translator)
    ocr = Ocr(TesseractEngine(runtime))
    fallback = Ocr(TesseractEngine(runtime, page_segmentation=11))
    window = MainWindow(
        ocr_recognitions=(
            CardRecognition(
                ocr,
                load_ocr_image,
                GenericCardAdapter(),
                history_adapter=GenericHistoryAdapter(),
                bank_name=UNIVERSAL_PROFILE,
                fallback_ocr=fallback,
                auto_history_hint=GenericHistoryAdapter.has_heading,
            ),
            CardRecognition(
                ocr,
                load_ocr_image,
                TBankCardAdapter(),
                history_adapter=TBankHistoryAdapter(),
                fallback_ocr=fallback,
            ),
            CardRecognition(
                ocr,
                load_ocr_image,
                VtbCardAdapter(),
                history_adapter=VtbHistoryAdapter(),
                bank_name="ВТБ",
                fallback_ocr=fallback,
            ),
        )
    )
    window.show()
    result = app.exec()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
