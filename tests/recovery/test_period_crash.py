import json
import subprocess
import sys
import time

import pytest
from sqlalchemy import text

from personal_finance.application.vault import open_vault
from personal_finance.domain.errors import VaultError
from personal_finance.domain.periods import AccountingMonth
from tests.conftest import PASSWORD


@pytest.mark.parametrize("reopen", [False, True])
def test_terminated_period_transition_is_rolled_back(session, tmp_path, reopen):
    month = AccountingMonth(2026, 1)
    closed = session.periods.close(month) if reopen else None
    expected = session.periods.history(month)
    with session.engine.connect() as c:
        before = [
            c.execute(text("SELECT count(*) FROM " + table)).scalar_one()
            for table in (
                "entities",
                "entity_revisions",
                "period_revisions",
                "change_groups",
                "change_log",
                "command_receipts",
            )
        ]
        sequence = c.execute(
            text("SELECT sequence FROM replicas WHERE id=:id"), {"id": session.replica_id}
        ).scalar_one()
    root = session.root
    session.close()
    ready = tmp_path / "period-ready"
    program = r"""
import json,sys
from pathlib import Path
from personal_finance.application.vault import open_vault
from personal_finance.domain.periods import AccountingMonth
from personal_finance.infrastructure.persistence.period_repository import SqlPeriodRepository
p=json.loads(sys.stdin.readline())
session=open_vault(Path(p['root']),p['password'])
insert=SqlPeriodRepository._insert_payload
def pause(self,*args):
    insert(self,*args)
    Path(p['ready']).write_text('ready')
    sys.stdin.readline()
SqlPeriodRepository._insert_payload=pause
month=AccountingMonth(2026,1)
if p['reopen']: session.periods.reopen(month,p['revision'],reason='Синтетическая проверка')
else: session.periods.close(month)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", program],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdin is not None
        process.stdin.write(
            json.dumps(
                {
                    "root": str(root),
                    "password": PASSWORD,
                    "ready": str(ready),
                    "reopen": reopen,
                    "revision": closed.revision_id if closed else None,
                }
            )
            + "\n"
        )
        process.stdin.flush()
        deadline = time.monotonic() + 20
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "Дочерний процесс не начал изменение периода"
        with pytest.raises(VaultError, match="уже открыта"):
            open_vault(root, PASSWORD).close()
        process.terminate()
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        if process.stdin:
            process.stdin.close()
        if process.stderr:
            process.stderr.close()
    restored = open_vault(root, PASSWORD)
    try:
        assert restored.periods.history(month) == expected
        with restored.engine.connect() as c:
            after = [
                c.execute(text("SELECT count(*) FROM " + table)).scalar_one()
                for table in (
                    "entities",
                    "entity_revisions",
                    "period_revisions",
                    "change_groups",
                    "change_log",
                    "command_receipts",
                )
            ]
            assert after == before
            assert (
                c.execute(
                    text("SELECT sequence FROM replicas WHERE id=:id"), {"id": restored.replica_id}
                ).scalar_one()
                == sequence
            )
        restored.check_integrity()
    finally:
        restored.close()
