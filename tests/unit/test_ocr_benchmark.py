"""Метрики синтетической матрицы проверяются отдельно от настоящего OCR-движка."""

import json
from dataclasses import replace
from datetime import date, time

import pytest

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr_cards import CashDirection
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter
from tests.ocr_fixtures import card, document, image
from tools import benchmark_ocr as benchmark
from tools import ocr_benchmark_samples as corpus
from tools.ocr_benchmark_samples import ExpectedRow, Sample


def sample(expected=None, *, blocked=False):
    return Sample(
        "synthetic-unit",
        "card",
        image(),
        expected
        if expected is not None
        else (ExpectedRow(123456, CashDirection.OUT, date(2026, 10, 27), time(14, 35)),),
        "unit-font",
        expected_blocked=blocked,
    )


def test_complete_sample_is_exact_without_financial_writes():
    result = benchmark.evaluate(sample(), (card(),), False)
    assert result["status"] == "passed" and result["missing_rows"] == result["extra_rows"] == 0
    assert all(
        metric == {"required": 1, "exact": 1} for metric in result["proposal_fields"].values()
    )


def test_proposed_defaults_are_not_counted_as_recognized_fields():
    value = GenericCardAdapter().parse(document(("Детали операции", "—12,34 Р", "23 октября")))
    expected = (ExpectedRow(1234, CashDirection.OUT, date(2026, 10, 23)),)
    result = benchmark.evaluate(sample(expected), (value,), False)
    assert result["status"] == "passed"
    assert result["proposal_fields"]["currency"]["exact"] == 1
    assert result["proposal_fields"]["date"]["exact"] == 1
    assert result["source_known_fields"]["currency"] == 0
    assert result["source_known_fields"]["date"] == 0
    assert result["source_known_fields"]["direction"] == 0
    assert result["source_exact_fields"]["currency"] == 0
    assert result["source_exact_fields"]["date"] == 0
    assert result["source_exact_fields"]["direction"] == 0


def test_missing_or_extra_row_is_not_hidden_by_matching_another_position():
    expected = sample().expected * 2
    missing = benchmark.evaluate(sample(expected), (card(),), False)
    assert missing["status"] == "needs_review" and missing["missing_rows"] == 1
    extra = benchmark.evaluate(sample(), (card(), card()), False)
    assert extra["status"] == "needs_review" and extra["extra_rows"] == 1


@pytest.mark.parametrize("blocked_reason", ["", "Недостаточно полей"])
def test_blocked_control_cannot_hide_any_money_candidate(blocked_reason):
    candidate = replace(card(), blocked_reason=blocked_reason)
    result = benchmark.evaluate(sample((), blocked=True), (candidate,), True)
    assert result["status"] == "needs_review" and result["extra_rows"] == 1


def test_engine_failure_report_does_not_include_exception_text():
    class Service:
        def preview_image(self, source):
            raise ValidationError("DO_NOT_PUBLISH_RAW_OCR_TEXT")

    result = benchmark.measure(Service(), (sample(),), clock=lambda: 0)
    assert result["status"] == "needs_review"
    assert result["financial_writes"] is False and result["generated_samples_only"] is True
    assert result["cases"][0]["error_type"] == "ValidationError"
    assert "DO_NOT_PUBLISH_RAW_OCR_TEXT" not in json.dumps(result)


def test_existing_report_is_never_replaced(tmp_path):
    target = tmp_path / "existing.json"
    target.write_bytes(b"keep")
    with pytest.raises(SystemExit) as result:
        benchmark.main(["--ocr-runtime", str(tmp_path), "--report", str(target)])
    assert result.value.code == 2 and target.read_bytes() == b"keep"


def test_generated_images_are_repeatable_and_have_available_glyphs(qapp):
    first = corpus.samples()
    second = corpus.samples()
    assert len(first) == 12 and len({item.id for item in first}) == 12
    assert [item.image.sha256 for item in first] == [item.image.sha256 for item in second]
    assert all((item.image.width, item.image.height) == (1080, 1800) for item in first)
    assert len(next(item for item in first if item.id == "history-dark").expected) == 6
    assert corpus._family()


def test_missing_font_fails_instead_of_measuring_tofu_boxes(qapp, monkeypatch):
    class Metrics:
        def inFontUcs4(self, char):
            return False

    corpus._family.cache_clear()
    monkeypatch.setattr(corpus, "QFontMetrics", lambda font: Metrics())
    monkeypatch.setattr(corpus.QFontDatabase, "addApplicationFont", lambda path: -1)
    monkeypatch.setattr(corpus.QFontDatabase, "applicationFontFamilies", lambda number: [])
    try:
        with pytest.raises(ValidationError, match="шрифт"):
            corpus._family()
    finally:
        corpus._family.cache_clear()
