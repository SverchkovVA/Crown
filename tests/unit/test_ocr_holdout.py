"""Контрольная выборка фиксируется до движка и не читает банковские файлы."""

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from personal_finance.domain.errors import ValidationError
from tools import benchmark_ocr_holdout as benchmark
from tools import ocr_holdout_samples as generator


def test_expected_rows_are_frozen_and_images_repeat(qapp):
    expected_file = Path(__file__).parents[1] / "fixtures" / "ocr_holdout_v1_expected.json"
    frozen = json.loads(expected_file.read_text(encoding="utf-8"))
    first, second = generator.samples(), generator.samples()
    assert len(first) == 14 and len({sample.id for sample in first}) == 14
    assert benchmark.expected_cases(first) == frozen["expected"]
    assert [s.image.sha256 for s in first] == [s.image.sha256 for s in second]
    assert sum(len(s.expected) for s in first) == 41
    assert sum(s.expected_blocked for s in first) == 3
    assert (
        frozen["expected_cases_sha256"]
        == hashlib.sha256(benchmark.encode(benchmark.expected_cases(first))).hexdigest()
    )
    assert frozen["specifications"] == [asdict(spec) for spec in generator.SPECS]
    assert len({sample.font for sample in first}) == 3


def test_prepare_is_bitwise_repeatable_in_one_environment(qapp, tmp_path):
    first, manifest_a, digest_a = benchmark.prepare(tmp_path / "first")
    second, manifest_b, digest_b = benchmark.prepare(tmp_path / "second")
    assert manifest_a == manifest_b and digest_a == digest_b
    assert [s.image.sha256 for s in first] == [s.image.sha256 for s in second]
    assert manifest_a["uses_real_financial_data"] is False
    assert all(len(font["sha256"]) == 64 for font in manifest_a["fonts"].values())


def test_prepare_only_never_constructs_a_recognizer(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        benchmark,
        "recognitions_for_directory",
        lambda path: pytest.fail("OCR вызван при prepare-only"),
    )
    directory = tmp_path / "prepared"
    assert benchmark.main(["--prepare-only", "--directory", str(directory)]) == 0
    assert (directory / "manifest.json").is_file()
    assert len(list(directory.glob("*.png"))) == 14
    assert not (directory / "report.json").exists()


def test_manifest_and_every_png_exist_before_runtime_is_created(qapp, tmp_path, monkeypatch):
    directory = tmp_path / "measured"
    marker = object()
    calls = []

    def factory(path):
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        assert len(manifest["images"]) == 14
        for item in manifest["images"]:
            assert (
                hashlib.sha256((directory / item["file"]).read_bytes()).hexdigest()
                == item["sha256"]
            )
        assert not (directory / "report.json").exists()
        calls.append("factory")
        return (marker,)

    def measure(service, corpus):
        assert service is marker and calls == ["factory"] and len(corpus) == 14
        calls.append("measure")
        return dict(
            case_count=14, cases_passed=14, missing_rows=0, extra_rows=0, status="passed", notes=[]
        )

    monkeypatch.setattr(benchmark, "recognitions_for_directory", factory)
    monkeypatch.setattr(benchmark, "measure", measure)
    assert benchmark.main(["--ocr-runtime", str(tmp_path), "--directory", str(directory)]) == 0
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert calls == ["factory", "measure"]
    assert report["corpus"] == generator.CORPUS_ID and report["purpose"] == "baseline"
    assert (
        report["frozen_manifest_sha256"]
        == hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()
    )


def test_existing_directory_is_not_changed(qapp, tmp_path):
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("оставить", encoding="utf-8")
    with pytest.raises(SystemExit) as result:
        benchmark.main(["--prepare-only", "--directory", str(tmp_path)])
    assert result.value.code == 2
    assert sentinel.read_text(encoding="utf-8") == "оставить"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep.txt"]


def test_invalid_sample_id_cannot_write_outside_output(qapp, tmp_path, monkeypatch):
    sample = replace(generator.samples()[0], id="../outside")
    monkeypatch.setattr(generator, "samples", lambda: (sample,))
    with pytest.raises(ValidationError, match="идентификаторы"):
        benchmark.prepare(tmp_path / "new")
    assert list(tmp_path.iterdir()) == []


def test_missing_font_is_an_error_not_an_empty_glyph_measurement(qapp, monkeypatch):
    generator.font_source.cache_clear()
    monkeypatch.setattr(generator, "_candidates", lambda profile: ())
    try:
        with pytest.raises(ValidationError, match="шрифта"):
            generator.font_source("sans")
    finally:
        generator.font_source.cache_clear()


def test_runtime_error_does_not_publish_exception_text(qapp, tmp_path, monkeypatch, capsys):
    def fail(path):
        raise ValidationError("DO_NOT_PUBLISH_EXCEPTION_TEXT")

    monkeypatch.setattr(benchmark, "recognitions_for_directory", fail)
    directory = tmp_path / "failed"
    with pytest.raises(SystemExit) as result:
        benchmark.main(["--ocr-runtime", str(tmp_path), "--directory", str(directory)])
    captured = capsys.readouterr()
    assert result.value.code == 2
    assert "DO_NOT_PUBLISH_EXCEPTION_TEXT" not in captured.out + captured.err
    assert (directory / "manifest.json").exists()
    assert not (directory / "report.json").exists()
