"""Месячные остатки одного снимка: таблица, график и источники."""

from calendar import monthrange
from datetime import date
from typing import Any

from PySide6.QtCore import QDate, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# isort: split
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from personal_finance.domain.analytics_cashflow import CashFlowReport, CashSourceKind
from personal_finance.domain.analytics_cashflow_months import (
    CashMonth,
    CashMonthPage,
    cash_month_sources,
    cash_months,
    month_index,
)
from personal_finance.domain.errors import ValidationError
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.tables import replace_table


def _date_text(day: date) -> str:
    return f"{day.day:02}.{day.month:02}.{day.year:04}"


def _month_caption(row: CashMonth) -> str:
    first, last = row.period.start, row.period.end
    title = f"{first.month:02}.{first.year:04}"
    if first.day != 1 or last.day != monthrange(last.year, last.month)[1]:
        title += f" · {first.day:02}–{last.day:02}"
    return title


class CashFlowMonths(QWidget):
    sources_requested = Signal(object, object, str)
    MONTHS_PER_PAGE = 6

    def __init__(
        self, report: CashFlowReport, names: dict[str, str], parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.report = report
        self.page: CashMonthPage | None = None
        self._offset = 0
        self._disposed = False
        self._targets: dict[object, int] = {}
        self._currencies = {v.currency.code: v.currency for v in report.totals}
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Валюта"))
        self.currency = QComboBox()
        self.currency.setAccessibleName("Валюта месячных остатков")
        for code in self._currencies:
            self.currency.addItem(code, code)
        controls.addWidget(self.currency)
        controls.addWidget(QLabel("Счёт"))
        self.account = QComboBox()
        self.account.setAccessibleName("Счёт месячных остатков")
        self.account.addItem("Все счета из отбора отчёта", None)
        for value in sorted(
            report.accounts, key=lambda v: (names.get(v.account_id, "").casefold(), v.account_id)
        ):
            self.account.addItem(
                names.get(value.account_id, "Счёт недоступен") + " · " + value.totals.currency.code,
                value.account_id,
            )
        self.account.setMinimumContentsLength(20)
        self.account.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        controls.addWidget(self.account, 1)
        layout.addLayout(controls)
        navigation = QHBoxLayout()
        navigation.addWidget(QLabel("Показать с месяца"))
        start = report.period.start
        self.month = QDateEdit(QDate(start.year, start.month, 1))
        self.month.setDisplayFormat("MM.yyyy")
        self.month.setCalendarPopup(True)
        self.month.setMinimumDate(QDate(start.year, start.month, 1))
        self.month.setMaximumDate(
            QDate(report.period.end.year, report.period.end.month, report.period.end.day)
        )
        self.month.setAccessibleName("Первый месяц страницы остатков")
        navigation.addWidget(self.month)
        self.previous, self.next = QPushButton("Назад"), QPushButton("Дальше")
        self.page_label = _label("")
        navigation.addWidget(self.previous)
        navigation.addWidget(self.page_label, 1)
        navigation.addWidget(self.next)
        layout.addLayout(navigation)
        self.table = QTableView()
        self.table.setAccessibleName("Месячные остатки и движения")
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tabs = QTabWidget()
        self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.tabs.setMinimumHeight(180)
        self.tabs.addTab(self.table, "Таблица")
        self.figure = Figure(layout="constrained", facecolor="#f5f7f9")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setAccessibleName("Остаток на конец каждого месяца")
        self.canvas.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.canvas.setMinimumSize(800, 180)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.canvas)
        self.tabs.addTab(scroll, "График")
        layout.addWidget(self.tabs, 1)
        buttons = QHBoxLayout()
        self.source_buttons: dict[CashSourceKind, QPushButton] = {}
        for kind, title in [
            (CashSourceKind.BEFORE, "Остаток на начало"),
            (CashSourceKind.PERIOD, "Движения месяца"),
            (CashSourceKind.THROUGH_END, "Остаток на конец"),
        ]:
            button = QPushButton(title)
            button.setAccessibleName("Источники месяца: " + title)
            button.clicked.connect(lambda checked=False, kind=kind: self.show_sources(kind))
            self.source_buttons[kind] = button
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.message = _label("")
        layout.addWidget(self.message)
        self._pick_connection = self.canvas.mpl_connect("pick_event", self._picked)
        self.currency.currentIndexChanged.connect(self._reset)
        self.account.currentIndexChanged.connect(self._reset)
        self.month.dateChanged.connect(self._jump)
        self.previous.clicked.connect(lambda: self._move(-self.MONTHS_PER_PAGE))
        self.next.clicked.connect(lambda: self._move(self.MONTHS_PER_PAGE))
        self.table.doubleClicked.connect(
            lambda index: self.show_sources(CashSourceKind.THROUGH_END)
        )
        self._render()

    def _reset(self) -> None:
        self._offset = 0
        self._render()

    def _jump(self) -> None:
        value = self.month.date()
        self._offset = (
            (value.year() - 1) * 12 + value.month() - 1 - month_index(self.report.period.start)
        )
        self._render()

    def _move(self, delta: int) -> None:
        if self.page is not None:
            self._offset = max(0, min(self.page.month_count - 1, self._offset + delta))
            self._render()

    def _render(self) -> None:
        if self._disposed:
            return
        self._targets.clear()
        self.figure.clear()
        self.axes = self.figure.add_subplot()
        self.axes.set_facecolor("#ffffff")
        self.axes.tick_params(colors="#172b35")
        self.axes.set_xticks([])
        self.axes.axvline(0, color="#526b79", linewidth=0.8)
        currency = self._currencies.get(self.currency.currentData())
        try:
            if currency is None or self.account.currentIndex() < 0:
                raise ValidationError("Нет данных для месячных остатков.")
            self.page = cash_months(
                self.report,
                currency,
                account_id=self.account.currentData(),
                offset=self._offset,
                limit=self.MONTHS_PER_PAGE,
            )
        except ValidationError:
            self.page = None
        rows = self.page.rows if self.page is not None else ()
        self.model = replace_table(
            self.table,
            [
                "Месяц",
                "Остаток\nна начало",
                "Ввод начальных\nостатков",
                "Поступления",
                "Списания",
                "Изменение",
                "Остаток\nна конец",
            ],
            [
                [
                    _month_caption(v),
                    *[
                        format_report_amount(x, v.totals.currency)
                        for x in [
                            v.totals.opening_minor,
                            v.totals.capital_minor,
                            v.totals.incoming_minor,
                            v.totals.outgoing_minor,
                            v.totals.change_minor,
                            v.totals.closing_minor,
                        ]
                    ],
                ]
                for v in rows
            ],
            right_columns=tuple(range(1, 7)),
        )
        self.table.selectionModel().selectionChanged.connect(self._update_actions)
        self.currency.setEnabled(bool(self._currencies))
        self.account.setEnabled(bool(self._currencies))
        self.month.setEnabled(bool(self._currencies))
        self.previous.setEnabled(self.page is not None and self._offset > 0)
        self.next.setEnabled(
            self.page is not None and self._offset + len(rows) < self.page.month_count
        )
        if self.page is None:
            self.page_label.setText("Нет данных в выбранной валюте по выбранному счёту")
            self.message.setText(
                "Измените валюту или счёт. Отбор не подменяется другим счётом."
                if self._currencies
                else "В этом снимке нет данных для месячных остатков."
            )
            self.axes.set_yticks([])
            self._update_actions()
            self.canvas.draw_idle()
            return
        currency = self.page.currency
        first, last = rows[0].period.start, rows[-1].period.end
        with QSignalBlocker(self.month):
            self.month.setDate(QDate(first.year, first.month, 1))
        self.canvas.setMinimumHeight(max(180, len(rows) * 42 + 40))
        self.axes.set_title(
            "Остаток на конец периода в каждом месяце · " + currency.code,
            color="#172b35",
            fontsize=10,
        )
        self.page_label.setText(
            f"Месяцы {self._offset + 1}–{self._offset + len(rows)} из {self.page.month_count}"
        )
        self.message.setText(
            f"Страница: {_date_text(first)} — {_date_text(last)}. Суммы в {currency.code}. "
            "Неполные месяцы ограничены периодом. Свои переводы включены в потоки. "
            "Столбец открывает источники "
            "остатка на конец месяца; с клавиатуры используйте таблицу и кнопки."
        )
        maximum = max(abs(self.page.lower_minor), abs(self.page.upper_minor))
        self.coordinate_divisor = 10 ** max(0, len(str(maximum)) - 9)

        def coordinate(minor: int) -> int:
            value = abs(minor) // self.coordinate_divisor
            return -value if minor < 0 else value

        low, high = (
            min(0, coordinate(self.page.lower_minor)),
            max(0, coordinate(self.page.upper_minor)),
        )
        padding = max(1, (high - low) // 3)
        self.axes.set_xlim(low - padding, high + padding)
        labels = [_month_caption(v) for v in rows]
        self.axes.set_yticks(range(len(rows)), labels, parse_math=False)
        self.axes.invert_yaxis()
        bars = self.axes.barh(
            range(len(rows)),
            [coordinate(v.totals.closing_minor) for v in rows],
            color="#17694f",
            height=0.6,
            picker=True,
        )
        for index, (bar, value) in enumerate(zip(bars, rows, strict=True)):
            self._targets[bar] = index
            minor = value.totals.closing_minor
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
        self.table.selectRow(0)
        self._update_actions()
        self.canvas.draw_idle()

    def _selected(self) -> int | None:
        if self.page is None or not self.table.selectionModel().hasSelection():
            return None
        index = self.table.currentIndex()
        return index.row() if index.isValid() and 0 <= index.row() < len(self.page.rows) else None

    def _update_actions(self) -> None:
        row = self._selected()
        for kind, button in self.source_buttons.items():
            button.setEnabled(
                self.page is not None
                and row is not None
                and bool(cash_month_sources(self.page, row, kind))
            )

    def show_sources(self, kind: CashSourceKind) -> None:
        row = self._selected()
        if row is not None:
            self._emit_sources(row, kind)

    def _emit_sources(self, row: int, kind: CashSourceKind) -> None:
        if self._disposed or self.page is None:
            return
        lines = cash_month_sources(self.page, row, kind)
        if lines:
            period = self.page.rows[row].period
            title = {
                CashSourceKind.BEFORE: "Остаток на начало",
                CashSourceKind.PERIOD: "Движения месяца",
                CashSourceKind.THROUGH_END: "Остаток на конец",
            }[kind]
            exact_dates = (
                _date_text(period.start)
                if kind == CashSourceKind.BEFORE
                else _date_text(period.end)
                if kind == CashSourceKind.THROUGH_END
                else _date_text(period.start) + " — " + _date_text(period.end)
            )
            self.sources_requested.emit(self.page.currency, lines, title + " · " + exact_dates)

    def _picked(self, event: Any) -> None:
        if not self._disposed and event.artist in self._targets:
            self.table.selectRow(self._targets[event.artist])
            self._emit_sources(self._targets[event.artist], CashSourceKind.THROUGH_END)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self.canvas.mpl_disconnect(self._pick_connection)
        self._targets.clear()
        self.figure.clear()
        self.canvas.close()
