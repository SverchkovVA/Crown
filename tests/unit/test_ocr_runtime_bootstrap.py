import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import personal_finance.bootstrap.ocr_runtime as module
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.infrastructure.ocr.process import OcrProcessError
from personal_finance.infrastructure.ocr.tesseract import TesseractEngine
from tests.ocr_fixtures import document, png_image


def fixture(tmp_path, monkeypatch):
    folder = tmp_path / "Набор с пробелами"
    folder.mkdir()
    files = []
    for name, data in [
        ("tesseract.exe", b"Windows synthetic"),
        ("tesseract", b"POSIX synthetic"),
        ("tessdata/rus.traineddata", b"rus"),
        ("tessdata/eng.traineddata", b"eng"),
    ]:
        p = folder / name
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(data)
        files.append([name, hashlib.sha256(data).hexdigest()])
    hashes = dict(files)
    trusted = tmp_path / "trusted.json"
    trusted.write_text(
        json.dumps(
            {
                "format": 1,
                "version": "synthetic",
                "models": [[n, hashes[f"tessdata/{n}.traineddata"]] for n in ["rus", "eng"]],
                "file_sha256": files,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "trusted_manifest_path", lambda: trusted)
    return folder, trusted


def test_source_manifest_has_pinned_engine_and_two_models():
    info, files = module.load_runtime_manifest()
    assert info.name == "tesseract" and info.version == "5.5.3.20260724" and len(files) == 58
    assert {n for n, _ in info.model_sha256} == {"rus", "eng"}


@pytest.mark.parametrize(
    "patch",
    [
        {"format": True},
        {"format": 2},
        {"version": False},
        {"models": []},
        {"file_sha256": [["../bad", "x"]]},
    ],
)
def test_corrupt_trusted_manifest_is_rejected(patch, tmp_path, monkeypatch):
    folder, path = fixture(tmp_path, monkeypatch)
    data = json.loads(path.read_text())
    data.update(patch)
    path.write_text(json.dumps(data))
    with pytest.raises(ValidationError):
        module.create_runtime(folder)


def test_manifest_is_size_bounded_and_missing_file_fails(tmp_path, monkeypatch):
    path = tmp_path / "missing.json"
    monkeypatch.setattr(module, "trusted_manifest_path", lambda: path)
    with pytest.raises(ValidationError):
        module.load_runtime_manifest()
    path.write_bytes(b" " * (module.MAX_MANIFEST_BYTES + 1))
    with pytest.raises(ValidationError):
        module.load_runtime_manifest()


def test_manifest_next_to_runtime_cannot_redefine_trusted_hashes(tmp_path, monkeypatch):
    folder, path = fixture(tmp_path, monkeypatch)
    runtime = module.create_runtime(folder)
    (folder / "runtime-candidate.json").write_text(
        path.read_text().replace("synthetic", "untrusted")
    )
    executable = folder / runtime.executable_name
    executable.write_bytes(b"untrusted content")
    other = json.loads(path.read_text())
    other["file_sha256"] = [
        [
            n,
            hashlib.sha256(executable.read_bytes()).hexdigest()
            if n == runtime.executable_name
            else h,
        ]
        for n, h in other["file_sha256"]
    ]
    (folder / "runtime-candidate.json").write_text(json.dumps(other))
    with pytest.raises(OcrProcessError):
        module.create_runtime(folder)


def test_loaded_engine_rechecks_files_before_every_recognition(tmp_path, monkeypatch):
    folder, _ = fixture(tmp_path, monkeypatch)
    runtime = module.create_runtime(folder)
    calls = []
    import personal_finance.infrastructure.ocr.tesseract as engine_module

    monkeypatch.setattr(engine_module, "run_ocr_process", lambda *a, **k: calls.append(True))
    (folder / runtime.executable_name).write_bytes(b"changed")
    with pytest.raises(OcrProcessError):
        TesseractEngine(runtime).recognize(png_image())
    assert not calls


def test_application_resource_is_used_in_frozen_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "sys", SimpleNamespace(frozen=True, _MEIPASS=str(tmp_path)))
    assert module.trusted_manifest_path() == tmp_path / "ocr/runtime-candidate.json"
    monkeypatch.setattr(module, "sys", SimpleNamespace(frozen=True))
    with pytest.raises(ValidationError):
        module.trusted_manifest_path()


def test_only_adjacent_frozen_runtime_is_discovered(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "ocr-runtime").mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(
        module, "sys", SimpleNamespace(frozen=True, executable=str(app / "PersonalFinance.exe"))
    )
    assert module.runtime_directory() is None
    (app / "ocr-runtime").mkdir()
    assert module.runtime_directory() == app / "ocr-runtime"
    monkeypatch.setattr(module, "sys", SimpleNamespace(frozen=False))
    assert module.runtime_directory() is None
    assert module.runtime_directory(Path("ocr-runtime")) == elsewhere / "ocr-runtime"


def test_factory_builds_three_profiles_and_generic_automatic_recognition(tmp_path, monkeypatch):
    folder, _ = fixture(tmp_path, monkeypatch)
    loaded = LoadedOcrImage("a" * 64, png_image())
    monkeypatch.setattr(module, "load_ocr_image", lambda _: loaded)

    def recognize(self, image):
        value = document(("Детали операции", "-12,50 RUB", "03.01.2026"), image_sha256=image.sha256)
        return replace(value, width=image.width, height=image.height)

    monkeypatch.setattr(TesseractEngine, "recognize", recognize)
    services = module.recognitions_for_directory(folder)
    assert [s.bank_name for s in services] == [UNIVERSAL_PROFILE, "Т-Банк", "ВТБ"]
    result = services[0].analyze(Path("unused.png"))
    assert result.bank_name == UNIVERSAL_PROFILE and result.card.amount.value == 1250


def test_runtime_path_is_not_resolved_before_link_verification(tmp_path, monkeypatch):
    folder, _ = fixture(tmp_path, monkeypatch)
    original_link, original_resolve = Path.is_symlink, Path.resolve
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == folder or original_link(p))

    def resolve(p, *a, **k):
        if p == folder:
            raise AssertionError("Нельзя разыменовывать путь до проверки ссылки")
        return original_resolve(p, *a, **k)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(OcrProcessError):
        module.create_runtime(folder)
