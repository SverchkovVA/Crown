"""Фиксация и измерение отдельной синтетической выборки без пользовательского входа."""

import argparse
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import qVersion
from PySide6.QtWidgets import QApplication

from personal_finance.bootstrap.ocr_runtime import recognitions_for_directory
from personal_finance.domain.errors import FinanceError, ValidationError
from tools import ocr_holdout_samples as generator
from tools.benchmark_ocr import measure
from tools.ocr_benchmark_samples import Sample


def expected_cases(corpus: Sequence[Sample]) -> list[dict]:
    return [
        {
            "id": sample.id,
            "kind": sample.kind,
            "expected_blocked": sample.expected_blocked,
            "uses_year_default": sample.uses_year_default,
            "uses_currency_default": sample.uses_currency_default,
            "rows": [
                {
                    "amount_minor": row.amount_minor,
                    "currency": row.currency,
                    "direction": row.direction.value,
                    "occurred_on": row.occurred_on.isoformat(),
                    "local_time": row.local_time.isoformat() if row.local_time else None,
                }
                for row in sample.expected
            ],
        }
        for sample in corpus
    ]


def encode(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def prepare(directory: Path) -> tuple[tuple[Sample, ...], dict, str]:
    if directory.exists():
        raise ValidationError("Для выборки требуется новый каталог.")
    corpus = generator.samples()
    if (
        not corpus
        or len({s.id for s in corpus}) != len(corpus)
        or any(re.fullmatch(r"[a-z0-9-]+", sample.id) is None for sample in corpus)
    ):
        raise ValidationError("Некорректные идентификаторы синтетического набора.")
    expected = expected_cases(corpus)
    manifest = {
        "format": 1,
        "corpus": generator.CORPUS_ID,
        "generated_samples_only": True,
        "uses_real_financial_data": False,
        "financial_writes": False,
        "qt": qVersion(),
        "generator_sha256": hashlib.sha256(
            Path(generator.__file__).read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest(),
        "specifications": [asdict(spec) for spec in generator.SPECS],
        "fonts": {
            profile: asdict(generator.font_source(profile))
            for profile in sorted({spec.profile for spec in generator.SPECS})
        },
        "expected": expected,
        "expected_cases_sha256": hashlib.sha256(encode(expected)).hexdigest(),
        "images": [
            {
                "id": sample.id,
                "file": sample.id + ".png",
                "sha256": sample.image.sha256,
                "width": sample.image.width,
                "height": sample.image.height,
            }
            for sample in corpus
        ],
    }
    directory.mkdir(parents=True, exist_ok=False)
    for sample in corpus:
        with (directory / (sample.id + ".png")).open("xb") as output:
            output.write(sample.image.png)
    encoded = encode(manifest)
    with (directory / "manifest.json").open("xb") as output:
        output.write(encoded)
    return corpus, manifest, hashlib.sha256(encoded).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory", required=True, type=Path, help="Новый каталог макетов и отчёта"
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--ocr-runtime", type=Path, help="Проверенный локальный OCR-модуль")
    action.add_argument("--prepare-only", action="store_true", help="Только зафиксировать макеты")
    parser.add_argument(
        "--purpose",
        choices=("baseline", "regression"),
        default="baseline",
        help="Первый замер либо повтор после исправлений",
    )
    args = parser.parse_args(argv)
    if args.directory.exists():
        parser.error("Каталог уже существует; укажите новый.")
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    try:
        corpus, manifest, manifest_sha = prepare(args.directory)
        if args.prepare_only:
            print(f"Зафиксировано синтетических макетов: {len(corpus)}. OCR не выполнялся.")
            return 0
        # Создание движка и первый вызов OCR идут только после записи manifest и PNG.
        service = recognitions_for_directory(args.ocr_runtime)[0]
        result = measure(service, corpus)
        result.update(
            corpus=generator.CORPUS_ID,
            purpose=args.purpose,
            frozen_manifest_sha256=manifest_sha,
            expected_cases_sha256=manifest["expected_cases_sha256"],
        )
        result["notes"].extend(
            [
                "Новые макеты зафиксированы до вызова OCR.",
                "После подстройки по найденным ошибкам это регрессионная выборка.",
                "Это не измерение точности на банковских изображениях.",
            ]
        )
        with (args.directory / "report.json").open("xb") as output:
            output.write(encode(result))
    except (FinanceError, OSError):
        parser.exit(
            2, "Выборка не завершена. Проверьте шрифты, новый каталог и локальный OCR-модуль.\n"
        )
    print(
        f"Контрольных случаев: {result['case_count']}; совпали: {result['cases_passed']}; "
        f"пропуски: {result['missing_rows']}; лишние строки: {result['extra_rows']}."
    )
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
