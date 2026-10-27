import json
import subprocess
import sys
import time
from datetime import date

import pytest
from sqlalchemy import text

from personal_finance.application.vault import open_vault
from personal_finance.domain.errors import VaultError
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from tests.conftest import PASSWORD

TABLES = (
    "entities",
    "entity_revisions",
    "revision_parents",
    "revision_heads",
    "change_groups",
    "change_log",
    "command_receipts",
    "tag_revisions",
    "saved_filter_revisions",
    "operation_tags",
    "postings",
)


def snapshot(session):
    with session.engine.connect() as c:
        return [
            c.execute(text("SELECT count(*) FROM " + table)).scalar_one() for table in TABLES
        ] + list(c.execute(text("SELECT sequence FROM replicas ORDER BY id")).scalars())


@pytest.mark.parametrize("phase", ["tag", "filter", "operation"])
def test_terminated_organization_command_rolls_back_whole_group(session, tmp_path, phase):
    tag = session.organization.add_tag("Исходный тег")
    account = session.catalog.add_account("Наличные", AccountKind.CASH)
    category = next(c.id for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE)
    operation = session.ledger.add(
        OperationDraft(OperationKind.EXPENSE, date(2026, 1, 15), account.id, Money(1000), category)
    )
    before = snapshot(session)
    root = session.root
    session.close()
    ready = tmp_path / "organization-ready"
    program = r"""
import json,sys
from pathlib import Path
from dataclasses import replace
from personal_finance.application.vault import open_vault
from personal_finance.domain.query import OperationQuery
from personal_finance.infrastructure.persistence.organization_repository import (
    SqlOrganizationRepository,
)
from personal_finance.infrastructure.persistence.ledger_repository import SqlLedgerRepository
p=json.loads(sys.stdin.readline())
session=open_vault(Path(p['root']),p['password'])
owner=SqlLedgerRepository if p['phase']=='operation' else SqlOrganizationRepository
name='_insert_tags' if p['phase']=='operation' else '_insert_payload'
insert=getattr(owner,name)
def pause(self,*args):
    insert(self,*args)
    Path(p['ready']).write_text('ready')
    sys.stdin.readline()
setattr(owner,name,pause)
if p['phase']=='tag': session.organization.add_tag('Новый тестовый тег')
elif p['phase']=='filter':
    session.organization.save_filter('Новый фильтр',OperationQuery(tag_ids=(p['tag'],)))
else:
    operation=session.ledger.operations()[0]
    session.ledger.replace(operation.id,operation.revision_id,replace(operation.draft,tag_ids=(p['tag'],)))
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
                    "tag": tag.id,
                }
            )
            + "\n"
        )
        process.stdin.flush()
        deadline = time.monotonic() + 20
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "Дочерний процесс не начал запись тегов или фильтра"
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
        assert reopened.organization.tags() == [tag]
        assert reopened.organization.filters() == []
        assert reopened.ledger.history(operation.id) == [operation]
        reopened.check_integrity()
    finally:
        reopened.close()
