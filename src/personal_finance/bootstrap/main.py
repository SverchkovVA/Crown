"""Точка входа. Параметры командной строки не принимают пароли и финансовые данные."""

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QTranslator
from PySide6.QtWidgets import QApplication

from personal_finance import __version__
from personal_finance.presentation.main_window import MainWindow


def main() -> int:
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
    options = parser.parse_args()
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
    if options.self_test:
        from personal_finance.bootstrap.self_test import self_test

        return self_test(app, options.self_test_report)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
