"""Отдельные вымышленные макеты: фиксируются до первого запуска OCR."""

import hashlib
import os
from dataclasses import dataclass
from datetime import date, time
from functools import cache
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontInfo, QFontMetrics, QImage, QPainter

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr_cards import CashDirection
from personal_finance.infrastructure.ocr.images import normalize_ocr_image
from tools.ocr_benchmark_samples import ExpectedRow, Sample

CORPUS_ID = "synthetic-holdout-v1"


@dataclass(frozen=True)
class Spec:
    id: str
    kind: str
    profile: str = "sans"
    dark: bool = False
    layout: str = "right"
    amount: int = 872316
    incoming: bool = False
    unsigned: bool = False
    yearless: bool = False
    currency: str = "RUB"


SPECS = (
    Spec("card-numeric-sans", "card", layout="below"),
    Spec("card-serif-dark", "card", "serif", True, "below", 7315, True, currency="₽"),
    Spec("card-mono-integer", "card", "mono", amount=250000, incoming=True),
    Spec("card-account-label", "card", dark=True, unsigned=True, amount=45983),
    Spec("card-yearless-heading", "card", "serif", yearless=True, amount=183504),
    Spec("history-sans-right", "history"),
    Spec("history-serif-dark", "history", "serif", True),
    Spec("history-mono-left", "history", "mono", layout="left"),
    Spec("history-compact", "history", layout="compact", yearless=True, currency=""),
    Spec("history-wrapped", "history", dark=True, layout="wrapped"),
    Spec("history-mixed", "history", "serif", True, "mixed"),
    Spec("control-clock", "control", layout="clock"),
    Spec("control-bonuses", "control", dark=True, layout="bonuses"),
    Spec("control-summary", "control", layout="summary"),
)


@dataclass(frozen=True)
class FontSource:
    family: str
    filename: str
    sha256: str


def _candidates(profile: str) -> tuple[Path, ...]:
    if os.name == "nt":
        folder = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        return (
            folder / {"sans": "arial.ttf", "serif": "times.ttf", "mono": "consola.ttf"}[profile],
        )
    folder = Path("/usr/share/fonts/truetype/dejavu")
    return (
        folder
        / {"sans": "DejaVuSans.ttf", "serif": "DejaVuSerif.ttf", "mono": "DejaVuSansMono.ttf"}[
            profile
        ],
    )


@cache
def font_source(profile: str) -> FontSource:
    for path in _candidates(profile):
        if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        for family in QFontDatabase.applicationFontFamilies(font_id):
            metrics = QFontMetrics(QFont(family))
            if all(metrics.inFontUcs4(ord(char)) for char in "0А−₽"):
                return FontSource(family, path.name, hashlib.sha256(path.read_bytes()).hexdigest())
    raise ValidationError("Нет подходящего локального шрифта контрольной выборки.")


def _font(spec: Spec, pixels: int, *, bold: bool = False) -> QFont:
    value = QFont(font_source(spec.profile).family)
    value.setPixelSize(pixels)
    if bold:
        value.setWeight(QFont.Weight.DemiBold)
    return value


def _draw(
    painter: QPainter,
    spec: Spec,
    text: str,
    x: int,
    y: int,
    size: int,
    color: QColor,
    *,
    bold: bool = False,
) -> None:
    painter.setFont(_font(spec, size, bold=bold))
    painter.setPen(color)
    painter.drawText(x, y, text)


def _money(minor: int, direction: CashDirection, currency: str, *, unsigned: bool = False) -> str:
    sign = "" if unsigned else "+" if direction == CashDirection.IN else "−"
    number = f"{minor // 100:,}".replace(",", "\u202f")
    if minor % 100:
        number += f",{minor % 100:02d}"
    return sign + number + (" " + currency if currency else "")


