"""Точка входа. Параметры командной строки не принимают пароли и финансовые данные."""

import argparse
import sys

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
    options = parser.parse_args()
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

        return self_test(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
