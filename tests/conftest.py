import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from personal_finance.application.vault import VaultSession, create_vault  # noqa: E402

PASSWORD = "длинная тестовая парольная фраза"
BACKUP_PASSWORD = "другая парольная фраза копии"


@pytest.fixture
def session(tmp_path: Path) -> Iterator[VaultSession]:
    opened, _ = create_vault(tmp_path / "test.pfvault", "Тестовая книга", PASSWORD)
    try:
        yield opened
    finally:
        opened.close()


@pytest.fixture(autouse=True)
def isolated_recent_books(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from personal_finance.infrastructure import recent_books

    monkeypatch.setattr(
        recent_books,
        "default_settings",
        lambda: QSettings(str(tmp_path / "recent-books.ini"), QSettings.Format.IniFormat),
    )
