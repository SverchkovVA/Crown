import csv
import io
import json
from dataclasses import replace

from personal_finance.domain.exporting import ExportFormat
from personal_finance.domain.investment_export import (
    prepare_portfolio_export,
    serialize_portfolio_export,
)
from personal_finance.domain.investment_portfolio import report_saved_portfolio
from personal_finance.domain.investments import TradeKind
from tests.unit.test_investment_portfolio import saved
from tests.unit.test_investments import ACCOUNT, SHARE, TODAY, trade


def portfolio():
    account = replace(ACCOUNT, name="=Учебное имя")
    security = replace(SHARE, code="+КОД")
    event = trade(1, TradeKind.BUY, 2, 600, account=account, security=security)
    return report_saved_portfolio([account], [saved(2000, account, security, [event])], TODAY)


def test_json_omits_names_by_default_and_preserves_unknown_values():
    preview = prepare_portfolio_export(portfolio(), include_names=False)
    raw = serialize_portfolio_export(preview, ExportFormat.JSON)
    result = json.loads(raw)
    (row,) = result["positions"]
    assert result["format"] == "crown-investment-portfolio"
    assert result["as_of"] == TODAY.isoformat()
    assert row["quantity"] == "2" and row["cost_minor"] == "600"
    assert row["market_minor"] is None and row["result_minor"] is None and row["price_date"] is None
    assert "account_name" not in row and "account_id" not in row
    assert ACCOUNT.id.encode() not in raw and SHARE.id.encode() not in raw


def test_csv_neutralizes_formula_like_names_and_security_codes():
    preview = prepare_portfolio_export(portfolio(), include_names=True)
    raw = serialize_portfolio_export(preview, ExportFormat.CSV)
    (row,) = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), delimiter=";")
    assert raw.startswith(b"\xef\xbb\xbf")
    assert row["Счёт"] == "'=Учебное имя"
    assert row["Инструмент"] == "'+КОД"
    assert row["Оценка"] == row["Результат"] == ""
    assert row["Себестоимость"] == "6,00"
