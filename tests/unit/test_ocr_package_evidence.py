"""Написанные сценарии инструмента происхождения; без настоящих OCR-DLL."""

import hashlib
import json

import pytest

from tools.prepare_ocr_package_evidence import collect_evidence, main, native_requirements

NATIVE = b"synthetic binary bytes, not executable"
HASH = hashlib.sha256(NATIVE).hexdigest()


def manifest(tmp_path, rows=None, **extra):
    path = tmp_path / "runtime.json"
    path.write_text(
        json.dumps(
            {
                "format": 1,
                "file_sha256": [["tesseract.exe", HASH], ["libsample.dll", HASH]]
                if rows is None
                else rows,
                **extra,
            }
        ),
        encoding="utf-8",
    )
    return path


def package(root, name, binaries=None, metadata=None):
    folder = root / name
    (folder / "mingw64/bin").mkdir(parents=True)
    (folder / ".PKGINFO").write_text(
        metadata
        if metadata is not None
        else (
            f"pkgname = {name}\npkgver = 1.0-1\narch = x86_64\n"
            "license = MIT\nlicense = BSD-2-Clause\n"
        ),
        encoding="utf-8",
    )
    (folder / ".BUILDINFO").write_text("format = 2\ninstalled = synthetic-1.0\n", encoding="utf-8")
    for binary, value in (binaries or {}).items():
        (folder / "mingw64/bin" / binary).write_bytes(value)
    return folder


def test_exact_bytes_match_only_correct_package_and_do_not_approve_distribution(tmp_path):
    root = tmp_path / "packages"
    package(root, "wrong", {"libsample.dll": b"different bytes"})
    package(root, "matching", {"libsample.dll": NATIVE})
    report = collect_evidence(manifest(tmp_path), root)
    assert report["native_byte_matches"] == 1 and report["native_unmatched"] == 1
    record = next(r for r in report["records"] if r["path"] == "libsample.dll")
    assert record["different_byte_candidates"] == 1
    matched = record["matching_packages"]
    assert len(matched) == 1 and matched[0]["package"]["pkgname"] == "matching"
    assert matched[0]["package"]["buildinfo_sha256"]
    assert matched[0]["package"]["licenses_declared_in_pkginfo"] == ["BSD-2-Clause", "MIT"]
    assert not matched[0]["package"]["package_signature_verified"]
    assert not matched[0]["package"]["source_archive_verified"]
    assert not report["license_audit_complete"] and not report["application_bundled"]


def test_multiple_identical_candidates_are_preserved_without_guessing_source(tmp_path):
    root = tmp_path / "packages"
    for name in ("one", "two"):
        package(root, name, {"libsample.dll": NATIVE, "tesseract.exe": NATIVE})
    report = collect_evidence(manifest(tmp_path), root)
    assert report["native_byte_matches"] == 2 and report["native_unmatched"] == 0
    assert all(len(r["matching_packages"]) == 2 for r in report["records"])
    assert not report["license_audit_complete"]


@pytest.mark.parametrize(
    "rows",
    [
        [["../libsample.dll", HASH]],
        [["/libsample.dll", HASH]],
        [["dir/libsample.dll", HASH]],
        [["C:libsample.dll", HASH]],
        [["libsample.dll", "bad"]],
        [["libsample.dll", HASH], ["LIBSAMPLE.dll", HASH]],
        [["libsample.dll", HASH, 1]],
        [["libsample.dll", False]],
        [],
    ],
)
def test_manifest_rejects_unsafe_names_duplicates_and_wrong_types(tmp_path, rows):
    with pytest.raises(ValueError):
        native_requirements(manifest(tmp_path, rows))


@pytest.mark.parametrize("version", [True, 1.0, 2, None])
def test_manifest_format_has_exact_type_and_known_version(tmp_path, version):
    with pytest.raises(ValueError):
        native_requirements(manifest(tmp_path, format=version))


@pytest.mark.parametrize(
    "metadata",
    [
        "pkgname = one\npkgver = 1\n",
        "pkgname = one\npkgname = two\npkgver = 1\narch = x86_64\n",
        "pkgname = \npkgver = 1\narch = x86_64\n",
        "not a metadata record\n",
        "pkgname = one\x00\npkgver = 1\narch = x86_64\n",
    ],
)
def test_package_metadata_must_be_complete_and_unambiguous(tmp_path, metadata):
    root = tmp_path / "packages"
    package(root, "one", metadata=metadata)
    with pytest.raises(ValueError):
        collect_evidence(manifest(tmp_path), root)


def test_report_exclusive_creation_preserves_existing_file(tmp_path):
    root = tmp_path / "packages"
    root.mkdir()
    input_manifest = manifest(tmp_path)
    output = tmp_path / "existing.json"
    output.write_bytes(b"keep this existing technical file")
    with pytest.raises(SystemExit) as error:
        main(["--manifest", str(input_manifest), "--packages", str(root), "--report", str(output)])
    assert error.value.code == 2
    assert output.read_bytes() == b"keep this existing technical file"


def test_cli_creates_only_report_and_does_not_change_package_bytes(tmp_path, capsys):
    root = tmp_path / "packages"
    folder = package(root, "one", {"libsample.dll": NATIVE})
    input_manifest = manifest(tmp_path)
    original = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    output = tmp_path / "new.json"
    assert (
        main(["--manifest", str(input_manifest), "--packages", str(root), "--report", str(output)])
        == 0
    )
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "byte_matches_only"
    assert "Лицензионная проверка не завершена" in capsys.readouterr().out
    assert {p: p.read_bytes() for p in original} == original


def test_symlink_to_file_outside_package_root_is_refused(tmp_path):
    root = tmp_path / "packages"
    folder = package(root, "one")
    outside = tmp_path / "outside.dll"
    outside.write_bytes(NATIVE)
    try:
        (folder / "mingw64/bin/libsample.dll").symlink_to(outside)
    except OSError:
        pytest.skip("В этой Windows создание тестовой ссылки недоступно.")
    with pytest.raises(ValueError):
        collect_evidence(manifest(tmp_path), root)
