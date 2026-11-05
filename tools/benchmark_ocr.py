"""Оценка локального OCR только на встроенных синтетических изображениях."""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from time import perf_counter_ns

from PySide6.QtCore import qVersion
from PySide6.QtWidgets import QApplication

from personal_finance import __version__
from personal_finance.application.ocr_cards import CardRecognition
from personal_finance.application.ocr_review import OcrReviewDefaults, suggest_corrections
from personal_finance.bootstrap.ocr_runtime import recognitions_for_directory
from personal_finance.domain.errors import FinanceError
from personal_finance.domain.models import Money
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.domain.ocr_cards import OcrCardDraft
from tools.ocr_benchmark_samples import Sample, samples

FIELDS = ("amount", "currency", "direction", "date", "time")


def evaluate(sample: Sample, cards: tuple[OcrCardDraft, ...], blocked: bool) -> dict:
    checks = []
    raw_known = {name: 0 for name in FIELDS}
    raw_exact = {name: 0 for name in FIELDS}
    fields = {name: {"required": 0, "exact": 0} for name in FIELDS}
    defaults = OcrReviewDefaults("RUB", 2026, False)
    for index, expected in enumerate(sample.expected):
        actual = cards[index] if index < len(cards) else None
        proposal = suggest_corrections(actual, defaults) if actual is not None else None
        try:
            amount = Money.parse(proposal.amount).minor if proposal is not None else None
        except FinanceError:
            amount = None
        observed = {
            "amount": amount,
            "currency": proposal.currency if proposal is not None else None,
            "direction": proposal.direction if proposal is not None else None,
            "date": proposal.occurred_on if proposal is not None else None,
            "time": (proposal.local_time or None) if proposal is not None else None,
        }
        wanted = {
            "amount": expected.amount_minor,
            "currency": expected.currency,
            "direction": expected.direction,
            "date": expected.occurred_on.strftime("%d.%m.%Y"),
            "time": expected.local_time.strftime("%H:%M")
            if expected.local_time is not None
            else None,
        }
        row = {}
        for name in FIELDS:
            required = wanted[name] is not None
            exact = observed[name] == wanted[name]
            fields[name]["required"] += int(required)
            fields[name]["exact"] += int(required and exact)
            row[name + "_exact"] = exact
        if actual is not None:
            for name, attribute in (
                ("amount", "amount"),
                ("currency", "currency"),
                ("direction", "direction"),
                ("date", "occurred_on"),
                ("time", "local_time"),
            ):
                raw_value = getattr(actual, attribute).value
                raw_known[name] += int(raw_value is not None)
                raw_wanted = (
                    expected.occurred_on
                    if name == "date"
                    else (expected.local_time if name == "time" else wanted[name])
                )
                raw_exact[name] += int(raw_wanted is not None and raw_value == raw_wanted)
        checks.append(row)
    detected = sum(c.amount.value is not None for c in cards)
    usable = sum(c.amount.value is not None and not c.blocked_reason for c in cards)
    if sample.expected_blocked:
        passed = blocked and detected == 0
    else:
        passed = (
            not blocked
            and len(cards) == len(sample.expected)
            and all(all(row.values()) for row in checks)
            and all(not card.blocked_reason for card in cards)
        )
    return {
        "id": sample.id,
        "kind": sample.kind,
        "image_sha256": sample.image.sha256,
        "image_size": [sample.image.width, sample.image.height],
        "status": "passed" if passed else "needs_review",
        "expected_rows": len(sample.expected),
        "actual_rows": detected,
        "draft_count": len(cards),
        "missing_rows": max(0, len(sample.expected) - detected),
        "extra_rows": max(0, detected - len(sample.expected))
        if not sample.expected_blocked
        else detected,
        "blocked": blocked,
        "usable_money_candidates": usable,
        "proposal_fields": fields,
        "source_known_fields": raw_known,
        "source_exact_fields": raw_exact,
        "expected_year_default": sample.uses_year_default,
        "expected_currency_default": sample.uses_currency_default,
        "row_checks": checks,
        "resolved_font": sample.font,
    }


def measure(
    service: CardRecognition,
    corpus: Sequence[Sample],
    clock: Callable[[], int] = perf_counter_ns,
) -> dict:
    records = []
    for sample in corpus:
        started = clock()
        loaded = LoadedOcrImage(sample.image.sha256, sample.image)
        try:
            if sample.kind == "card":
                result = service.preview_image(loaded)
                cards, blocked = (result.card,), bool(result.card.blocked_reason)
            else:
                history = service.history_image(loaded).history
                cards, blocked = history.cards, bool(history.blocked_reason)
            record = evaluate(sample, cards, blocked)
            engine = cards[0].document.engine if cards else history.document.engine
            record["engine_name"] = engine.name
            record["engine_version"] = engine.version
            record["model_sha256"] = dict(engine.model_sha256)
        except FinanceError as error:
            record = {
                "id": sample.id,
                "kind": sample.kind,
                "status": "engine_error",
                "error_type": type(error).__name__,
                "expected_rows": len(sample.expected),
                "actual_rows": 0,
                "missing_rows": len(sample.expected),
                "extra_rows": 0,
            }
        record["duration_ms"] = max(0, (clock() - started) // 1_000_000)
        records.append(record)
    return {
        "format": 1,
        "application": __version__,
        "corpus": "synthetic-layout-v1",
        "generated_samples_only": True,
        "uses_real_financial_data": False,
        "financial_writes": False,
        "qt": qVersion(),
        "platform": sys.platform,
        "status": "passed" if all(r["status"] == "passed" for r in records) else "needs_review",
        "case_count": len(records),
        "cases_passed": sum(r["status"] == "passed" for r in records),
        "missing_rows": sum(r["missing_rows"] for r in records),
        "extra_rows": sum(r["extra_rows"] for r in records),
        "notes": [
            "Только синтетические макеты. Банковская точность не измерена.",
            "Поля предложения и поля, найденные в источнике, подсчитаны раздельно.",
            "Дата/валюта по настройке не считаются найденными в источнике.",
            "Строки сравниваются по порядку; пропуск не скрывается подбором совпадения.",
        ],
        "cases": records,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr-runtime", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--images", type=Path, help="Новый каталог сгенерированных PNG")
    args = parser.parse_args(argv)
    if args.report.exists():
        parser.error("Файл отчёта уже существует; укажите новое имя.")
    if args.report.suffix.lower() != ".json":
        parser.error("Для отчёта укажите новый файл .json.")
    if args.images is not None and args.images.exists():
        parser.error("Каталог изображений уже существует; укажите новый.")
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    corpus = samples()
    if args.images is not None:
        args.images.mkdir(parents=True, exist_ok=False)
        for sample in corpus:
            with (args.images / (sample.id + ".png")).open("xb") as output:
                output.write(sample.image.png)
    result = measure(recognitions_for_directory(args.ocr_runtime)[0], corpus)
    with args.report.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(
        f"Синтетических случаев: {result['case_count']}; совпали: {result['cases_passed']}; "
        f"пропуски: {result['missing_rows']}; лишние строки: {result['extra_rows']}. "
        "Финансовая запись не выполнялась."
    )
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
