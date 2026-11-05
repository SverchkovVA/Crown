from threading import RLock

import pytest

from personal_finance.application.analytics import Analytics
from personal_finance.domain.errors import VaultError
from tests.unit.test_analytics_cashflow import PERIOD, snapshot


def test_cash_flow_reads_one_snapshot_and_checks_session_before_read():
    operations = snapshot()
    events = []

    class Ledger:
        def operations(self):
            events.append("read")
            return iter(operations)

    service = Analytics(Ledger(), RLock(), lambda: events.append("open"))
    report = service.cash_flow(PERIOD)
    assert events == ["open", "read"] and report.totals[0].closing_minor == 144900
    operations.clear()
    assert report.totals[0].closing_minor == 144900 and len(report.operation_references) == 6


def test_cash_flow_does_not_read_closed_book():
    class Ledger:
        def operations(self):
            raise AssertionError("Закрытый журнал прочитан")

    def closed():
        raise VaultError("Книга закрыта")

    with pytest.raises(VaultError):
        Analytics(Ledger(), RLock(), closed).cash_flow(PERIOD)
