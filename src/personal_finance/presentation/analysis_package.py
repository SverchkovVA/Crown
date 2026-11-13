"""Выбор периода, состава и просмотр агрегатов перед сохранением."""

from datetime import date

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHeaderView,
    QPushButton,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.analysis_package import AnalysisPackage, package_categories
from personal_finance.domain.analytics import FlowTotals, ReportPeriod
from personal_finance.domain.errors import ValidationError
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.deposit_scenarios import date_text
from personal_finance.presentation.recurring_payments import _date, _date_input, _table
from personal_finance.presentation.tables import replace_table


class AnalysisPackageOptions(QDialog):
    previewRequested = Signal()

    def __init__(self, parent: QWidget, *, has_portfolio: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle("Пакет для ChatGPT")
        layout = QVBoxLayout(self)
        layout.addWidget(_label("Месячные итоги доходов и расходов и описание расчётов."))
        fields = QFormLayout()
        today = QDate.currentDate()
        self.start = _date_input(QDate(today.year(), today.month(), 1), "Начало периода")
        self.end = _date_input(today, "Конец периода")
        fields.addRow("Начало", self.start)
        fields.addRow("Конец", self.end)
        layout.addLayout(fields)
        self.categories = QCheckBox("Итоги по категориям с условными названиями")
        self.portfolio = QCheckBox("Оценка портфеля по валютам на конец периода")
        self.portfolio.setVisible(has_portfolio)
        layout.addWidget(self.categories)
        layout.addWidget(self.portfolio)
        self.feedback = _label("")
        self.feedback.setObjectName("feedbackError")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        self.preview = QPushButton("Предпросмотр")
        self.preview.setObjectName("primary")
        self.preview.clicked.connect(self.previewRequested)
        layout.addWidget(self.preview)
        layout.addStretch()

    def period(self) -> ReportPeriod | None:
        try:
            period = ReportPeriod(_date(self.start), _date(self.end))
        except ValidationError as error:
            self.feedback.setText(str(error))
            self.feedback.show()
            return None
        self.feedback.hide()
        return period


def _period_text(start: date, end: date) -> str:
    return f"{date_text(start)} — {date_text(end)}"


class AnalysisPackagePreview(QDialog):
    def __init__(self, package: AnalysisPackage, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Предпросмотр пакета для ChatGPT")
        self.package = package
        flow = package.flow
        layout = QVBoxLayout(self)
        layout.addWidget(_label(_period_text(flow.period.start, flow.period.end)))
        sections = ["Месячные итоги", "Описание расчётов"]
        if package.include_categories:
            sections.append(f"Категории: {len({item.category_id for item in flow.categories})}")
        if package.portfolio is not None:
            sections.append(f"Портфель: валют {len(package.portfolio.totals)}")
        layout.addWidget(_label(" · ".join(sections)))
        tabs = QTabWidget()
        self.table = self._flows(
            tabs,
            "По месяцам",
            "Месяц",
            [(f"{item.month:%m.%Y}", item.totals) for item in flow.months],
        )
        if package.include_categories:
            self._flows(tabs, "По категориям", "Категория", list(package_categories(flow)))
        if package.portfolio is not None:
            portfolio = package.portfolio
            self._section(
                tabs,
                "Портфель",
                ["Валюта", "Позиций", "Без цены", "Оценка"],
                [
                    [
                        item.currency.code,
                        str(item.open_count),
                        str(item.unpriced_count),
                        "—"
                        if item.priced_market_minor is None
                        else format_report_amount(item.priced_market_minor, item.currency),
                    ]
                    for item in portfolio.totals
                ],
                (1, 2, 3),
            )
            if portfolio.unavailable_count:
                layout.addWidget(
                    _label(f"Позиций на недоступных счетах: {portfolio.unavailable_count}.")
                )
        layout.addWidget(tabs, 1)
        warning = _label("JSON будет доступен без пароля. Crown сохраняет его только на ваш диск.")
        warning.setProperty("stickyNotice", True)
        layout.addWidget(warning)
        self.save = QPushButton("Выбрать файл…")
        self.save.setObjectName("primary")
        self.save.setProperty("stickyActions", True)
        self.save.clicked.connect(self.accept)
        layout.addWidget(self.save)

    @classmethod
    def _flows(
        cls, tabs: QTabWidget, title: str, first: str, flows: list[tuple[str, FlowTotals]]
    ) -> QTableView:
        return cls._section(
            tabs,
            title,
            [first, "Валюта", "Доходы", "Расходы", "Разница"],
            [
                [
                    key,
                    item.currency.code,
                    format_report_amount(item.income_minor, item.currency),
                    format_report_amount(item.expense_minor, item.currency),
                    format_report_amount(item.difference_minor, item.currency),
                ]
                for key, item in flows[:200]
            ],
            (2, 3, 4),
            total=len(flows),
        )

    @staticmethod
    def _section(
        tabs: QTabWidget,
        title: str,
        headers: list[str],
        rows: list[list[str]],
        numeric: tuple[int, ...],
        *,
        total: int | None = None,
    ) -> QTableView:
        page = QWidget()
        layout = QVBoxLayout(page)
        table = _table(title)
        replace_table(table, headers, rows, right_columns=numeric)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        if rows:
            layout.addWidget(table, 1)
        else:
            table.setParent(page)
            table.hide()
            layout.addWidget(_label("В этом разделе нет данных за выбранный период."))
            layout.addStretch()
        if total is not None and total > len(rows):
            layout.addWidget(_label(f"Показано {len(rows)} из {total} строк. В файл войдут все."))
        tabs.addTab(page, title)
        return table
