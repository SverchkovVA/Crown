import os
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from personal_finance.application.vault import create_vault, open_vault
from personal_finance.domain.errors import InvalidCredentials
from personal_finance.domain.models import AccountKind

BEFORE = "вымышленная прежняя фраза"
AFTER = "вымышленная следующая фраза"
CUTOVER = """
import os
import sys
from pathlib import Path
from personal_finance.application.vault import open_vault
from personal_finance.infrastructure.security import manifest_file

session = open_vault(Path(sys.argv[1]), sys.argv[2])
replace = manifest_file.os.replace

def interrupt(staged, target):
    if target != session.root / "data/key.json":
        replace(staged, target)
        return
    if sys.argv[4] == "after":
        replace(staged, target)
    os._exit(79)

manifest_file.os.replace = interrupt
session.change_password(sys.argv[2], sys.argv[3])
raise RuntimeError("Ожидалось прерывание в точке замены ключевого файла")
"""


@pytest.mark.parametrize("stage", ["before", "after"])
def test_password_change_survives_process_exit_at_publication(tmp_path: Path, stage: str) -> None:
    root = tmp_path / "crash.pfvault"
    created, recovery = create_vault(root, "Учебная книга для прерывания", BEFORE)
    with closing(created) as session:
        account = session.catalog.add_account("Учебный счёт", AccountKind.CASH)
    key_path = root / "data/key.json"
    before_key = key_path.read_bytes()
    database = root / "data/ledger.sqlite3"
    before_database = database.read_bytes()

    result = subprocess.run(
        [sys.executable, "-c", CUTOVER, str(root), BEFORE, AFTER, stage],
        capture_output=True,
        timeout=60,
        check=False,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 79, result.stderr.decode("utf-8", errors="replace")
    assert (key_path.read_bytes() == before_key) == (stage == "before")
    valid, rejected = (BEFORE, AFTER) if stage == "before" else (AFTER, BEFORE)
    with pytest.raises(InvalidCredentials):
        open_vault(root, rejected)
    for credential, by_code in ((valid, False), (recovery, True)):
        with closing(open_vault(root, credential, recovery=by_code)) as reopened:
            assert reopened.catalog.accounts()[0].id == account.id
            reopened.check_integrity()
    assert database.read_bytes() == before_database
