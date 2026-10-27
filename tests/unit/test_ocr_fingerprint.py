from tools.runtime_info import source_fingerprint


def test_ocr_runtime_manifest_changes_the_source_fingerprint(tmp_path):
    for name in (
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        ".gitattributes",
        "check-windows.cmd",
    ):
        (tmp_path / name).write_text("", encoding="utf-8")
    folder = tmp_path / "packaging/ocr"
    folder.mkdir(parents=True)
    manifest = folder / "runtime-candidate.json"
    manifest.write_text('{"sha256": "first"}', encoding="utf-8")
    first = source_fingerprint(tmp_path)
    manifest.write_text('{"sha256": "changed"}', encoding="utf-8")
    assert source_fingerprint(tmp_path) != first
