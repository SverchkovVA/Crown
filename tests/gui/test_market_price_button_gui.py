from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from PySide6.QtCore import QDate

from personal_finance.application.market_price_refresh import BookQuoteAccess
from personal_finance.application.vault import create_vault, open_vault
from personal_finance.domain.investments import TradeKind
from personal_finance.domain.models import AccountKind
from personal_finance.presentation.investment_portfolio import (
    InvestmentPortfolioDialog,
    InvestmentPortfolioOptions,
)
from personal_finance.presentation.main_window import MainWindow
from tests.conftest import PASSWORD
from tests.gui.test_planning_scenarios_workflow import current
from tests.integration.test_market_quote_lifecycle import quote_schema as quote_schema
from tests.unit.test_investment_positions import inputs
from tests.unit.test_investments import BOND, trade
from tests.unit.test_market_price_refresh import DEBT, STOCK, stock


def show_portfolio(window, qtbot, day):
    window._report_forms.clear()
    window.navigation.setCurrentRow(2)
    window.investment_portfolio_action.trigger()
    options = current(window, InvestmentPortfolioOptions)
    options.as_of.setDate(QDate(day.year, day.month, day.day))
    options.calculate.click()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    return current(window, InvestmentPortfolioDialog)


def test_price_refresh_is_explicit_and_keeps_portfolio_open(tmp_path, qtbot, quote_schema):
    day = date.today()
    booked_on = day - timedelta(days=2)
    path = tmp_path / "market.pfvault"
    session, _ = create_vault(path, "Синтетический портфель", PASSWORD)
    cash = session.catalog.add_account("Учебный инвестиционный счёт", AccountKind.INVESTMENT)
    trade_ = trade(1, TradeKind.BUY, 2, 1000, account=cash, day=booked_on)
    holding = session.investment_positions.save(replace(inputs(cash), events=(trade_,), prices=()))
    ledger = session.ledger.operations(), session.ledger.balances()
    calls = []

    def provider(codes):
        calls.append(codes)
        yield stock(value=Decimal("12.34"), quoted_on=day, quoted_at=datetime.now(UTC))
        raise OSError("synthetic")

    window = MainWindow(
        market_quote_access=BookQuoteAccess(session.book_id, provider, (STOCK, DEBT))
    )
    qtbot.addWidget(window)
    window.show()
    window.activate_session(session)
    try:
        past = show_portfolio(window, qtbot, day - timedelta(days=1))
        assert past.refresh_prices.isHidden() and not calls
        report = show_portfolio(window, qtbot, day)
        assert report.refresh_prices.isVisible()
        assert report.report.rows[0].market_minor is None and calls == []
        report.positions.selectRow(0)
        report.refresh_prices.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        assert calls == [(STOCK.instrument,)]
        assert current(window, InvestmentPortfolioDialog) is report
        assert report.positions.selectionModel().selectedRows()[0].row() == 0
        assert report.details.isEnabled()
        assert report.positions.model().item(0, 4).text() == "24,68 ₽"
        assert "Цены обновлены: 1" in window._report_forms.notice.text()
        assert session.investment_positions.get(holding.id) == holding
        assert (session.ledger.operations(), session.ledger.balances()) == ledger

        window.lock_book()
        reopened = open_vault(path, PASSWORD)
        window.activate_session(reopened)
        assert show_portfolio(window, qtbot, day).report.rows[0].market_minor == 2468
        assert calls == [(STOCK.instrument,)]

        elsewhere, _ = create_vault(tmp_path / "another.pfvault", "Другая учебная книга", PASSWORD)
        other = elsewhere.catalog.add_account("Другой учебный счёт", AccountKind.INVESTMENT)
        elsewhere.investment_positions.save(
            replace(inputs(other), events=(replace(trade_, account_id=other.id),), prices=())
        )
        window.activate_session(elsewhere)
        separate = show_portfolio(window, qtbot, day)
        assert separate.refresh_prices.isHidden()
        assert separate.report.rows[0].market_minor is None
        assert calls == [(STOCK.instrument,)]
    finally:
        window.lock_book()


def test_partial_refresh_keeps_usable_prices_and_explains_missing_quotes(
    tmp_path, qtbot, quote_schema
):
    day = date.today()
    session, _ = create_vault(tmp_path / "partial.pfvault", "Учебный частичный ответ", PASSWORD)
    cash = session.catalog.add_account("Синтетический счёт", AccountKind.INVESTMENT)
    for seq, security in enumerate((STOCK.security, BOND), 1):
        buy = trade(
            seq,
            TradeKind.BUY,
            2,
            1000,
            account=cash,
            security=security,
            day=day,
            accrued=0 if security == BOND else None,
        )
        session.investment_positions.save(
            replace(inputs(cash), security=security, events=(buy,), prices=())
        )
    calls = []

    def provider(codes):
        calls.append(codes)
        yield stock(quoted_on=day, quoted_at=None)
        raise OSError("synthetic")

    window = MainWindow(
        market_quote_access=BookQuoteAccess(session.book_id, provider, (STOCK, DEBT))
    )
    qtbot.addWidget(window)
    window.show()
    window.activate_session(session)
    try:
        report = show_portfolio(window, qtbot, day)
        for attempt in range(2):
            report.refresh_prices.click()
            qtbot.waitUntil(lambda: not window._busy, timeout=15000)
            assert calls == [(STOCK.instrument, DEBT.instrument)] * (attempt + 1)
            assert report.report.totals[0].priced_market_minor == 2468
            assert report.report.totals[0].unpriced_count == 1
            message = window._report_forms.notice.text()
            assert "Нет подходящего ответа: 1" in message
            assert f"Уже актуальны: {attempt}" in message
            tooltips = {
                row.position.security.id: report.positions.model().item(index, 4).toolTip()
                for index, row in enumerate(report.report.rows)
            }
            assert "Источник недоступен" in tooltips[DEBT.security.id]
            assert "Источник example" in tooltips[STOCK.security.id]
            status = "Цена сохранена" if not attempt else "Такая цена уже сохранена"
            assert status in tooltips[STOCK.security.id]
    finally:
        window.lock_book()
