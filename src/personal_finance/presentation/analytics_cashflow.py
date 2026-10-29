"""Период, остатки и исходные движения одного снимка, без финансовых команд."""

from html import escape

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.analytics import ReportScope
from personal_finance.domain.analytics_cashflow import (
    CashFlowReport,
    CashMovement,
    CashPosition,
    CashSourceKind,
    cash_flow_sources,
)
from personal_finance.domain.ledger import OPERATION_LABELS
from personal_finance.domain.models import Account, Category, Currency, Institution
from personal_finance.presentation.analytics import (
    OperationSourceDialog,
    ReportPeriodDialog,
    _label,
    format_report_amount,
)
from personal_finance.presentation.ledger_dialogs import category_title
from personal_finance.presentation.tables import replace_table


def _size(dialog: QDialog, width: int, height: int) -> None:
    if (screen := dialog.screen()) is not None:
        available = screen.availableGeometry()
        width, height = min(width, available.width() - 50), min(height, available.height() - 70)
    dialog.resize(width, height)


def _view(name: str) -> QTableView:
    view = QTableView()
    view.setAccessibleName(name)
    view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
    view.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
    view.verticalHeader().hide()
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    view.setWordWrap(False)
    view.setAlternatingRowColors(True)
    return view


class CashFlowPeriodDialog(ReportPeriodDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        accounts: list[Account] | None = None,
        institutions: list[Institution] | None = None,
    ) -> None:
        super().__init__(parent, accounts=accounts, institutions=institutions)
        self.setWindowTitle("Движение средств · Период и счета")
        self.description.setText(
            "Выберите период, банк и счёт. Остатки и движения рассчитываются отдельно по валютам."
        )
        self.form.setRowVisible(self.category, False)
        self.form.setRowVisible(self.include_children, False)

    def _scope(self) -> ReportScope:
        return ReportScope(account_ids=super()._scope().account_ids)


