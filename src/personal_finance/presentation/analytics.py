"""Период и таблицы отчёта только для чтения, без финансовых команд."""

from datetime import date
from html import escape

from PySide6.QtCore import QDate, QModelIndex, QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.analytics import (
    FlowTotals,
    IncomeExpenseReport,
    OperationFlow,
    ReportPeriod,
    ReportScope,
    report_sources,
)
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OPERATION_LABELS
from personal_finance.domain.models import Account, Category, CategoryKind, Currency, Institution
from personal_finance.presentation.ledger_dialogs import category_title
from personal_finance.presentation.tables import replace_table


def _label(text: str) -> QLabel:
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    return result


def format_report_amount(minor: int, currency: Currency) -> str:
    scale = 10**currency.exponent
    whole, fraction = divmod(abs(minor), scale)
    text = f"{whole:,}".replace(",", " ")
    if currency.exponent:
        text += "," + f"{fraction:0{currency.exponent}d}"
    return ("−" if minor < 0 else "") + text


def scope_captions(
    report: IncomeExpenseReport, categories: list[Category], accounts: list[Account]
) -> tuple[str, str]:
    category_names = {c.id: category_title(c, categories) for c in categories}
    account_names = {a.id: a.name for a in accounts}
    compact = []
    full = []
    for title, identifiers, names in [
        ("Счета", report.scope.account_ids, account_names),
        ("Категории", report.scope.category_ids, category_names),
    ]:
        selected = (
            []
            if identifiers is None
            else sorted((names.get(i, "Недоступная запись") for i in identifiers), key=str.casefold)
        )
        expanded = (
            "все" if identifiers is None else ", ".join(selected) if selected else "не выбраны"
        )
        brief = (
            expanded if len(expanded) <= 100 and len(selected) <= 2 else f"выбрано: {len(selected)}"
        )
        compact.append(title + ": " + brief)
        full.append(title + ": " + expanded)
    return " · ".join(compact), "\n".join(full)


class ReportPeriodDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        accounts: list[Account] | None = None,
        categories: list[Category] | None = None,
        institutions: list[Institution] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Доходы и расходы · Период и фильтры")
        self.resize(680, 450)
        self._accounts = tuple(accounts or [])
        self._categories = tuple(categories or [])
        self.result_scope: ReportScope | None = None
        self.result_period: ReportPeriod | None = None
        layout = QVBoxLayout(self)
        self.description = _label(
            "Выберите период, банк, счёт и категорию. Валюты рассчитываются отдельно."
        )
        layout.addWidget(self.description)
        form = self.form = QFormLayout()
        today = date.today()
        self.start = QDateEdit(QDate(today.year, today.month, 1))
        self.end = QDateEdit(QDate(today.year, today.month, today.day))
        for editor, name in (
            (self.start, "Начало периода отчёта"),
            (self.end, "Конец периода отчёта"),
        ):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd.MM.yyyy")
            editor.setMinimumDate(QDate(1, 1, 1))
            editor.setMaximumDate(QDate(9999, 12, 31))
            editor.setAccessibleName(name)
        form.addRow("С", self.start)
        form.addRow("По включительно", self.end)
        self.bank = QComboBox()
        self.bank.addItem("Все банки и наличные", None)
        for institution in sorted(institutions or [], key=lambda v: v.name.casefold()):
            self.bank.addItem(institution.name, institution.id)
        self.bank.setAccessibleName("Банк отчёта")
        self.account = QComboBox()
        self.account.setAccessibleName("Счёт отчёта")
        self.category = QComboBox()
        self.category.addItem("Все категории", None)
        self.category.setAccessibleName("Категория отчёта")
        category_list = list(self._categories)
        for item in sorted(
            category_list, key=lambda v: (v.kind, category_title(v, category_list).casefold())
        ):
            kind = "Доход" if item.kind == CategoryKind.INCOME else "Расход"
            self.category.addItem(kind + " · " + category_title(item, category_list), item.id)
        self.include_children = QCheckBox("Включать подкатегории")
        self.include_children.setChecked(True)
        self.include_children.setEnabled(False)
        self.bank.currentIndexChanged.connect(self._fill_accounts)
        self.category.currentIndexChanged.connect(
            lambda: self.include_children.setEnabled(self.category.currentData() is not None)
        )
        self._fill_accounts()
        for combo in (self.bank, self.account, self.category):
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(24)
        form.addRow("Банк", self.bank)
        form.addRow("Счёт", self.account)
        form.addRow("Категория", self.category)
        form.addRow("", self.include_children)
        layout.addLayout(form)
        self.feedback = _label("")
        self.feedback.setObjectName("feedbackError")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Построить отчёт")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        try:
            self.result_scope = self._scope()
            self.result_period = ReportPeriod(
                date(self.start.date().year(), self.start.date().month(), self.start.date().day()),
                date(self.end.date().year(), self.end.date().month(), self.end.date().day()),
            )
        except ValidationError as error:
            self.result_period = None
            self.result_scope = None
            self.feedback.setText(str(error))
            self.feedback.show()
            return
        self.accept()

    def _fill_accounts(self) -> None:
        self.account.clear()
        self.account.addItem(
            "Все счета выбранного банка" if self.bank.currentData() else "Все счета", None
        )
        for item in sorted(self._accounts, key=lambda v: v.name.casefold()):
            if self.bank.currentData() is None or item.institution_id == self.bank.currentData():
                self.account.addItem(item.name + " · " + item.currency, item.id)

    def _scope(self) -> ReportScope:
        bank, account, category = (
            self.bank.currentData(),
            self.account.currentData(),
            self.category.currentData(),
        )
        account_ids = (
            None
            if bank is None and account is None
            else frozenset(
                item.id
                for item in self._accounts
                if (bank is None or item.institution_id == bank)
                and (account is None or item.id == account)
            )
        )
        category_ids = None
        if category is not None:
            selected = {category}
            if self.include_children.isChecked():
                while True:
                    updated = selected | {
                        item.id for item in self._categories if item.parent_id in selected
                    }
                    if updated == selected:
                        break
                    selected = updated
            category_ids = frozenset(selected)
        return ReportScope(account_ids, category_ids)


