"""Публичная подготовка не перезаписывает файлы и отклоняет изменённые материалы."""

import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "crown_android_ocr_inputs", Path(__file__).parents[2] / "tools/prepare_android_ocr_probe.py"
)
assert _SPEC is not None and _SPEC.loader is not None
probe = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = probe
_SPEC.loader.exec_module(probe)


def test_complete_input_set_is_published_with_hashes_and_honest_limits(tmp_path: Path) -> None:
    content = b"synthetic-public-license"
    artifact = probe.Artifact(
        "licenses/test.txt",
        "https://raw.githubusercontent.com/test/license",
        hashlib.sha256(content).hexdigest(),
    )
    output = tmp_path / "complete"
    probe.prepare(output, artifacts=(artifact,), fetch=lambda _: content)
    assert (output / artifact.name).read_bytes() == content
    manifest = json.loads((output / "candidate-inputs.json").read_text(encoding="utf-8"))
    assert manifest["files"][0]["sha256"] == artifact.sha256
    assert not manifest["production_integration"]
    assert not manifest["full_license_audit_complete"]
    assert not list(tmp_path.glob("crown-ocr-inputs-*"))


def test_changed_bytes_and_existing_directory_are_never_published(tmp_path: Path) -> None:
    artifact = probe.Artifact(
        "license.txt", "https://raw.githubusercontent.com/test/license", "0" * 64
    )
    output = tmp_path / "rejected"
    with pytest.raises(ValueError, match="Контрольная сумма"):
        probe.prepare(output, artifacts=(artifact,), fetch=lambda _: b"changed")
    assert not output.exists()
    assert not list(tmp_path.glob("crown-ocr-inputs-*"))
    output.mkdir()
    original = output / "keep.txt"
    original.write_text("untouched", encoding="utf-8")
    with pytest.raises(ValueError, match="уже существует"):
        probe.prepare(
            output, artifacts=(artifact,), fetch=lambda _: pytest.fail("Не должно быть загрузки")
        )
    assert original.read_text(encoding="utf-8") == "untouched"


def test_stream_limit_and_unsafe_names_are_rejected() -> None:
    assert probe.read_bounded(io.BytesIO(b"abcd"), 4) == b"abcd"
    with pytest.raises(ValueError, match="размер"):
        probe.read_bounded(io.BytesIO(b"abcde"), 4)
    for name in ("../key.json", "/absolute", "C:/outside", "a\\b"):
        with pytest.raises(ValueError, match="имя"):
            probe.Artifact(name, "https://raw.githubusercontent.com/test/license", "0" * 64)
