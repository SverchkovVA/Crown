import json
import subprocess
import sys
import time

import pytest
from sqlalchemy import text

from personal_finance.application.vault import open_vault
from personal_finance.domain.errors import VaultError
from personal_finance.domain.models import AccountKind, CategoryKind
from tests.conftest import PASSWORD

TABLES = (
    "entities",
    "entity_revisions",
    "change_groups",
    "revision_heads",
    "change_log",
    "command_receipts",
    "postings",
    "import_batches",
    "import_items",
)


def snapshot(session):
    with session.engine.connect() as c:
        return [
            c.execute(text("SELECT count(*) FROM " + table)).scalar_one() for table in TABLES
        ] + list(c.execute(text("SELECT sequence FROM replicas ORDER BY id")).scalars())


@pytest.mark.parametrize("phase", ["first_item", "after_seal"])
def test_terminated_import_never_leaves_partial_batch_or_dedup_keys(session, tmp_path, phase):
    account = session.catalog.add_account("Синтетический счёт импорта", AccountKind.CASH)
    category = next(c.id for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE)
    before, root = snapshot(session), session.root
    session.close()
    ready = tmp_path / "import-ready"
    program = r"""
import json,sys
from pathlib import Path
from datetime import date
from personal_finance.application.vault import open_vault
from personal_finance.domain.imports import ImportSelection
from personal_finance.domain.ledger import OperationDraft,OperationKind
from personal_finance.domain.models import Money
from personal_finance.infrastructure.importing.statements import CsvMapping
from personal_finance.infrastructure.persistence.import_repository import SqlImportRepository
p=json.loads(sys.stdin.readline())
session=open_vault(Path(p['root']),p['password'])
name='_insert_item' if p['phase']=='first_item' else '_result'
original=getattr(SqlImportRepository,name)
def pause(self,*args):
    result=original(self,*args)
    Path(p['ready']).write_text('ready')
    sys.stdin.readline()
    return result
setattr(SqlImportRepository,name,pause)
document=session.importing.parse_csv(
    b'date;amount;id\n2026-01-15;-1.00;one\n2026-01-16;-2.00;two\n',
    CsvMapping('date','amount',id_column='id',date_format='%Y-%m-%d',decimal_separator='.'),
)
selected=tuple(ImportSelection(r.position,OperationDraft(OperationKind.EXPENSE,
    r.occurred_on,p['account'],Money(abs(r.amount.minor)),p['category'])) for r in document.rows)
session.importing.confirm(document,p['account'],selected)
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
                    "phase": phase,
                    "account": account.id,
                    "category": category,
                }
            )
            + "\n"
        )
        process.stdin.flush()
        deadline = time.monotonic() + 20
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "Дочерний процесс не достиг выбранного шага импорта"
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
    reopened = open_vault(root, PASSWORD)
    try:
        assert snapshot(reopened) == before
        assert not reopened.ledger.operations()
        reopened.check_integrity()
    finally:
        reopened.close()
