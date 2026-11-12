"""Явное сопоставление ожидаемого платежа с записью журнала."""

from datetime import date

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QHeaderView, QPushButton, QVBoxLayout, QWidget

from personal_finance.domain.analytics_cashflow import CashMovement
from personal_finance.domain.balance_forecast import BalanceForecast, PaymentMatch, match_payments
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OPERATION_LABELS
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.deposit_scenarios import date_text
from personal_finance.presentation.recurring_payments import _table
from personal_finance.presentation.tables import replace_table


class PaymentMatching(QWidget):
    changed = Signal(object)

    def __init__(self, forecast: BalanceForecast) -> None:
        super().__init__()
        self.forecast = forecast
        self._journal: dict[tuple[date, int], list[CashMovement]] = {}
        for item in forecast.booked:
            key = item.operation.draft.occurred_on, item.minor
            self._journal.setdefault(key, []).append(item)
        self.indices = tuple(
            index
            for index, item in enumerate(forecast.planned)
            if (item.occurs_on, item.signed_minor) in self._journal
        )
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                "Выберите платёж и запись журнала с той же датой и суммой. "
                "Связь действует в этом прогнозе."
            )
        )
        self.table = _table("Ожидаемые платежи с совпадениями в журнале")
        layout.addWidget(self.table, 1)
        self.journal = QComboBox()
        self.journal.setAccessibleName("Запись журнала для сопоставления")
        layout.addWidget(self.journal)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.confirm = QPushButton("Сопоставить")
        self.confirm.setObjectName("primary")
        self.undo = QPushButton("Отменить сопоставление")
        actions.addWidget(self.confirm)
        actions.addWidget(self.undo)
        actions.addStretch()
        layout.addLayout(actions)
        self.feedback = _label("")
        self.feedback.setObjectName("feedbackError")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        self.journal.currentIndexChanged.connect(self._enable_confirm)
        self.confirm.clicked.connect(self._confirm)
        self.undo.clicked.connect(self._undo)
        self._refresh()

    def _selected(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return self.indices[rows[0].row()] if rows else None

    def _refresh(self, selected: int | None = None) -> None:
        linked = {item.expected_index for item in self.forecast.matches}
        replace_table(
            self.table,
            ["Дата", "Ожидаемый платёж", "Сумма", "Состояние"],
            [
                [
                    date_text(item.occurs_on),
                    item.schedule.name,
                    format_report_amount(item.signed_minor, self.forecast.currency),
                    "Учтён в журнале" if index in linked else "Ожидается",
                ]
                for index in self.indices
                for item in (self.forecast.planned[index],)
            ],
            right_columns=(2,),
            selection_changed=self._show_journal,
        )
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        if selected is not None:
            self.table.selectRow(self.indices.index(selected))
        self._show_journal()

    def _show_journal(self) -> None:
        index = self._selected()
        matches = {item.expected_index: item.revision_id for item in self.forecast.matches}
        self.journal.blockSignals(True)
        self.journal.clear()
        if index is None:
            self.journal.addItem("Выберите ожидаемый платёж", None)
        else:
            item = self.forecast.planned[index]
            used = set(matches.values())
            candidates = self._journal[(item.occurs_on, item.signed_minor)]
            if index in matches:
                actual = next(
                    row for row in candidates if row.operation.revision_id == matches[index]
                )
                self.journal.addItem(self._journal_label(actual), None)
            else:
                available = [row for row in candidates if row.operation.revision_id not in used]
                self.journal.addItem(
                    "Выберите запись журнала" if available else "Совпадения уже сопоставлены", None
                )
                for row in available:
                    self.journal.addItem(self._journal_label(row), row.operation.revision_id)
        self.journal.setEnabled(self.journal.count() > 1)
        self.journal.blockSignals(False)
        self.undo.setEnabled(index in matches)
        self._enable_confirm()

    @staticmethod
    def _journal_label(item: CashMovement) -> str:
        draft = item.operation.draft
        return f"{draft.note or OPERATION_LABELS[draft.kind]} · {item.operation.id[-8:]}"

    def _enable_confirm(self) -> None:
        self.confirm.setEnabled(self.journal.currentData() is not None)

    def _confirm(self) -> None:
        index, revision = self._selected(), self.journal.currentData()
        if index is not None and isinstance(revision, str):
            self._apply((*self.forecast.matches, PaymentMatch(index, revision)), index)

    def _undo(self) -> None:
        index = self._selected()
        if index is not None:
            self._apply(
                tuple(item for item in self.forecast.matches if item.expected_index != index), index
            )

    def _apply(self, matches: tuple[PaymentMatch, ...], selected: int) -> None:
        try:
            self.forecast = match_payments(self.forecast, matches)
        except ValidationError as error:
            self.feedback.setText(str(error))
            self.feedback.show()
            return
        self.feedback.hide()
        self._refresh(selected)
        self.changed.emit(self.forecast)
