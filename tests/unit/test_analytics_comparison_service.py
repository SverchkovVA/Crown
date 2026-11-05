from dataclasses import replace
from threading import RLock
from uuid import uuid4

import pytest

from personal_finance.application.analytics import Analytics
from personal_finance.domain.errors import VaultError
from personal_finance.domain.ledger import OperationKind
from tests.unit.test_analytics import PERIOD, operation


class Ledger:
    def __init__(self):
        self.reads = 0
        self.original = operation(OperationKind.INCOME, 100)

    def operations(self):
        self.reads += 1
        return [
            self.original if self.reads == 1 else replace(self.original, revision_id=str(uuid4()))
        ]


def test_service_reads_one_snapshot_for_two_equal_periods():
    ledger = Ledger()
    opened = []
    result = Analytics(ledger, RLock(), lambda: opened.append(True)).compare(PERIOD, PERIOD)
    assert ledger.reads == len(opened) == 1
    assert result.before.sources[0] is result.after.sources[0] is ledger.original
    assert result.totals[0].income_delta == 0


def test_closed_session_refuses_before_reading_the_ledger():
    ledger = Ledger()

    def closed():
        raise VaultError("Книга закрыта.")

    with pytest.raises(VaultError):
        Analytics(ledger, RLock(), closed).compare(PERIOD, PERIOD)
    assert ledger.reads == 0
