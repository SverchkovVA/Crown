import json
from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtCore import QSettings

from personal_finance.infrastructure.recent_books import MAX_RECENT, LocalRecentBooks


def store(tmp_path):
    return LocalRecentBooks(QSettings(str(tmp_path / "recent.ini"), QSettings.Format.IniFormat))


def test_remember_restart_reorder_and_forget_do_not_touch_book(tmp_path):
    value = store(tmp_path)
    one, two = str(uuid4()), str(uuid4())
    a, b = tmp_path / "first.pfvault", tmp_path / "second.pfvault"
    a.mkdir()
    (a / "unchanged").write_bytes(b"synthetic")
    value.remember(a, one, "Первая")
    value.remember(b, two, "Вторая")
    assert [v.book_id for v in store(tmp_path).items()] == [two, one]
    value.remember(a, one, "Новое имя")
    assert [v.name for v in value.items()] == ["Новое имя", "Вторая"]
    value.forget(str(a))
    assert len(store(tmp_path).items()) == 1 and (a / "unchanged").read_bytes() == b"synthetic"


def test_disabled_book_is_removed_and_not_remembered_after_move_or_restart(tmp_path):
    value = store(tmp_path)
    identifier = str(uuid4())
    value.remember(tmp_path / "a.pfvault", identifier, "Проверка")
    value.set_enabled(identifier, False)
    value = store(tmp_path)
    assert not value.items() and not value.enabled(identifier)
    value.remember(tmp_path / "moved.pfvault", identifier, "Другое название")
    assert not value.items()
    value.set_enabled(identifier, True)
    value.remember(tmp_path / "moved.pfvault", identifier, "Снова")
    assert value.items()[0].name == "Снова"


def test_corrupted_mru_does_not_clear_disabled_policy(tmp_path):
    value = store(tmp_path)
    identifier = str(uuid4())
    value.set_enabled(identifier, False)
    value.settings.setValue("recent_books_v1", "{bad")
    assert not value.items() and not value.enabled(identifier)


def test_list_is_bounded_and_does_not_probe_paths(tmp_path, monkeypatch):
    value = store(tmp_path)
    for i in range(MAX_RECENT + 3):
        value.remember(tmp_path / f"missing-{i}.pfvault", str(uuid4()), f"Книга {i}")
    monkeypatch.setattr(Path, "exists", lambda self: pytest.fail("Path probing is not allowed"))
    assert len(value.items()) == MAX_RECENT


@pytest.mark.parametrize(
    "raw", ["{}", '["wrong"]', json.dumps([{"book_id": "bad", "path": "x", "name": "X"}]), "null"]
)
def test_invalid_settings_do_not_break_startup(tmp_path, raw):
    value = store(tmp_path)
    value.settings.setValue("recent_books_v1", raw)
    assert not value.items()


def test_metadata_contains_only_name_path_identifier(tmp_path):
    value = store(tmp_path)
    identifier = str(uuid4())
    value.remember(tmp_path / "test.pfvault", identifier, "Вымышленная книга")
    rows = json.loads(value.settings.value("recent_books_v1"))
    assert set(rows[0]) == {"book_id", "path", "name"}
