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


@pytest.fixture
def session_0017(tmp_path, monkeypatch) -> Iterator[VaultSession]:
    from tests.integration.test_deposit_scenario_upgrade import create_at

    opened, _ = create_at(monkeypatch, tmp_path / "previous.pfvault", "0017")
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


@pytest.fixture(autouse=True)
def isolated_theme_preferences(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from personal_finance.presentation import theme

    monkeypatch.setattr(
        theme,
        "default_theme_settings",
        lambda: QSettings(str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat),
    )