class OperationSourceDialog(QDialog):
    def __init__(
        self,
        flow: OperationFlow,
        names: dict[str, str],
        accounts: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.flow = flow
        self.fields: dict[str, QLabel] = {}
        self.setWindowTitle("Операция из снимка отчёта")
        self.resize(720, 560)
        if (screen := self.screen()) is not None:
            available = screen.availableGeometry()
            self.resize(min(720, available.width() - 50), min(560, available.height() - 70))
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                "Записанная версия на момент формирования отчёта. "
                "Вклад в отбор может быть частью полной суммы операции."
            )
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        value = flow.operation.draft
        currency = value.amount.currency

        def field(title: str, text: str) -> None:
            label = _label(text)
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
            self.fields[title] = label
            form.addRow(title, label)

        def money(minor: int) -> str:
            return format_report_amount(minor, currency) + " " + currency.code

        field("Дата", f"{value.occurred_on:%d.%m.%Y}")
        field("Вид", OPERATION_LABELS[value.kind])
        field("Счёт", accounts.get(value.account_id, "Счёт недоступен"))
        if value.destination_account_id:
            field("Счёт получения", accounts.get(value.destination_account_id, "Счёт недоступен"))
        field("Сумма операции", money(value.amount.minor))
        if value.fee.minor:
            field("Комиссия", money(value.fee.minor))
        field("Доход в отборе", money(flow.totals.income_minor))
        field("Расход в отборе", money(flow.totals.expense_minor))
        parts = [
            names.get(part.category_id, "Недоступная категория") + ": " + money(part.amount.minor)
            for part in value.category_allocations()
        ]
        if value.fee_category_id:
            parts.append(
                "Комиссия · "
                + names.get(value.fee_category_id, "Недоступная категория")
                + ": "
                + money(value.fee.minor)
            )
        if parts:
            field("Категории операции", "\n".join(parts))
        field("Примечание", value.note or "—")
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class IncomeExpenseDialog(QDialog):
    def __init__(
        self,
        report: IncomeExpenseReport,
        categories: list[Category],
        parent: QWidget | None = None,
        *,
        accounts: list[Account] | None = None,
    ) -> None:
        super().__init__(parent)
        self.report = report
        self._names = {c.id: category_title(c, categories) for c in categories}
        self._accounts = {a.id: a.name for a in accounts or []}
        self._category_order = sorted(
            report.categories,
            key=lambda v: (v.totals.currency.code, self._names.get(v.category_id, "").casefold()),
        )
        self._currencies = {value.currency.code: value.currency for value in report.totals}
        self._source_category: str | None = None
        self._source_month: date | None = None
        self._source_rows: tuple[OperationFlow, ...] = ()
        self.setWindowTitle("Доходы и расходы")
        self.resize(1120, 700)
        self.setMinimumSize(800, 500)
        if (screen := self.screen()) is not None:
            available = screen.availableGeometry()
            self.resize(
                min(1120, max(800, available.width() - 50)),
                min(700, max(500, available.height() - 70)),
            )
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                f"{report.period.start:%d.%m.%Y} — {report.period.end:%d.%m.%Y} · "
                f"Операций в расчёте: {report.operation_count}"
            )
        )
        layout.addWidget(
            _label(
                "Переводы между своими счетами и начальные остатки исключены из доходов/расходов. "
                "Комиссии включены в расходы; возвраты уменьшают расходы на дату получения. "
                "Разница = доходы − расходы."
            )
        )
        brief, full = scope_captions(report, categories, accounts or [])
        self.scope_context = _label(brief)
        self.scope_context.setAccessibleName("Глобальные фильтры отчёта")
        self.scope_context.setToolTip("<qt>" + escape(full).replace("\n", "<br>") + "</qt>")
        layout.addWidget(self.scope_context)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.summary_table = self._table(
            "Итоги",
            ["Валюта", "Доходы", "Расходы с возвратами", "Разница"],
            [self._total_row(v) for v in report.totals],
            (1, 2, 3),
        )
        self.category_table = self._table(
            "Категории",
            ["Категория", "Валюта", "Доходы", "Расходы с возвратами"],
            [
                [
                    self._names.get(v.category_id, "Недоступная категория"),
                    v.totals.currency.code,
                    format_report_amount(v.totals.income_minor, v.totals.currency),
                    format_report_amount(v.totals.expense_minor, v.totals.currency),
                ]
                for v in self._category_order
            ],
            (2, 3),
        )
        self.month_table = self._table(
            "По месяцам",
            ["Месяц", "Валюта", "Доходы", "Расходы с возвратами", "Разница"],
            [[f"{v.month:%m.%Y}", *self._total_row(v.totals)] for v in report.months],
            (2, 3, 4),
        )
        self.source_panel = QWidget()
        sources = QVBoxLayout(self.source_panel)
        bar = QHBoxLayout()
        bar.addWidget(_label("Валюта"))
        self.source_currency = QComboBox()
        self.source_currency.setAccessibleName("Валюта источников отчёта")
        for code in self._currencies:
            self.source_currency.addItem(code, code)
        bar.addWidget(self.source_currency)
        bar.addStretch()
        self.source_reset = QPushButton("Все операции отчёта")
        self.source_reset.clicked.connect(self._clear_source_filter)
        bar.addWidget(self.source_reset)
        sources.addLayout(bar)
        self.source_context = _label("")
        sources.addWidget(self.source_context)
        self.source_table = self._view([], [], ())
        self.source_table.setAccessibleName("Операции снимка отчёта")
        sources.addWidget(self.source_table, 1)
        self.source_totals = _label("")
        sources.addWidget(self.source_totals)
        sources.addWidget(
            _label(
                "Доход и расход в отборе показывают только выбранную часть. "
                "Полная сумма операции приведена отдельно; для перевода в расход входит комиссия."
            )
        )
        self.tabs.addTab(self.source_panel, "Операции")
        from personal_finance.presentation.analytics_charts import IncomeExpenseCharts

        self.charts = IncomeExpenseCharts(report, self._names, self)
        self.charts.sources_requested.connect(self._select_sources)
        self.tabs.addTab(self.charts, "Графики")
        self.finished.connect(self.charts.dispose)
        self.notice = _label(
            "В выбранном периоде нет действующих доходов, расходов, возвратов или комиссий."
            if not report.operation_count
            else "Снимок текущих записанных операций. "
            "После изменения книги сформируйте отчёт заново. "
            "Месяцы без движений не показаны; отрицательный расход означает превышение возвратов. "
            "Выберите строку и откройте её операции кнопкой или двойным щелчком."
        )
        layout.addWidget(self.notice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.source_button = QPushButton("Операции выбранной строки")
        self.source_button.clicked.connect(self._sources_clicked)
        buttons.addButton(self.source_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.source_details = QPushButton("Поля выбранной операции")
        self.source_details.clicked.connect(self.show_source_details)
        buttons.addButton(self.source_details, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.summary_table.doubleClicked.connect(self._summary_sources)
        self.category_table.doubleClicked.connect(self._category_sources)
        self.month_table.doubleClicked.connect(self._month_sources)
        self.source_table.doubleClicked.connect(self.show_source_details)
        for table in (self.summary_table, self.category_table, self.month_table):
            table.selectionModel().selectionChanged.connect(self._update_actions)
        self.tabs.currentChanged.connect(self._update_actions)
        self.source_currency.currentIndexChanged.connect(self._render_sources)
        self._render_sources()
        self._update_actions()

    def _select_sources(
        self, code: str, category_id: str | None = None, month: date | None = None
    ) -> None:
        with QSignalBlocker(self.source_currency):
            self.source_currency.setCurrentIndex(self.source_currency.findData(code))
        self._source_category, self._source_month = category_id, month
        self._render_sources()
        self.tabs.setCurrentWidget(self.source_panel)

    def _summary_sources(self, index: QModelIndex) -> None:
        if index.isValid():
            self._select_sources(self.report.totals[index.row()].currency.code)

    def _category_sources(self, index: QModelIndex) -> None:
        if index.isValid():
            value = self._category_order[index.row()]
            self._select_sources(value.totals.currency.code, value.category_id)

    def _month_sources(self, index: QModelIndex) -> None:
        if index.isValid():
            value = self.report.months[index.row()]
            self._select_sources(value.totals.currency.code, month=value.month)

    def _sources_clicked(self) -> None:
        current = self.tabs.currentWidget()
        if current is self.summary_table:
            self._summary_sources(self.summary_table.currentIndex())
        elif current is self.category_table:
            self._category_sources(self.category_table.currentIndex())
        elif current is self.month_table:
            self._month_sources(self.month_table.currentIndex())

    def _clear_source_filter(self) -> None:
        self._source_category = self._source_month = None
        self._render_sources()

    def _render_sources(self) -> None:
        code = self.source_currency.currentData()
        currency = self._currencies.get(code)
        self._source_rows = (
            report_sources(
                self.report, currency, category_id=self._source_category, month=self._source_month
            )
            if currency
            else ()
        )
        context = (
            self._names.get(self._source_category, "Недоступная категория")
            if self._source_category
            else f"{self._source_month:%m.%Y}"
            if self._source_month
            else "Весь отбор отчёта"
        )
        self.source_context.setText(context + (" · " + code if code else " · Нет данных"))
        rows = []
        for row in self._source_rows:
            value = row.operation.draft
            rows.append(
                [
                    f"{value.occurred_on:%d.%m.%Y}",
                    OPERATION_LABELS[value.kind],
                    self._accounts.get(value.account_id, "Счёт недоступен"),
                    format_report_amount(value.amount.minor, value.amount.currency)
                    + " "
                    + value.amount.currency.code,
                    format_report_amount(row.totals.income_minor, row.totals.currency),
                    format_report_amount(row.totals.expense_minor, row.totals.currency),
                    value.note,
                ]
            )
        replace_table(
            self.source_table,
            [
                "Дата",
                "Вид",
                "Счёт",
                "Сумма операции",
                "Доход в отборе",
                "Расход в отборе",
                "Примечание",
            ],
            rows,
            right_columns=(3, 4, 5),
            selection_changed=self._update_actions,
        )
        if rows:
            self.source_table.selectRow(0)
        if currency:
            income = sum(v.totals.income_minor for v in self._source_rows)
            expense = sum(v.totals.expense_minor for v in self._source_rows)
            self.source_totals.setText(
                f"Операций: {len(rows)} · "
                f"Доход: {format_report_amount(income, currency)} {code} · "
                f"Расход: {format_report_amount(expense, currency)} {code}"
            )
        else:
            self.source_totals.setText("Нет операций для просмотра.")
        self.source_reset.setEnabled(bool(self._currencies))
        self._update_actions()

    def _update_actions(self) -> None:
        table = self.tabs.currentWidget()
        chart_visible = table is self.charts
        self.notice.setVisible(not chart_visible)
        self.source_button.setVisible(not chart_visible)
        self.source_details.setVisible(not chart_visible)
        self.source_button.setEnabled(
            table in (self.summary_table, self.category_table, self.month_table)
            and table.currentIndex().isValid()
        )
        self.source_details.setEnabled(
            table is self.source_panel and self.source_table.currentIndex().isValid()
        )

    def show_source_details(self) -> None:
        index = self.source_table.currentIndex()
        if not index.isValid() or not 0 <= index.row() < len(self._source_rows):
            return
        dialog = OperationSourceDialog(
            self._source_rows[index.row()], self._names, self._accounts, self
        )
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    @staticmethod
    def _total_row(value: FlowTotals) -> list[str]:
        return [
            value.currency.code,
            format_report_amount(value.income_minor, value.currency),
            format_report_amount(value.expense_minor, value.currency),
            format_report_amount(value.difference_minor, value.currency),
        ]

    @staticmethod
    def _view(
        headers: list[str], rows: list[list[str]], right_columns: tuple[int, ...]
    ) -> QTableView:
        table = QTableView()
        replace_table(table, headers, rows, right_columns=right_columns)
        table.setWordWrap(False)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        if rows:
            table.selectRow(0)
        return table

    def _table(
        self, title: str, headers: list[str], rows: list[list[str]], right_columns: tuple[int, ...]
    ) -> QTableView:
        table = self._view(headers, rows, right_columns)
        table.setAccessibleName(title + " отчёта")
        self.tabs.addTab(table, title)
        return table
