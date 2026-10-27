import json
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from sqlalchemy import text

from personal_finance.application.vault import open_vault
from personal_finance.domain.classification import CategoryRule
from personal_finance.domain.errors import VaultError
from personal_finance.domain.ledger import OperationKind
from personal_finance.domain.models import CategoryKind
from tests.conftest import PASSWORD


def state(session):
    with session.engine.connect() as connection:
        return [
            connection.execute(text("SELECT count(*) FROM " + name)).scalar_one()
            for name in (
                "entities",
                "entity_revisions",
                "revision_parents",
                "revision_heads",
                "change_groups",
                "change_log",
                "command_receipts",
                "category_rule_revisions",
                "postings",
            )
        ] + list(connection.execute(text("SELECT sequence FROM replicas ORDER BY id")).scalars())


@pytest.mark.parametrize("phase", ["add", "edit", "disable"])
def test_terminated_rule_command_rolls_back_all_history_and_sequence(session, tmp_path, phase):
    category = next(c.id for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE)
    saved = session.classification.save(
        CategoryRule(str(uuid4()), "Исходное правило", OperationKind.EXPENSE, "магазин", category)
    )
    before = state(session)
    root = session.root
    session.close()
    ready = tmp_path / "category-rule-ready"
    program = r"""
import json,sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4
from personal_finance.application.vault import open_vault
import personal_finance.infrastructure.persistence.classification_repository as storage
p=json.loads(sys.stdin.readline())
session=open_vault(Path(p['root']),p['password'])
saved=session.classification.rules()[0]
insert=storage.SqlClassificationRepository._insert_payload
def pause(self,*args):
    insert(self,*args)
    Path(p['ready']).write_text('ready')
    sys.stdin.readline()
storage.SqlClassificationRepository._insert_payload=pause
if p['phase']=='add':
    session.classification.save(replace(saved.rule,id=str(uuid4()),name='New synthetic rule'))
elif p['phase']=='edit':
    session.classification.save(replace(saved.rule,priority=50),expected_revision_id=saved.revision_id)
else:
    session.classification.save(replace(saved.rule,enabled=False),expected_revision_id=saved.revision_id)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", program],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        assert process.stdin is not None
        process.stdin.write(
            json.dumps(
                {"root": str(root), "password": PASSWORD, "ready": str(ready), "phase": phase}
            )
            + "\n"
        )
        process.stdin.flush()
        deadline = time.monotonic() + 20
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "Дочерний процесс не начал запись правила"
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
        assert state(reopened) == before
        assert reopened.classification.rules() == [saved]
        assert reopened.classification.history(saved.rule.id) == [saved]
        reopened.check_integrity()
    finally:
        reopened.close()
