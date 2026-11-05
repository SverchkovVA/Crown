"""Воспроизводимые вымышленные изображения; входа пользовательских файлов нет."""

import os
from dataclasses import dataclass, field
from datetime import date, time
from functools import cache
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontInfo, QFontMetrics, QImage, QPainter

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import OcrImage
from personal_finance.domain.ocr_cards import CashDirection
from personal_finance.infrastructure.ocr.images import normalize_ocr_image


@dataclass(frozen=True)
class ExpectedRow:
    amount_minor: int
    direction: CashDirection
    occurred_on: date
    local_time: time | None = None
    currency: str = "RUB"


@dataclass(frozen=True)
class Sample:
    id: str
    kind: str
    image: OcrImage = field(repr=False)
    expected: tuple[ExpectedRow, ...]
    font: str
    expected_blocked: bool = False
    uses_year_default: bool = False
    uses_currency_default: bool = False


@cache
def _family() -> str:
    def supported(name: str) -> bool:
        metrics = QFontMetrics(QFont(name))
        return all(metrics.inFontUcs4(ord(char)) for char in "0А−₽")

    for name in ("Segoe UI", "DejaVu Sans", "Arial"):
        if supported(name):
            return name
    candidates = [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    if os.name == "nt":
        font_directory = Path(os.environ["WINDIR"]) / "Fonts"
        candidates = [font_directory / "segoeui.ttf", font_directory / "arial.ttf"]
    for path in candidates:
        if path.is_file():
            font_id = QFontDatabase.addApplicationFont(str(path))
            for name in QFontDatabase.applicationFontFamilies(font_id):
                if supported(name):
                    return name
    raise ValidationError("Не найден шрифт с нужными символами для синтетического набора.")


def _font(size: int) -> QFont:
    value = QFont(_family())
    value.setPixelSize(size)
    return value


def _amount(minor: int, direction: CashDirection, *, unsigned: bool = False) -> str:
    sign = "" if unsigned else "+" if direction == CashDirection.IN else "−"
    number = f"{minor // 100:,}".replace(",", " ") + f",{minor % 100:02d}"
    return sign + number


def _canvas(dark: bool) -> tuple[QImage, QPainter, QColor]:
    image = QImage(1080, 1800, QImage.Format.Format_RGB32)
    image.fill(QColor("#191b20" if dark else "#f7f8fa"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    foreground = QColor("#f4f5f7" if dark else "#18202a")
    return image, painter, foreground


def _text(painter: QPainter, color: QColor, x: int, y: int, text: str, size: int) -> None:
    painter.setPen(color)
    painter.setFont(_font(size))
    painter.drawText(x, y, text)


def card_sample(
    name: str,
    *,
    dark: bool,
    incoming: bool = False,
    unsigned: bool = False,
    account_only: bool = False,
) -> Sample:
    image, painter, color = _canvas(dark)
    direction = CashDirection.IN if incoming else CashDirection.OUT
    try:
        _text(painter, color, 40, 50, "05:10 P", 24)
        _text(painter, color, 40, 170, "Детали операции", 40)
        _text(painter, color, 40, 300, "Учебный пример", 32)
        if not account_only:
            _text(
                painter, color, 260, 470, _amount(12345, direction, unsigned=unsigned) + " RUB", 64
            )
        _text(painter, color, 40, 690, "Счёт зачисления" if incoming else "Счёт списания", 28)
        _text(painter, color, 40, 750, "Тестовый счёт", 32)
        _text(painter, color, 40, 820, "Баланс: 9 876,54 RUB", 28)
        _text(painter, color, 40, 1060, "Дата и время", 28)
        _text(painter, color, 40, 1120, "23.10.2026 в 19:17", 34)
        _text(painter, color, 40, 1310, "Описание: Учебный пример", 30)
    finally:
        painter.end()
    expected = (
        () if account_only else (ExpectedRow(12345, direction, date(2026, 10, 23), time(19, 17)),)
    )
    return Sample(
        name,
        "card",
        normalize_ocr_image(image),
        expected,
        QFontInfo(_font(34)).family(),
        expected_blocked=account_only,
    )


def history_sample(
    name: str,
    *,
    dark: bool,
    size: int = 34,
    mixed: bool = False,
    yearless: bool = False,
    currency_default: bool = False,
    clock_only: bool = False,
) -> Sample:
    image, painter, color = _canvas(dark)
    values = (14750, 36075, 8901, 19900, 27832, 115040)
    flows = (
        CashDirection.OUT,
        CashDirection.IN,
        CashDirection.OUT,
        CashDirection.OUT,
        CashDirection.OUT,
        CashDirection.IN,
    )
    titles = (
        "Тестовый магазин",
        "Учебное поступление",
        "Тестовое кафе",
        "Учебная подписка",
        "Тестовые товары",
        "Учебный перевод",
    )
    expected = []
    try:
        _text(painter, color, 40, 50, "05:10 P", 24)
        _text(painter, color, 40, 170, "История операций", 40)
        for group, day in enumerate((24, 23)):
            y = 260 + group * 700
            heading = f"{day} октября" + ("" if yearless else " 2026")
            _text(painter, color, 40, y, heading, 30)
            if not clock_only:
                # Итог дня — сумма в заголовке даты, а не отдельная операция.
                _text(painter, color, 730, y, "−499,99 RUB", 26)
            if clock_only:
                continue
            for offset in range(3):
                index = group * 3 + offset
                baseline = y + 110 + offset * 180
                row_color = color
                if mixed:
                    painter.fillRect(25, baseline - 60, 1030, 135, QColor("#ffffff"))
                    row_color = QColor("#18202a")
                _text(painter, row_color, 40, baseline, titles[index], size)
                currency = "" if currency_default else " RUB"
                money_color = QColor("#02864f") if flows[index] == CashDirection.IN else row_color
                _text(
                    painter,
                    money_color,
                    730,
                    baseline,
                    _amount(values[index], flows[index]) + currency,
                    size,
                )
                _text(painter, row_color, 40, baseline + 47, "Учебная категория", 24)
                if index == 0:
                    _text(painter, row_color, 750, baseline + 47, "+1 бонус", 22)
                expected.append(ExpectedRow(values[index], flows[index], date(2026, 10, day)))
        _text(painter, color, 40, 1720, "Главная    История    Профиль", 26)
    finally:
        painter.end()
    return Sample(
        name,
        "history",
        normalize_ocr_image(image),
        tuple(expected),
        QFontInfo(_font(size)).family(),
        expected_blocked=clock_only,
        uses_year_default=yearless,
        uses_currency_default=currency_default,
    )


def samples() -> tuple[Sample, ...]:
    return (
        card_sample("card-light", dark=False),
        card_sample("card-dark", dark=True),
        card_sample("card-income-dark", dark=True, incoming=True),
        card_sample("card-unsigned-label", dark=False, unsigned=True),
        history_sample("history-light", dark=False),
        history_sample("history-dark", dark=True),
        history_sample("history-small-text", dark=False, size=28),
        history_sample("history-large-text", dark=True, size=38),
        history_sample("history-mixed-theme", dark=True, mixed=True),
        history_sample("history-defaults", dark=True, yearless=True, currency_default=True),
        card_sample("control-account-only", dark=True, account_only=True),
        history_sample("control-clock-only", dark=True, clock_only=True),
    )
