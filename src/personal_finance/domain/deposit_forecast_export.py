"""Локальная выгрузка рассчитанного графика выплат вклада."""

import csv
import io
import json

from personal_finance.domain.deposits import DepositForecast
from personal_finance.domain.exporting import ExportFormat, decimal_minor


def serialize_deposit_forecast(report: DepositForecast, format: ExportFormat) -> bytes:
    scenario = report.scenario
    currency = scenario.initial.currency
    payout_dates = set(scenario.payout_dates)
    payouts = tuple(row for row in report.days if row.day in payout_dates)
    if format is ExportFormat.JSON:
        payload = {
            "format": "crown-deposit-forecast",
            "version": 1,
            "algorithm": "deposit-daily-v1",
            "opened_on": scenario.opened_on.isoformat(),
            "matures_on": scenario.matures_on.isoformat(),
            "currency": currency.code,
            "currency_exponent": currency.exponent,
            "money_encoding": "integer_minor_decimal_string",
            "interest_minor": str(report.interest_minor),
            "closing_minor": str(report.closing_minor),
            "payouts": [
                {
                    "date": item.day.isoformat(),
                    "interest_minor": str(item.payout_minor),
                    "capitalized_minor": str(item.capitalized_minor),
                    "closing_minor": str(item.closing_minor),
                }
                for item in payouts
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"

    target = io.StringIO(newline="")
    writer = csv.writer(target, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Дата прогнозной выплаты", "Валюта", "Проценты", "На вклад", "Остаток вклада"])
    for item in payouts:
        writer.writerow(
            [
                item.day.isoformat(),
                currency.code,
                *(
                    decimal_minor(minor, currency.exponent)
                    for minor in (item.payout_minor, item.capitalized_minor, item.closing_minor)
                ),
            ]
        )
    return target.getvalue().encode("utf-8-sig")
