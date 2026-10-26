import json
import subprocess
import sys
import time
from datetime import date

import pytest
from sqlalchemy import text

from personal_finance.application.vault import open_vault
from personal_finance.domain.errors import VaultError
from personal_finance.domain.ledger import OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from tests.conftest import PASSWORD


@pytest.mark.parametrize("split", [False, True])
def test_process_termination_rolls_back_linked_refund(session, tmp_path, split):
    account = session.catalog.add_account("Синтетический счёт возврата", AccountKind.CASH)
    categories = [c.id for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE]
    purchase = session.ledger.add(
        OperationDraft(
            OperationKind.EXPENSE,
            date(2026, 10, 27),
            account.id,
            Money(1000),
            category_id=None if split else categories[0],
            parts=(
                OperationPart(categories[0], Money(600)),
                OperationPart(categories[1], Money(400)),
            )
            if split
            else (),
        )
    )
    with session.engine.connect() as c:
        sequence = c.execute(
            text("SELECT sequence FROM replicas WHERE id=:id"), {"id": session.replica_id}
        ).scalar_one()
    root = session.root
    session.close()
    ready = tmp_path / "refund-ready"
    program = r"""
import json,sys
from datetime import date
from pathlib import Path
from personal_finance.application.vault import open_vault
from personal_finance.domain.ledger import OperationDraft,OperationKind,OperationPart
from personal_finance.domain.models import Money
from personal_finance.infrastructure.persistence.ledger_repository import SqlLedgerRepository
p=json.loads(sys.stdin.readline())
session=open_vault(Path(p['root']),p['password'])
insert=SqlLedgerRepository._insert_postings
def pause(self,*args):
    insert(self,*args)
    Path(p['ready']).write_text('ready')
    sys.stdin.readline()
SqlLedgerRepository._insert_postings=pause
session.ledger.add(OperationDraft(OperationKind.REFUND,date(2026,10,28),p['account'],Money(500),
    category_id=None if p['split'] else p['categories'][0],
    parts=(OperationPart(p['categories'][0],Money(200)),OperationPart(p['categories'][1],Money(300)))
    if p['split'] else (),refund_of=p['purchase'],refund_of_revision_id=p['revision']))
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
                    "account": account.id,
                    "categories": categories,
                    "purchase": purchase.id,
                    "revision": purchase.revision_id,
                    "split": split,
                }
            )
            + "\n"
        )
        process.stdin.flush()
        deadline = time.monotonic() + 20
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "Дочерний процесс не начал транзакцию возврата"
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
        assert reopened.ledger.operations() == [purchase]
        assert reopened.ledger.balances()[0].amount == Money(-1000)
        assert reopened.ledger.refund_candidates()[0].amount_remaining == Money(1000)
        with reopened.engine.connect() as c:
            assert c.execute(text("SELECT count(*) FROM postings")).scalar_one() == (
                3 if split else 2
            )
            assert c.execute(text("SELECT count(*) FROM operation_parts")).scalar_one() == (
                2 if split else 0
            )
            assert (
                c.execute(
                    text("SELECT sequence FROM replicas WHERE id=:id"), {"id": reopened.replica_id}
                ).scalar_one()
                == sequence
            )
        reopened.check_integrity()
    finally:
        reopened.close()
