import json
from datetime import date
from uuid import uuid4

from personal_finance.domain.analysis_package import AnalysisPackage, serialize_analysis_package
from personal_finance.domain.analytics import ReportPeriod, income_expense_report
from personal_finance.domain.investment_portfolio import report_saved_portfolio
from personal_finance.domain.ledger import Operation, OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import Money

DAY = date(2026, 11, 12)


def uid():
    return str(uuid4())


def test_package_sums_parts_and_transfer_fees_without_disclosing_identifiers():
    account, other, first_category, second_category = (uid() for _ in range(4))
    entries = (
        OperationDraft(OperationKind.OPENING, DAY, account, Money(1000000)),
        OperationDraft(
            OperationKind.EXPENSE,
            DAY,
            account,
            Money(900),
            parts=(
                OperationPart(first_category, Money(500)),
                OperationPart(second_category, Money(400)),
            ),
            note="Скрытая проверочная строка",
        ),
        OperationDraft(
            OperationKind.TRANSFER,
            DAY,
            account,
            Money(7200),
            destination_account_id=other,
            fee=Money(21),
            fee_category_id=first_category,
        ),
    )
    flow = income_expense_report(
        tuple(Operation(uid(), uid(), item) for item in entries), ReportPeriod(DAY, DAY)
    )
    raw = serialize_analysis_package(AnalysisPackage(flow, True))
    result = json.loads(raw)
    assert result["totals"][0]["expense_minor"] == "921"
    assert result["months"][0]["difference_minor"] == "-921"
    assert sorted(int(row["expense_minor"]) for row in result["categories"]) == [400, 521]
    assert "methodology" in result
    assert "Скрытая".encode() not in raw
    for identifier in (account, other, first_category, second_category):
        assert identifier.encode() not in raw


def test_package_only_includes_optional_sections_when_selected():
    flow = income_expense_report((), ReportPeriod(DAY, DAY))
    basic = json.loads(serialize_analysis_package(AnalysisPackage(flow, False)))
    assert basic["months"] == [] and "categories" not in basic and "portfolio" not in basic
    portfolio = report_saved_portfolio([], [], DAY)
    expanded = json.loads(serialize_analysis_package(AnalysisPackage(flow, True, portfolio)))
    assert expanded["categories"] == [] and expanded["portfolio"]["currencies"] == []
    assert expanded["portfolio"]["as_of"] == DAY.isoformat()
