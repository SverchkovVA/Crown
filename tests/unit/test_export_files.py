import json
from types import SimpleNamespace

import pytest

from personal_finance.domain.errors import FinanceError, ValidationError
from personal_finance.domain.exporting import ExportFormat, ExportSettings, prepare_journal_export
from personal_finance.infrastructure.exporting import files


@pytest.mark.parametrize("format", list(ExportFormat))
def test_export_creates_only_new_file_outside_book(tmp_path, format):
    root = tmp_path / "book.pfvault"
    root.mkdir()
    preview = prepare_journal_export((), ExportSettings())
    target = tmp_path / ("Проверочный экспорт." + format.value)
    assert files.write_export_new(target, preview, format, vault_root=root) == target.resolve()
    if format == ExportFormat.JSON:
        assert json.loads(target.read_bytes())["operations"] == []
    else:
        assert target.read_bytes().startswith(b"\xef\xbb\xbf")
    assert not list(tmp_path.glob(".crown-export-*"))
    assert not list(root.iterdir())


def test_export_never_replaces_existing_file(tmp_path):
    target = tmp_path / "existing.json"
    target.write_bytes(b"keep existing")
    with pytest.raises(ValidationError, match="существует"):
        files.write_export_new(
            target,
            prepare_journal_export((), ExportSettings()),
            ExportFormat.JSON,
            vault_root=tmp_path / "book",
        )
    assert target.read_bytes() == b"keep existing"


def test_export_rejects_book_folder_and_wrong_extension(tmp_path):
    root = tmp_path / "book.pfvault"
    root.mkdir()
    for target in (root / "export.json", tmp_path / "export.pfbackup"):
        with pytest.raises(ValidationError):
            files.write_export_new(
                target,
                prepare_journal_export((), ExportSettings()),
                ExportFormat.JSON,
                vault_root=root,
            )
        assert not target.exists()


def test_failure_before_publish_leaves_no_target_or_temporary_file(tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("Синтетический отказ диска")

    monkeypatch.setattr(files.os, "fsync", fail)
    with pytest.raises(FinanceError):
        files.write_export_new(
            tmp_path / "new.json",
            prepare_journal_export((), ExportSettings()),
            ExportFormat.JSON,
            vault_root=tmp_path / "book",
        )
    assert not list(tmp_path.iterdir())


def test_racing_destination_is_preserved_and_our_temporary_file_removed(tmp_path, monkeypatch):
    publish = files._publish

    def race(temporary, destination):
        destination.write_bytes(b"other writer")
        publish(temporary, destination)

    monkeypatch.setattr(files, "_publish", race)
    target = tmp_path / "race.json"
    with pytest.raises(ValidationError):
        files.write_export_new(
            target,
            prepare_journal_export((), ExportSettings()),
            ExportFormat.JSON,
            vault_root=tmp_path / "book",
        )
    assert target.read_bytes() == b"other writer"
    assert not list(tmp_path.glob(".crown-export-*"))


def test_temporary_name_collision_never_deletes_foreign_file(tmp_path, monkeypatch):
    temporary = tmp_path / ".crown-export-collision.tmp"
    temporary.write_bytes(b"foreign temporary contents")
    monkeypatch.setattr(files, "uuid4", lambda: SimpleNamespace(hex="collision"))
    with pytest.raises(ValidationError):
        files.write_export_new(
            tmp_path / "new.json",
            prepare_journal_export((), ExportSettings()),
            ExportFormat.JSON,
            vault_root=tmp_path / "book",
        )
    assert temporary.read_bytes() == b"foreign temporary contents"
    assert not (tmp_path / "new.json").exists()
