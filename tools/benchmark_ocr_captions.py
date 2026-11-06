"""Регрессия соседних подписей итогов на вымышленных изображениях."""

import argparse
import hashlib
from datetime import date
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontInfo, QImage, QPainter
from PySide6.QtWidgets import QApplication

from personal_finance.bootstrap.ocr_runtime import recognitions_for_directory
from personal_finance.domain.errors import FinanceError
from personal_finance.domain.ocr_cards import CashDirection
from personal_finance.infrastructure.ocr.images import normalize_ocr_image
from tools.benchmark_ocr import measure
from tools.benchmark_ocr_holdout import encode, expected_cases
from tools.ocr_benchmark_samples import ExpectedRow, Sample
from tools.ocr_holdout_samples import font_source

CORPUS_ID = "synthetic-summary-layout-v1"


def samples() -> tuple[Sample, ...]:
    corpus = []
    for dark in (False, True):
        for kind in ("control-before", "control-after", "history", "card"):
            pixels = QImage(900, 1500, QImage.Format.Format_RGB32)
            pixels.fill(QColor("#131821" if dark else "#ffffff"))
            painter = QPainter(pixels)
            font = QFont(font_source("sans").family)
            font.setPixelSize(34)
            painter.setFont(font)
            painter.setPen(QColor("#f4f6f8" if dark else "#20262e"))
            expected = ()
            try:
                title = "Детали операции" if kind == "card" else "История операций"
                painter.drawText(40, 140, title)
                painter.drawText(40, 240, "27.10.2026")
                if kind.startswith("control"):
                    for index, (label, amount) in enumerate(
                        (("Траты", "−5 009,00 RUB"), ("Доходы", "+5 600,00 RUB"))
                    ):
                        first, second = (
                            (label, amount) if kind.endswith("before") else (amount, label)
                        )
                        painter.drawText(40, 420 + index * 260, first)
                        painter.drawText(40, 470 + index * 260, second)
                else:
                    if kind == "history":
                        painter.drawText(40, 370, "−5 009,00 RUB")
                        painter.drawText(40, 420, "Траты")
                    painter.drawText(
                        40,
                        640,
                        "Получатель: Учебный магазин" if kind == "card" else "Учебный магазин",
                    )
                    painter.drawText(40, 690, "−73,15 RUB")
                    painter.drawText(40, 740, "Расходы")
                    expected = (ExpectedRow(7315, CashDirection.OUT, date(2026, 10, 27)),)
                    if kind == "history":
                        painter.drawText(40, 980, "Учебное поступление")
                        painter.drawText(40, 1030, "+149,07 RUB")
                        painter.drawText(40, 1080, "Доходы")
                        expected += (ExpectedRow(14907, CashDirection.IN, date(2026, 10, 27)),)
            finally:
                painter.end()
            corpus.append(
                Sample(
                    kind + ("-dark" if dark else "-light"),
                    "card" if kind == "card" else "history",
                    normalize_ocr_image(pixels),
                    expected,
                    QFontInfo(font).family(),
                    expected_blocked=kind.startswith("control"),
                )
            )
    return tuple(corpus)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True, help="Новый каталог замера")
    parser.add_argument("--ocr-runtime", type=Path, required=True)
    args = parser.parse_args()
    if args.directory.exists():
        parser.error("Каталог уже существует; укажите новый.")
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    try:
        corpus = samples()
        manifest = {
            "corpus": CORPUS_ID,
            "purpose": "regression",
            "generated_samples_only": True,
            "uses_real_financial_data": False,
            "financial_writes": False,
            "font": font_source("sans").__dict__,
            "generator_sha256": hashlib.sha256(
                Path(__file__).read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest(),
            "expected": expected_cases(corpus),
            "images": [{"id": s.id, "sha256": s.image.sha256} for s in corpus],
        }
        args.directory.mkdir(parents=True, exist_ok=False)
        for sample in corpus:
            with (args.directory / (sample.id + ".png")).open("xb") as output:
                output.write(sample.image.png)
        manifest_bytes = encode(manifest)
        with (args.directory / "manifest.json").open("xb") as output:
            output.write(manifest_bytes)
        result = measure(recognitions_for_directory(args.ocr_runtime)[0], corpus)
        result.update(
            corpus=CORPUS_ID,
            purpose="regression",
            frozen_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        )
        with (args.directory / "report.json").open("xb") as output:
            output.write(encode(result))
    except (FinanceError, OSError):
        parser.exit(2, "Замер не завершён. Проверьте локальные шрифты, каталог и OCR-модуль.\n")
    print(
        f"Синтетических макетов: {result['case_count']}; совпали: {result['cases_passed']}; "
        f"пропуски: {result['missing_rows']}; лишние строки: {result['extra_rows']}."
    )
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
