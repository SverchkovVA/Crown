"""Сохранение и выбор версий инвестиционных позиций."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QDialog, QHeaderView, QPushButton, QVBoxLayout

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
from personal_finance.presentation.investment_preview import InvestmentPreviewOptions
from personal_finance.presentation.recurring_payments import _table
from personal_finance.presentation.tables import replace_table

if TYPE_CHECKING:
    from personal_finance.presentation.main_window import MainWindow


class SavedInvestmentPositionsDialog(QDialog):
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
        self.open_button.setEnabled(False)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.open_button.clicked.connect(self._open)
        layout.addWidget(self.open_button)

    def _selection_changed(self) -> None:
        self.open_button.setEnabled(bool(self.table.selectionModel().selectedRows()))

    def _open(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self.selected = self.records[rows[0].row()]
            self.accept()


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

            present_form(dialog, self.panel, completed)

        self.window._panel_job(self.panel, load, show, "Загрузка инвестиционных позиций…")

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