def _card(spec: Spec, painter: QPainter, foreground: QColor) -> tuple[ExpectedRow, ...]:
    flow = CashDirection.IN if spec.incoming else CashDirection.OUT
    _draw(painter, spec, "Детали операции", 35, 145, 38, foreground)
    _draw(painter, spec, "Учебный платёж", 35, 285, 30, foreground)
    if spec.yearless:
        _draw(painter, spec, "27 октября, 15:42", 35, 220, 28, foreground)
    value = _money(spec.amount, flow, spec.currency, unsigned=spec.unsigned)
    width = QFontMetrics(_font(spec, 58, bold=True)).horizontalAdvance(value)
    _draw(painter, spec, value, (900 - width) // 2, 460, 58, foreground, bold=True)
    _draw(
        painter,
        spec,
        "Карта зачисления" if spec.incoming else "Счёт списания",
        35,
        720,
        28,
        foreground,
    )
    _draw(painter, spec, "Учебный счёт", 35, 780, 28, foreground)
    _draw(painter, spec, "Баланс: 20 110,00 RUB", 35, 840, 24, foreground)
    if not spec.yearless:
        _draw(painter, spec, "Дата и время", 35, 1020, 26, foreground)
        _draw(painter, spec, "27.10.2026 в 15:42", 35, 1075, 30, foreground)
    _draw(painter, spec, "Описание: Синтетический пример", 35, 1280, 26, foreground)
    return (ExpectedRow(spec.amount, flow, date(2026, 10, 27), time(15, 42)),)


def _history(
    spec: Spec, painter: QPainter, foreground: QColor, width: int
) -> tuple[ExpectedRow, ...]:
    size = 24 if spec.layout == "compact" else 30
    _draw(painter, spec, "История операций", 35, 145, 36, foreground)
    values = (73219, 149087, 250604, 60835, 174200, 92371)
    flows = (
        CashDirection.OUT,
        CashDirection.IN,
        CashDirection.OUT,
        CashDirection.OUT,
        CashDirection.IN,
        CashDirection.OUT,
    )
    expected = []
    for group, day in enumerate((27, 25)):
        heading_y = 240 + group * 760
        heading = f"{day} октября" + ("" if spec.yearless else " 2026")
        _draw(painter, spec, heading, 35, heading_y, 28, foreground)
        _draw(painter, spec, "−725,50 RUB", width - 210, heading_y, 23, foreground)
        for offset in range(3):
            index = group * 3 + offset
            baseline = heading_y + 110 + offset * 180
            color = foreground
            if spec.layout == "mixed":
                painter.fillRect(20, baseline - 52, width - 40, 138, QColor("#ffffff"))
                color = QColor("#20262e")
            title_x = 330 if spec.layout == "left" else 35
            if spec.layout == "wrapped":
                _draw(painter, spec, "Учебный", title_x, baseline - 20, size, color)
                _draw(painter, spec, f"магазин {index + 1}", title_x, baseline + 20, size, color)
            else:
                _draw(painter, spec, f"Учебный магазин {index + 1}", title_x, baseline, size, color)
            money = _money(values[index], flows[index], spec.currency)
            amount_width = QFontMetrics(_font(spec, size)).horizontalAdvance(money)
            amount_x = 35 if spec.layout == "left" else width - 30 - amount_width
            money_color = (
                QColor("#249fce" if spec.dark and spec.layout != "mixed" else "#087b55")
                if flows[index] == CashDirection.IN
                else color
            )
            _draw(painter, spec, money, amount_x, baseline, size, money_color)
            _draw(painter, spec, "Учебная категория", title_x, baseline + 65, 20, color)
            if index == 0:
                _draw(painter, spec, "+2 бонуса", width - 180, baseline + 65, 20, color)
            expected.append(ExpectedRow(values[index], flows[index], date(2026, 10, day)))
    _draw(painter, spec, "Главная    История    Профиль", 35, 1730, 24, foreground)
    return tuple(expected)


def _control(spec: Spec, painter: QPainter, foreground: QColor) -> tuple[ExpectedRow, ...]:
    title = (
        "Сводка за день"
        if spec.layout == "summary"
        else "Бонусы"
        if spec.layout == "bonuses"
        else "История операций"
    )
    _draw(painter, spec, title, 35, 145, 36, foreground)
    _draw(painter, spec, "27 октября 2026", 35, 240, 28, foreground)
    labels = {
        "clock": ("05:10 RUB", "19:42:30 P"),
        "bonuses": ("Начислено +17 бонусов", "Кешбэк 5%"),
        "summary": ("Траты −5 009,00 RUB", "Доходы +5 600,00 RUB"),
    }[spec.layout]
    for index, label in enumerate(labels):
        _draw(painter, spec, label, 35, 420 + index * 180, 34, foreground)
    return ()


def make_sample(spec: Spec) -> Sample:
    width = 720 if spec.layout == "compact" else 900
    height = 1600 if spec.kind == "card" else 1800
    pixels = QImage(width, height, QImage.Format.Format_RGB32)
    pixels.fill(QColor("#131821" if spec.dark else "#ffffff"))
    painter = QPainter(pixels)
    foreground = QColor("#f4f6f8" if spec.dark else "#20262e")
    try:
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        _draw(painter, spec, "05:10 P", 35, 50, 23, foreground)
        if spec.kind == "card":
            expected = _card(spec, painter, foreground)
        elif spec.kind == "history":
            expected = _history(spec, painter, foreground, width)
        else:
            expected = _control(spec, painter, foreground)
    finally:
        painter.end()
    return Sample(
        spec.id,
        "card" if spec.kind == "card" else "history",
        normalize_ocr_image(pixels),
        expected,
        QFontInfo(_font(spec, 30)).family(),
        expected_blocked=spec.kind == "control",
        uses_year_default=spec.yearless,
        uses_currency_default=not spec.currency,
    )


def samples() -> tuple[Sample, ...]:
    return tuple(make_sample(spec) for spec in SPECS)
