from datetime import date
from decimal import Decimal

import pytest
from PySide6.QtCore import QDate, Qt

import personal_finance.domain.deposits as d
from personal_finance.domain.errors import ValidationError
from personal_finance.presentation.deposits import (
    DepositForecastDialog,
    DepositScenarioDialog,
    monthly_payouts,
)


def fill(dialog):
    dialog.initial.setText("0,01")
    dialog.rate.setText("50")
    dialog.start.setDate(QDate(2026, 1, 1))
    dialog.end.setDate(QDate(2027, 1, 1))
    dialog.boundary.setCurrentIndex(1)
    dialog.day_count.setCurrentIndex(1)


def test_form_exact_policy_data_and_result(qtbot):
    form = DepositScenarioDialog()
    qtbot.addWidget(form)
    fill(form)
    form._accept()
    value = form.result_scenario
    assert value is not None and value.initial.minor == 1
    assert value.rates[0].annual_percent == Decimal(50)
    assert value.boundary is d.AccrualBoundary.OPENING_INCLUDED
    assert value.day_count is d.DayCount.FIXED_365
    report = d.forecast_deposit(value)
    assert report.interest_minor == 1
    result = DepositForecastDialog(report)
    qtbot.addWidget(result)
    assert result.model.rowCount() == 1
    assert result.model.data(result.model.index(0, 1)) == "0,01"
    assert result.model.data(result.model.index(0, 2)) == "0,00"
    assert result.model.data(result.model.index(0, 3)) == "0,01"
    assert "Прогноз процентов: 0,01 RUB" in result.summary.text()
    assert result.summary.textFormat() == Qt.TextFormat.PlainText
    assert "Половина вверх" in result.assumptions.text()


@pytest.mark.parametrize(
    "amount,rate",
    [
        ("", "5"),
        ("0", "5"),
        ("-1", "5"),
        ("1.001", "5"),
        ("100", ""),
        ("100", "NaN"),
        ("100", "Infinity"),
        ("100", "-1"),
        ("100", "0.000000001"),
        ("100", "1e999999"),
    ],
)
def test_form_errors_preserve_inputs(qtbot, amount, rate):
    form = DepositScenarioDialog()
    qtbot.addWidget(form)
    fill(form)
    form.initial.setText(amount)
    form.rate.setText(rate)
    form._accept()
    assert form.result_scenario is None
    assert form.initial.text() == amount and form.rate.text() == rate
    assert form.feedback.text() and not form.feedback.isHidden()


def test_invalid_dates_then_correction(qtbot):
    form = DepositScenarioDialog()
    qtbot.addWidget(form)
    fill(form)
    form.end.setDate(form.start.date())
    form._accept()
    assert form.result_scenario is None and "Срок" in form.feedback.text()
    form.end.setDate(QDate(2027, 1, 1))
    form._accept()
    assert form.result_scenario is not None


@pytest.mark.parametrize(
    "start,end,expected",
    [
        (
            date(2023, 1, 31),
            date(2023, 4, 15),
            (date(2023, 2, 28), date(2023, 3, 31), date(2023, 4, 15)),
        ),
        (date(2024, 1, 31), date(2024, 3, 31), (date(2024, 2, 29), date(2024, 3, 31))),
        (date(2026, 1, 1), date(2026, 1, 2), (date(2026, 1, 2),)),
        (date(9999, 11, 30), date.max, (date(9999, 12, 30), date.max)),
    ],
)
def test_monthly_dates_keep_anchor_and_final(start, end, expected):
    assert monthly_payouts(start, end) == expected


def test_monthly_schedule_in_form_and_zero_payouts(qtbot):
    form = DepositScenarioDialog()
    qtbot.addWidget(form)
    fill(form)
    form.start.setDate(QDate(2024, 1, 31))
    form.end.setDate(QDate(2024, 3, 31))
    form.frequency.setCurrentIndex(1)
    form.rate.setText("0")
    form._accept()
    value = form.result_scenario
    assert value.payout_dates == (date(2024, 2, 29), date(2024, 3, 31))
    result = DepositForecastDialog(d.forecast_deposit(value))
    qtbot.addWidget(result)
    assert result.model.rowCount() == 2 and all(v.payout_minor == 0 for v in result.payouts)


def test_invalid_monthly_dates_rejected():
    with pytest.raises(ValidationError):
        monthly_payouts(date(2026, 1, 1), date(2026, 1, 1))


def test_russian_rate_caption_and_zero_exponent_safe(qtbot):
    form = DepositScenarioDialog()
    qtbot.addWidget(form)
    fill(form)
    form.rate.setText("12,5")
    form._accept()
    result = DepositForecastDialog(d.forecast_deposit(form.result_scenario))
    qtbot.addWidget(result)
    assert "Ставка: 12,5 % годовых" in result.assumptions.text()
    form.rate.setText("0e-1000000000")
    form._accept()
    result = DepositForecastDialog(d.forecast_deposit(form.result_scenario))
    qtbot.addWidget(result)
    assert "Ставка: 0 % годовых" in result.assumptions.text()
    assert len(result.assumptions.text()) < 500
