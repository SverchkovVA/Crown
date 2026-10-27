import hashlib
import os
from dataclasses import replace

import pytest

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import OcrEngineInfo
from personal_finance.infrastructure.ocr.process import OcrProcessError
from personal_finance.infrastructure.ocr.tesseract import TesseractRuntime


def runtime(tmp_path):
    entries = []
    for name, data in (
        ("tesseract.exe" if os.name == "nt" else "tesseract", b"synthetic executable"),
        ("tessdata/rus.traineddata", b"rus"),
        ("tessdata/eng.traineddata", b"eng"),
        ("engine.dll", b"dll"),
    ):
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        entries.append((name, hashlib.sha256(data).hexdigest()))
    files = dict(entries)
    engine = OcrEngineInfo(
        "tesseract",
        "synthetic",
        tuple((n, files[f"tessdata/{n}.traineddata"]) for n in ("rus", "eng")),
    )
    return TesseractRuntime(tmp_path, engine, tuple(entries))


def test_all_pinned_files_are_verified_each_time_without_mutation(tmp_path):
    value = runtime(tmp_path)
    value.verify()
    (tmp_path / "engine.dll").write_bytes(b"changed")
    with pytest.raises(OcrProcessError):
        value.verify()
    assert (tmp_path / "engine.dll").read_bytes() == b"changed"


def test_unlisted_dll_and_missing_model_are_rejected(tmp_path):
    value = runtime(tmp_path)
    extra = tmp_path / "foreign.dll"
    extra.write_bytes(b"x")
    with pytest.raises(OcrProcessError):
        value.verify()
    extra.unlink()
    (tmp_path / "tessdata/rus.traineddata").unlink()
    with pytest.raises(OcrProcessError):
        value.verify()


@pytest.mark.parametrize(
    "name", ["../outside", "/absolute", "tessdata/../file", "a\\b", "C:secret", "a//b", "./file"]
)
def test_manifest_paths_cannot_escape_root(name, tmp_path):
    value = runtime(tmp_path)
    with pytest.raises(ValidationError):
        replace(value, file_sha256=(*value.file_sha256, (name, "1" * 64)))


def test_manifest_requires_model_hash_agreement_and_unique_names(tmp_path):
    value = runtime(tmp_path)
    with pytest.raises(ValidationError):
        replace(value, file_sha256=(*value.file_sha256, ("ENGINE.DLL", "1" * 64)))
    with pytest.raises(ValidationError):
        replace(
            value, engine=replace(value.engine, model_sha256=(("rus", "1" * 64), ("eng", "2" * 64)))
        )


def test_engine_uses_explicit_verified_paths_and_private_minimal_environment(tmp_path, monkeypatch):
    import struct

    from personal_finance.domain.ocr import OcrImage
    from personal_finance.infrastructure.ocr import tesseract

    value = runtime(tmp_path)
    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 20, 10) + b"\0" * 9
    image = OcrImage(png, 20, 10)
    calls = []
    monkeypatch.setenv("HTTPS_PROXY", "synthetic secret")
    monkeypatch.setenv("TESSDATA_PREFIX", "foreign models")

    def process(command, contents, **options):
        calls.append(command)
        assert command[0] == str(tmp_path / value.executable_name)
        assert command[1:3] == ("stdin", "stdout")
        assert "rus+eng" in command and str(tmp_path / "tessdata") in command
        assert options["cwd"] == tmp_path and options["timeout_seconds"] == 30
        assert "HTTPS_PROXY" not in options["environment"]
        assert "TESSDATA_PREFIX" not in options["environment"]
        assert contents is image.png
        return (
            b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
            b"left\ttop\twidth\theight\tconf\ttext\n"
            b"1\t1\t0\t0\t0\t0\t0\t0\t20\t10\t-1\t\n"
        )

    monkeypatch.setattr(tesseract, "run_ocr_process", process)
    result = tesseract.TesseractEngine(value).recognize(image)
    assert result.engine == value.engine and result.words == ()
    (tmp_path / "engine.dll").write_bytes(b"changed")
    with pytest.raises(OcrProcessError):
        tesseract.TesseractEngine(value).recognize(image)
    assert len(calls) == 1
