"""Совместимый запуск локального OCR через общую точку входа приложения."""

import argparse
from pathlib import Path

from personal_finance.bootstrap.main import main as application_main


def main() -> int:
    parser = argparse.ArgumentParser(description="Опытный запуск локального OCR")
    parser.add_argument(
        "--runtime", type=Path, required=True, help="Каталог проверенного модуля OCR"
    )
    options = parser.parse_args()
    return application_main(["--ocr-runtime", str(options.runtime)])


if __name__ == "__main__":
    raise SystemExit(main())
