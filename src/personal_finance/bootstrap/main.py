"""Точка входа. Параметры командной строки не принимают пароли и финансовые данные."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QLibraryInfo, QTranslator
from PySide6.QtWidgets import QApplication, QMessageBox

from personal_finance import __version__
from personal_finance.application.ocr_cards import CardRecognition
from personal_finance.bootstrap.ocr_runtime import recognitions_for_directory, runtime_directory
from personal_finance.domain.errors import ValidationError
from personal_finance.infrastructure.ocr.process import OcrProcessError
from personal_finance.presentation.main_window import MainWindow


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Личные финансы — локальное приложение")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--self-test", action="store_true", help="Проверить сборку на временных данных"
    )
    parser.add_argument(
        "--self-test-report",
        type=Path,
        metavar="FILE",
        help="Сохранить безопасный JSON-отчёт самопроверки в новый файл",
    )
    parser.add_argument(
        "--ocr-runtime",
        type=Path,
        metavar="DIRECTORY",
        help="Каталог проверенного локального модуля распознавания",
    )
    options = parser.parse_args(argv)
    if options.self_test_report is not None and not options.self_test:
        parser.error("--self-test-report используется только вместе с --self-test")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("PersonalFinance")
    app.setApplicationDisplayName("Личные финансы")
    app.setOrganizationName("PersonalFinanceLocal")
    translator = QTranslator(app)
    if not translator.load(
        "qtbase_ru", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    ):
        if sys.stderr is not None:
            print("В поставке отсутствует русский перевод стандартных окон Qt.", file=sys.stderr)
        return 1
    app.installTranslator(translator)
    directory = runtime_directory(options.ocr_runtime)
    if options.self_test:
        from personal_finance.bootstrap.self_test import self_test

        return self_test(app, options.self_test_report, ocr_directory=directory)
    recognitions: tuple[CardRecognition, ...] = ()
    ocr_error = False
    if directory is not None:
        try:
            recognitions = recognitions_for_directory(directory)
        except (ValidationError, OcrProcessError):
            ocr_error = True
    window = MainWindow(ocr_recognitions=recognitions)
    window.show()
    if ocr_error:
        QMessageBox.warning(
            window,
            "Локальное распознавание недоступно",
            "Не удалось проверить модуль распознавания. "
            "Используйте полный проверенный каталог программы.",
        )
    exit_code = app.exec()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
