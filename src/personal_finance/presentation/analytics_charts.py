"""Графики неизменяемого отчёта: целые суммы, раздельные валюты, без записи."""

from collections.abc import Sequence
from datetime import date
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# isort: split

# PySide6 выбран до импорта QtAgg; pyplot и глобальное переключение backend не нужны.
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from personal_finance.domain.analytics import CategoryFlow, IncomeExpenseReport, MonthlyFlow
from personal_finance.presentation.analytics import format_report_amount


class IncomeExpenseCharts(QWidget):
    sources_requested = Signal(str, object, object)
    MONTHS_PER_PAGE = 6
    CATEGORIES_PER_PAGE = 8

    def __init__(
        self, report: IncomeExpenseReport, names: dict[str, str], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.report = report
        self._names = dict(names)
        self._currencies = {v.currency.code: v.currency for v in report.totals}
        self._page = 0
        self._disposed = False
        self._targets: dict[object, tuple[str | None, date | None]] = {}
        self.plotted_rows: tuple[MonthlyFlow | CategoryFlow, ...] = ()
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Валюта"))
        self.currency = QComboBox()
        self.currency.setAccessibleName("Валюта графика")
        for code in self._currencies:
            self.currency.addItem(code, code)
        controls.addWidget(self.currency)
        self.kind = QComboBox()
        self.kind.addItem("Доходы и расходы по месяцам", "months")
        self.kind.addItem("Расходы по категориям", "categories")
        self.kind.setAccessibleName("Вид графика")
        controls.addWidget(self.kind, 1)
        self.previous = QPushButton("Назад")
        self.next = QPushButton("Дальше")
        self.page_label = QLabel()
        controls.addWidget(self.previous)
        controls.addWidget(self.page_label)
        controls.addWidget(self.next)
        layout.addLayout(controls)
        self.figure = Figure(layout="constrained", facecolor="#f5f7f9")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setAccessibleName("График доходов и расходов")
        self.canvas.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.canvas.setMinimumSize(800, 180)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.canvas)
        layout.addWidget(scroll, 1)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self._pick_connection = self.canvas.mpl_connect("pick_event", self._picked)
        self.currency.currentIndexChanged.connect(self._reset_page)
        self.kind.currentIndexChanged.connect(self._reset_page)
        self.previous.clicked.connect(lambda: self._change_page(-1))
        self.next.clicked.connect(lambda: self._change_page(1))
        self._render()

    def _reset_page(self) -> None:
        self._page = 0
        self._render()

    def _change_page(self, step: int) -> None:
        self._page += step
        self._render()

    def _rows(self) -> Sequence[MonthlyFlow | CategoryFlow]:
        code = self.currency.currentData()
        if self.kind.currentData() == "months":
            return [v for v in self.report.months if v.totals.currency.code == code]
        categories = [
            v
            for v in self.report.categories
            if v.totals.currency.code == code
            and (v.totals.expense_minor != 0 or v.totals.income_minor == 0)
        ]
        categories.sort(key=lambda v: self._names.get(v.category_id, "").casefold())
        return categories

    def _render(self) -> None:
        if self._disposed:
            return
        rows = self._rows()
        monthly = self.kind.currentData() == "months"
        size = self.MONTHS_PER_PAGE if monthly else self.CATEGORIES_PER_PAGE
        pages = max(1, (len(rows) + size - 1) // size)
        self._page = max(0, min(self._page, pages - 1))
        self.plotted_rows = tuple(rows[self._page * size : (self._page + 1) * size])
        self.canvas.setMinimumHeight(
            max(180, len(self.plotted_rows) * (52 if monthly else 40) + 40)
        )
        self.page_label.setText(f"{self._page + 1} из {pages}")
        self.previous.setEnabled(self._page > 0)
        self.next.setEnabled(self._page + 1 < pages)
        self.currency.setEnabled(bool(self._currencies))
        self._targets.clear()
        self.figure.clear()
        self.axes = self.figure.add_subplot()
        self.axes.set_facecolor("#ffffff")
        self.axes.tick_params(colors="#172b35")
        self.axes.axvline(0, color="#526b79", linewidth=0.8)
        self.axes.set_xticks([])
        code = self.currency.currentData()
        currency = self._currencies.get(code)
        if not self.plotted_rows or currency is None:
            self.axes.set_yticks([])
            self.message.setText("Для этого вида графика нет данных в выбранной валюте.")
            self.canvas.draw_idle()
            return
        labels = [
            f"{v.month:%m.%Y}"
            if isinstance(v, MonthlyFlow)
            else self._short_name(self._names.get(v.category_id, "Недоступная категория"))
            for v in self.plotted_rows
        ]
        self.axes.set_yticks(range(len(labels)), labels, parse_math=False)
        self.axes.invert_yaxis()
        series = (
            [
                ("income_minor", -0.19, "Доходы", "#17694f"),
                ("expense_minor", 0.19, "Расходы с возвратами", "#155c9b"),
            ]
            if monthly
            else [("expense_minor", 0, "Расходы с возвратами", "#155c9b")]
        )
        all_amounts = [getattr(v.totals, field) for v in rows for field, *_ in series]
        maximum = max(abs(v) for v in all_amounts)
        # Ограниченные целые координаты обходят предел NumPy int64. Суммы и подписи
        # остаются исходными int; округление картинки не возвращается в отчёт.
        self.coordinate_divisor = 10 ** max(0, len(str(maximum)) - 9)

        def coordinate(minor: int) -> int:
            value = abs(minor) // self.coordinate_divisor
            return -value if minor < 0 else value

        all_coordinates = [coordinate(v) for v in all_amounts]
        low, high = min(0, *all_coordinates), max(0, *all_coordinates)
        padding = max(1, (high - low) // 3)
        self.axes.set_xlim(low - padding, high + padding)
        for field, shift, title, color in series:
            amounts = [getattr(v.totals, field) for v in self.plotted_rows]
            # Matplotlib получает int; координаты картинки не участвуют в расчёте денег.
            bars = self.axes.barh(
                [i + shift for i in range(len(labels))],
                [coordinate(v) for v in amounts],
                height=0.34 if monthly else 0.65,
                label=title,
                color=color,
                picker=True,
            )
            for bar, value, minor in zip(bars, self.plotted_rows, amounts, strict=True):
                target = (
                    (None, value.month)
                    if isinstance(value, MonthlyFlow)
                    else (value.category_id, None)
                )
                self._targets[bar] = target
                if minor < 0:
                    bar.set_hatch("//")
                self.axes.annotate(
                    format_report_amount(minor, currency),
                    (bar.get_width(), bar.get_y() + bar.get_height() / 2),
                    xytext=(5 if minor >= 0 else -5, 0),
                    textcoords="offset points",
                    ha="left" if minor >= 0 else "right",
                    va="center",
                    color="#172b35",
                    fontsize=9,
                    parse_math=False,
                )
        self.axes.margins(y=0.12)
        if monthly:
            self.axes.legend(loc="upper center", bbox_to_anchor=(0.5, 1.16), ncol=2)
        self.message.setText(
            f"Суммы в {code}. Столбец открывает операции месяца или категории. "
            "Отрицательный расход означает превышение возвратов. "
            "Точные суммы и полные названия доступны в таблицах; переход с клавиатуры — там же."
        )
        self.canvas.setAccessibleDescription(self.message.text())
        self.canvas.draw_idle()

    @staticmethod
    def _short_name(name: str) -> str:
        text = name.replace("\n", " ")
        return text if len(text) <= 34 else text[:31] + "…"

    def _picked(self, event: Any) -> None:
        if self._disposed or event.artist not in self._targets:
            return
        category, month = self._targets[event.artist]
        self.sources_requested.emit(self.currency.currentData(), category, month)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self.canvas.mpl_disconnect(self._pick_connection)
        self._targets.clear()
        self.figure.clear()
        self.canvas.close()
