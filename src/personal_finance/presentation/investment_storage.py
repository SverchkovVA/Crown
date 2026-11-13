"""Сохранение и выбор версий инвестиционных позиций."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from personal_finance.application.vault import VaultSession
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.investment_positions import (
    InvestmentPositionInputs,
    SavedInvestmentPosition,
)
from personal_finance.infrastructure.persistence.investment_position_repository import (
    SqlInvestmentPositions,
)
from personal_finance.presentation.analytics import _label
from personal_finance.presentation.inline_forms import InlineForms, present_form
from personal_finance.presentation.investment_preview import (
    InvestmentPreviewOptions,
    InvestmentPreviewRequest,
)
from personal_finance.presentation.periods import local_time
from personal_finance.presentation.recurring_payments import _date, _date_input, _table
from personal_finance.presentation.tables import replace_table

if TYPE_CHECKING:
    from personal_finance.presentation.main_window import MainWindow


class SavedInvestmentPositionsDialog(QDialog):
    historyRequested = Signal()

    def __init__(
        self,
        records: Sequence[SavedInvestmentPosition],
        parent: MainWindow,
        options: InvestmentPreviewOptions,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Сохранённые инвестиционные позиции")
        self.records = tuple(
            record for record in records if record.inputs.account_id in options.accounts
        )
        self.selected: SavedInvestmentPosition | None = None
        layout = QVBoxLayout(self)
        self.table = _table("Сохранённые инвестиционные позиции")
        replace_table(
            self.table,
            ["Инструмент", "Счёт", "Операций", "Ручных цен"],
            [
                [
                    record.inputs.security.code,
                    options.accounts[record.inputs.account_id].name,
                    str(len(record.inputs.events)),
                    str(len(record.inputs.prices)),
                ]
                for record in self.records
            ],
            right_columns=(2, 3),
        )
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        if not self.records:
            layout.addWidget(_label("Для доступных счетов сохранённых позиций пока нет."))
        layout.addWidget(self.table, 1)
        self.open_button = QPushButton("Открыть позицию")
        self.open_button.setObjectName("primary")
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.open_button.clicked.connect(self._open)
        self.history_button = QPushButton("История версий")
        self.history_button.clicked.connect(self.historyRequested)
        actions = QHBoxLayout()
        actions.addWidget(self.open_button)
        actions.addWidget(self.history_button)
        actions.addStretch()
        layout.addLayout(actions)
        self._selection_changed()

    def selection(self) -> SavedInvestmentPosition | None:
        rows = self.table.selectionModel().selectedRows()
        return self.records[rows[0].row()] if rows else None

    def _selection_changed(self) -> None:
        selected = self.selection() is not None
        self.open_button.setEnabled(selected)
        self.history_button.setEnabled(selected)

    def _open(self) -> None:
        if (record := self.selection()) is not None:
            self.selected = record
            self.accept()


class InvestmentVersionHistoryDialog(QDialog):
    calculationRequested = Signal()

    def __init__(
        self,
        versions: Sequence[SavedInvestmentPosition],
        day: QDate,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("История инвестиционной позиции")
        self.versions = tuple(reversed(versions))
        layout = QVBoxLayout(self)
        layout.addWidget(_label("Выберите сохранённую версию для просмотра её расчёта."))
        self.table = _table("Неизменяемые версии позиции")
        replace_table(
            self.table,
            ["Версия", "Сохранена", "Инструмент", "Состояние", "Операций", "Ручных цен"],
            [
                [
                    str(len(self.versions) - index),
                    local_time(record.created_at),
                    record.inputs.security.code,
                    "Архивная" if record.is_deleted else "Действующая" if index == 0 else "Прежняя",
                    str(len(record.inputs.events)),
                    str(len(record.inputs.prices)),
                ]
                for index, record in enumerate(self.versions)
            ],
            right_columns=(4, 5),
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        date_form = QFormLayout()
        self.as_of = _date_input(day, "Дата расчёта версии позиции")
        date_form.addRow("На дату", self.as_of)
        actions.addLayout(date_form)
        self.calculate = QPushButton("Посмотреть расчёт")
        self.calculate.setObjectName("primary")
        self.calculate.clicked.connect(self.calculationRequested)
        actions.addWidget(self.calculate)
        actions.addStretch()
        layout.addLayout(actions)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self._selection_changed()

    def selection(self) -> SavedInvestmentPosition | None:
        rows = self.table.selectionModel().selectedRows()
        return self.versions[rows[0].row()] if rows else None

    def _selection_changed(self) -> None:
        self.calculate.setEnabled(self.selection() is not None)


class InvestmentStorage:
    def __init__(
        self,
        window: MainWindow,
        session: VaultSession,
        panel: InlineForms,
        options: InvestmentPreviewOptions,
        service: SqlInvestmentPositions,
        record: SavedInvestmentPosition | None,
        open_record: Callable[[SavedInvestmentPosition], None],
    ) -> None:
        self.window, self.session, self.panel = window, session, panel
        self.options, self.service, self.record = options, service, record
        self.open_record = open_record
        self.options.open_saved.clicked.connect(lambda: self.pick())
        self.options.save_position.clicked.connect(lambda: self.save())

    def _current(self) -> bool:
        return self.window.session is self.session and not self.panel.clearing

    def pick(self) -> None:
        if not self._current() or self.window._busy:
            return

        def load() -> list[SavedInvestmentPosition]:
            with self.session.mutex:
                self.session.ensure_open()
                return self.service.items()

        def show(records: list[SavedInvestmentPosition]) -> None:
            if not self._current():
                return
            dialog = SavedInvestmentPositionsDialog(records, self.window, self.options)

            def completed(code: int) -> None:
                if (
                    code == QDialog.DialogCode.Accepted
                    and self._current()
                    and dialog.selected is not None
                ):
                    self.open_record(dialog.selected)

            dialog.historyRequested.connect(lambda: self._show_history(dialog))
            present_form(dialog, self.panel, completed)

        self.window._panel_job(self.panel, load, show, "Загрузка инвестиционных позиций…")

    def _show_history(self, picker: SavedInvestmentPositionsDialog) -> None:
        record = picker.selection()
        if record is None or not self._current() or self.window._busy:
            return

        def load() -> list[SavedInvestmentPosition]:
            with self.session.mutex:
                self.session.ensure_open()
                return self.service.history(record.id)

        def show(versions: list[SavedInvestmentPosition]) -> None:
            if not self._current() or not picker.isVisible():
                return
            dialog = InvestmentVersionHistoryDialog(versions, self.options.as_of.date(), picker)

            def calculate() -> None:
                if (selected := dialog.selection()) is None or not self._current():
                    return
                inputs = selected.inputs
                account = self.options.accounts[inputs.account_id]
                request = InvestmentPreviewRequest(
                    account, inputs.security, inputs.events, _date(dialog.as_of), inputs.prices
                )
                self.window._show_investment_calculation(self.session, request)

            dialog.calculationRequested.connect(calculate)
            present_form(dialog, self.panel)

        self.window._panel_job(self.panel, load, show, "Загрузка истории позиции…")

    def save(self) -> None:
        if not self._current() or self.window._busy:
            return
        request = self.options.prepare_request()
        if request is None:
            return
        try:
            inputs = InvestmentPositionInputs(
                request.account.id, request.security, request.events, request.prices
            )
        except ValidationError as error:
            self.panel.message(str(error))
            return
        prior = self.record
        if prior is not None and prior.inputs == inputs:
            self.panel.message("Позиция уже сохранена.")
            return

        def persist() -> SavedInvestmentPosition:
            with self.session.mutex:
                self.session.ensure_open()
                return self.service.save(
                    inputs,
                    identifier=prior.id if prior else None,
                    expected_revision_id=prior.revision_id if prior else None,
                )

        def saved(record: SavedInvestmentPosition) -> None:
            if self._current():
                self.record = record
                self.options.mark_saved(record.inputs.security)
                self.panel.message("Позиция сохранена в книге.")

        self.window._panel_job(self.panel, persist, saved, "Сохранение инвестиционной позиции…")
