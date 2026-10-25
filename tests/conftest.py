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
