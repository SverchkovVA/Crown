from tools.runtime_info import source_fingerprint


def base(tmp_path):
    for name in (
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        ".gitattributes",
        "check-windows.cmd",
        "THIRD_PARTY_NOTICES.md",
    ):
        (tmp_path / name).write_text("Вымышленный файл\n", encoding="utf-8")
    return tmp_path


def test_build_fingerprint_covers_bundled_license_content_and_normalizes_newlines(tmp_path):
    root = base(tmp_path)
    folder = root / "docs" / "legal"
    folder.mkdir(parents=True)
    license_file = folder / "example.txt"
    license_file.write_bytes(b"Example license\n")
    before = source_fingerprint(root)
    license_file.write_bytes(b"Example license\r\n")
    assert source_fingerprint(root) == before
    license_file.write_bytes(b"Changed license\n")
    assert source_fingerprint(root) != before


def test_build_fingerprint_ignores_private_artifacts_and_ordinary_docs(tmp_path):
    root = base(tmp_path)
    before = source_fingerprint(root)
    (root / "artifacts").mkdir()
    (root / "artifacts" / "private-example.json").write_text("Вымышленный отчёт", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "progress.md").write_text("Описание прогресса", encoding="utf-8")
    assert source_fingerprint(root) == before
