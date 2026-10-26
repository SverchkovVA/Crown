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


@pytest.mark.parametrize("split", [False, True])
def test_process_termination_rolls_back_financial_revision_and_postings(session, tmp_path, split):
    account = session.catalog.add_account("Счёт аварийной проверки", AccountKind.CASH)
    income = next(c for c in session.catalog.categories() if c.kind == CategoryKind.INCOME)
    expenses = [c for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE]
    expense = expenses[0]
    assert session.ledger is not None
    original = session.ledger.add(
        OperationDraft(
            OperationKind.INCOME,
            date(2026, 10, 26),
            account.id,
            Money(1000),
            income.id,
        )
    )
    with session.engine.connect() as connection:
        sequence = connection.execute(
            text("SELECT sequence FROM replicas WHERE id=:id"),
            {"id": session.replica_id},
        ).scalar_one()
    root = session.root
    session.close()
    ready = tmp_path / "financial-ready"
    program = r"""
import json, sys
from datetime import date
from pathlib import Path
from personal_finance.application.vault import open_vault
from personal_finance.domain.ledger import OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import Money
from personal_finance.infrastructure.persistence.ledger_repository import SqlLedgerRepository
parameters=json.loads(sys.stdin.readline())
session=open_vault(Path(parameters["root"]),parameters["password"])
original=SqlLedgerRepository._insert_postings
def pause_after_postings(self,*args):
    original(self,*args)
    Path(parameters["ready"]).write_text("ready")
    sys.stdin.readline()
SqlLedgerRepository._insert_postings=pause_after_postings
session.ledger.add(OperationDraft(
    OperationKind.EXPENSE,date(2026,10,26),parameters["account"],Money(500),
    category_id=None if parameters["split"] else parameters["category"],
    parts=(OperationPart(parameters["category"],Money(300)),
           OperationPart(parameters["other_category"],Money(200))) if parameters["split"] else (),
))
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
                    "category": expense.id,
                    "other_category": expenses[1].id,
                    "split": split,
                }
            )
            + "\n"
        )
        process.stdin.flush()
        deadline = time.monotonic() + 20
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "Дочерний процесс не начал финансовую транзакцию"
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
        assert reopened.ledger is not None
        assert reopened.ledger.operations() == [original]
        assert reopened.ledger.balances()[0].amount.minor == 1000
        with reopened.engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM postings")).scalar_one() == 2
            assert (
                connection.execute(text("SELECT count(*) FROM operation_parts")).scalar_one() == 0
            )
            assert (
                connection.execute(
                    text("SELECT sequence FROM replicas WHERE id=:id"),
                    {"id": reopened.replica_id},
                ).scalar_one()
                == sequence
            )
        reopened.check_integrity()
    finally:
        reopened.close()
