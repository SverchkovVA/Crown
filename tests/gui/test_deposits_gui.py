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


def changed_scenario(qtbot):
    form = DepositScenarioDialog()
    qtbot.addWidget(form)
    form.initial.setText("100,00")
    form.rate.setText("365")
    form.start.setDate(QDate(2026, 1, 1))
    form.end.setDate(QDate(2026, 1, 4))
    form.day_count.setCurrentIndex(1)
    form.allow_topups.setChecked(True)
    form.allow_withdrawals.setChecked(True)
    form._add_event(form.rate_table)
    form.rate_table.cellWidget(0, 0).setDate(QDate(2026, 1, 3))
    form.rate_table.cellWidget(0, 1).setText("730")
    for day, text in [(2, "+100,00"), (4, "−25,00")]:
        form._add_event(form.movement_table)
        row = form.movement_table.rowCount() - 1
        form.movement_table.cellWidget(row, 0).setDate(QDate(2026, 1, day))
        form.movement_table.cellWidget(row, 1).setText(text)
    return form


@pytest.mark.parametrize("moment,interest", [(0, 950), (1, 900)])
def test_changed_rates_and_movements_exact_projection(qtbot, moment, interest):
    form = changed_scenario(qtbot)
    form.balance_moment.setCurrentIndex(moment)
    form._accept()
    value = form.result_scenario
    assert value is not None, form.feedback.text()
    assert len(value.rates) == 2 and len(value.movements) == 2
    result = d.forecast_deposit(value)
    assert result.interest_minor == interest and result.closing_minor == 17500
    view = DepositForecastDialog(result)
    qtbot.addWidget(view)
    assert view.rate_table.model().rowCount() == 2 and view.movement_table.model().rowCount() == 2
    assert view.movement_table.model().data(view.movement_table.model().index(1, 1)) == "−25,00"
    assert view.tabs.count() == 2 and "Сценарных движений: 2" in view.summary.text()


@pytest.mark.parametrize(
    "table_name,bad",
    [
        ("rate_table", "NaN"),
        ("rate_table", "0.000000001"),
        ("movement_table", "0"),
        ("movement_table", "100,001"),
        ("movement_table", "не сумма"),
    ],
)
def test_changed_rows_invalid_keep_both_tables(qtbot, table_name, bad):
    form = changed_scenario(qtbot)
    table = getattr(form, table_name)
    table.cellWidget(0, 1).setText(bad)
    form._accept()
    assert form.result_scenario is None and form.feedback.text()
    assert table.cellWidget(0, 1).text() == bad
    assert form.rate_table.rowCount() == 1 and form.movement_table.rowCount() == 2


def test_permissions_and_minimum_are_enforced_without_losing_movements(qtbot):
    form = changed_scenario(qtbot)
    form.allow_topups.setChecked(False)
    form._accept()
    assert form.result_scenario is None and "пополнение" in form.feedback.text()
    form.allow_topups.setChecked(True)
    form.allow_withdrawals.setChecked(False)
    form._accept()
    assert form.result_scenario is None and "снятие" in form.feedback.text()
    form.allow_withdrawals.setChecked(True)
    form.minimum.setText("176,00")
    form._accept()
    assert form.result_scenario is None
    form.minimum.setText("50,00")
    form._accept()
    assert form.result_scenario.minimum.minor == 5000
    assert d.forecast_deposit(form.result_scenario).closing_minor == 17500


def test_duplicate_date_after_opening_and_wrong_movement_date_rejected(qtbot):
    form = changed_scenario(qtbot)
    editor = form.rate_table.cellWidget(0, 0)
    editor.setDate(form.start.date())
    form._accept()
    assert form.result_scenario is None
    editor.setDate(QDate(2026, 1, 3))
    form.movement_table.cellWidget(0, 0).setDate(QDate(2025, 12, 31))
    form._accept()
    assert form.result_scenario is None and form.movement_table.rowCount() == 2


def test_delete_rows_and_row_ids_stay_stable_when_error_corrected(qtbot):
    from PySide6.QtWidgets import QPushButton

    form = changed_scenario(qtbot)
    ids = [form.movement_table.cellWidget(i, 0).property("recordId") for i in range(2)]
    form.rate_table.cellWidget(0, 1).setText("NaN")
    form._accept()
    assert [form.movement_table.cellWidget(i, 0).property("recordId") for i in range(2)] == ids
    form.movement_table.selectRow(1)
    remove = next(
        b
        for b in form.findChildren(QPushButton)
        if b.accessibleName() == "Сценарные пополнения и снятия: удалить выбранную строку"
    )
    assert remove.isEnabled()
    remove.click()
    assert form.movement_table.rowCount() == 1
    assert form.movement_table.cellWidget(0, 0).property("recordId") == ids[0]
    form.rate_table.cellWidget(0, 1).setText("730")
    form._accept()
    assert len(form.result_scenario.movements) == 1


def test_editor_focus_selects_same_row_for_delete(qtbot):
    from PySide6.QtWidgets import QPushButton

    form = changed_scenario(qtbot)
    form.tabs.setCurrentIndex(1)
    form.show()
    field = form.movement_table.cellWidget(0, 1)
    form.tabs.widget(1).ensureWidgetVisible(field)
    qtbot.mouseClick(field, Qt.MouseButton.LeftButton)
    assert form.movement_table.currentRow() == 0
    remove = next(
        b
        for b in form.findChildren(QPushButton)
        if b.accessibleName() == "Сценарные пополнения и снятия: удалить выбранную строку"
    )
    assert remove.text() == "Удалить строку 1"
    original_second = form.movement_table.cellWidget(1, 0).property("recordId")
    remove.click()
    assert form.movement_table.rowCount() == 1
    assert form.movement_table.cellWidget(0, 0).property("recordId") == original_second