class CashFlowSourcesDialog(QDialog):
    def __init__(
        self,
        lines: tuple[CashMovement, ...],
        currency: Currency,
        title: str,
        accounts: dict[str, str],
        categories: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.lines, self.currency, self.accounts, self.categories = (
            lines,
            currency,
            accounts,
            categories,
        )
        self.setWindowTitle(title)
        _size(self, 1080, 620)
        layout = QVBoxLayout(self)
        self.summary = _label(
            f"Движений: {len(lines)} · Сумма: "
            f"{format_report_amount(sum(v.minor for v in lines), currency)} {currency.code}"
        )
        layout.addWidget(self.summary)
        self.table = _view("Исходные движения средств")
        self.model = replace_table(
            self.table,
            [
                "Дата",
                "Вид",
                "Счёт движения",
                "Движение · " + currency.code,
                "Сумма операции",
                "Комиссия\nпо счёту",
                "Примечание",
            ],
            [
                [
                    f"{v.operation.draft.occurred_on:%d.%m.%Y}",
                    OPERATION_LABELS[v.operation.draft.kind],
                    accounts.get(v.account_id, "Счёт недоступен"),
                    format_report_amount(v.minor, currency),
                    format_report_amount(v.operation.draft.amount.minor, currency),
                    format_report_amount(v.fee_minor, currency),
                    v.operation.draft.note,
                ]
                for v in lines
            ],
            right_columns=(3, 4, 5),
        )
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(2, 200)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        layout.addWidget(
            _label(
                "Записанные версии на момент построения отчёта. "
                "Перевод между выбранными счетами показан двумя движениями. "
                "Итог рассчитан по столбцу «Движение». "
                "Для новых данных постройте отчёт заново."
            )
        )
        self.details_button = QPushButton("Операция из снимка")
        self.details_button.clicked.connect(self.show_details)
        self.table.doubleClicked.connect(self._double_clicked)
        self.table.selectionModel().selectionChanged.connect(self._update_actions)
        layout.addWidget(self.details_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if lines:
            self.table.selectRow(0)
        self._update_actions()

    def _selected(self) -> CashMovement | None:
        index = self.table.currentIndex()
        return (
            self.lines[index.row()]
            if index.isValid() and 0 <= index.row() < len(self.lines)
            else None
        )

    def _update_actions(self) -> None:
        self.details_button.setEnabled(self._selected() is not None)

    def _double_clicked(self, index: QModelIndex) -> None:
        if index.isValid():
            self.table.selectRow(index.row())
            self.show_details()

    def show_details(self) -> None:
        line = self._selected()
        if line is None:
            return
        dialog = OperationSourceDialog(line, self.categories, self.accounts, self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()


class CashFlowDialog(QDialog):
    def __init__(
        self,
        report: CashFlowReport,
        accounts: list[Account],
        parent: QWidget | None = None,
        *,
        categories: list[Category] | None = None,
    ) -> None:
        super().__init__(parent)
        self.report = report
        self.account_names = {a.id: a.name for a in accounts}
        self.category_names = {c.id: category_title(c, categories or []) for c in categories or []}
        self.positions = tuple(
            sorted(
                report.accounts,
                key=lambda v: (self.account_names.get(v.account_id, "").casefold(), v.account_id),
            )
        )
        self.setWindowTitle("Движение средств")
        _size(self, 1120, 660)
        layout = QVBoxLayout(self)
        self.period_label = _label(
            f"{report.period.start:%d.%m.%Y} — {report.period.end:%d.%m.%Y} · "
            f"Операций в периоде: {report.period_operation_count}"
        )
        layout.addWidget(self.period_label)
        selected = report.scope.account_ids
        caption = (
            "все"
            if selected is None
            else ", ".join(
                sorted(
                    (self.account_names.get(i, "Недоступный счёт") for i in selected),
                    key=str.casefold,
                )
            )
            if selected
            else "не выбраны"
        )
        self.scope_label = _label(
            "Счета: " + (caption if len(caption) <= 160 else f"выбрано {len(selected or [])}")
        )
        self.scope_label.setToolTip("<p>" + escape("Счета: " + caption) + "</p>")
        layout.addWidget(self.scope_label)
        layout.addWidget(
            _label(
                "Остаток на начало учитывает движения до периода. "
                "Введённые начальные остатки внутри периода показаны отдельно. "
                "Поступления и списания включают свои переводы, "
                "не являясь доходами и расходами."
            )
        )
        self.tabs = QTabWidget()
        self.total_table = _view("Итоги движения средств по валютам")
        self.account_table = _view("Движение средств по счетам")
        headers = [
            "Остаток\nна начало",
            "Ввод начальных\nостатков",
            "Поступления",
            "Списания",
            "Изменение",
            "Остаток\nна конец",
        ]
        self.total_model = replace_table(
            self.total_table,
            ["Валюта", *headers],
            [[v.currency.code, *self._amounts(v)] for v in report.totals],
            right_columns=tuple(range(1, 7)),
        )
        self.account_model = replace_table(
            self.account_table,
            ["Счёт", "Валюта", *headers],
            [
                [
                    self.account_names.get(v.account_id, "Счёт недоступен"),
                    v.totals.currency.code,
                    *self._amounts(v.totals),
                ]
                for v in self.positions
            ],
            right_columns=tuple(range(2, 8)),
        )
        self.account_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Interactive
        )
        self.account_table.setColumnWidth(0, 200)
        self.tabs.addTab(self.total_table, "Итоги")
        self.tabs.addTab(self.account_table, "Счета")
        layout.addWidget(self.tabs, 1)
        self.empty_label = _label(
            "До конца выбранного периода нет движений по выбранным счетам. "
            "Счета без операций здесь не показаны."
        )
        self.empty_label.setVisible(not report.movements)
        layout.addWidget(self.empty_label)
        row = QHBoxLayout()
        self.source_buttons: dict[CashSourceKind, QPushButton] = {}
        for kind, title in [
            (CashSourceKind.BEFORE, "Остаток на начало"),
            (CashSourceKind.PERIOD, "Движения в периоде"),
            (CashSourceKind.THROUGH_END, "Остаток на конец"),
        ]:
            button = QPushButton(title)
            button.setAccessibleName("Источники: " + title)
            button.clicked.connect(lambda checked=False, kind=kind: self.show_sources(kind))
            self.source_buttons[kind] = button
            row.addWidget(button)
        layout.addLayout(row)
        layout.addWidget(
            _label(
                "Источники открываются из выбранной строки. "
                "Суммы — в валюте строки; конвертации нет. "
                "Снимок не меняется при последующем исправлении операций."
            )
        )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.tabs.currentChanged.connect(self._update_actions)
        for table in [self.total_table, self.account_table]:
            table.selectionModel().selectionChanged.connect(self._update_actions)
            if table.model().rowCount():
                table.selectRow(0)
        self._update_actions()

    @staticmethod
    def _amounts(value: CashPosition) -> list[str]:
        return [
            format_report_amount(v, value.currency)
            for v in [
                value.opening_minor,
                value.capital_minor,
                value.incoming_minor,
                value.outgoing_minor,
                value.change_minor,
                value.closing_minor,
            ]
        ]

    def _selected(self) -> tuple[Currency, str | None] | None:
        accounts = self.tabs.currentIndex() == 1
        table = self.account_table if accounts else self.total_table
        index = table.currentIndex()
        if not index.isValid():
            return None
        if accounts and 0 <= index.row() < len(self.positions):
            value = self.positions[index.row()]
            return value.totals.currency, value.account_id
        if not accounts and 0 <= index.row() < len(self.report.totals):
            return self.report.totals[index.row()].currency, None
        return None

    def _update_actions(self) -> None:
        selection = self._selected()
        for kind, button in self.source_buttons.items():
            button.setEnabled(
                selection is not None
                and bool(
                    cash_flow_sources(self.report, selection[0], kind, account_id=selection[1])
                )
            )

    def show_sources(self, kind: CashSourceKind) -> None:
        selection = self._selected()
        if selection is None:
            return
        currency, account_id = selection
        lines = cash_flow_sources(self.report, currency, kind, account_id=account_id)
        if not lines:
            return
        title = {
            CashSourceKind.BEFORE: "Остаток на начало",
            CashSourceKind.PERIOD: "Движения в периоде",
            CashSourceKind.THROUGH_END: "Остаток на конец",
        }[kind]
        dialog = CashFlowSourcesDialog(
            lines, currency, title, self.account_names, self.category_names, self
        )
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
