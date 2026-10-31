"""Список-снимок сохранённых условий; обращения к книге выполняются вне окна."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.deposit_scenarios import SavedDepositScenario
from personal_finance.domain.errors import ValidationError
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.analytics_cashflow import _size
from personal_finance.presentation.tables import replace_table


class ScenarioAction(StrEnum):
    CREATE = "create"
    CALCULATE = "calculate"
    EDIT = "edit"
    COPY = "copy"
    RENAME = "rename"
    ARCHIVE = "archive"
    RESTORE = "restore"
    HISTORY = "history"


@dataclass(frozen=True, slots=True)
class ScenarioRequest:
    action: ScenarioAction
    record: SavedDepositScenario | None


def date_text(day: date) -> str:
    return f"{day.day:02d}.{day.month:02d}.{day.year:04d}"


class SavedScenariosDialog(QDialog):
    def __init__(
        self, records: Sequence[SavedDepositScenario], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        if any(not isinstance(v, SavedDepositScenario) for v in records) or len(
            {v.id for v in records}
        ) != len(records):
            raise ValidationError("Некорректный список текущих сценариев.")
        self.records = tuple(sorted(records, key=lambda v: (v.name.casefold(), v.id)))
        self.visible: tuple[SavedDepositScenario, ...] = ()
        self.request: ScenarioRequest | None = None
        self.setWindowTitle("Сохранённые сценарии вкладов")
        _size(self, 1000, 680)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                "Это сохранённые предположения расчёта. "
                "Они не создают банковские выплаты и не меняют остатки."
            )
        )
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setMaxLength(120)
        self.search.setPlaceholderText("Найти по названию")
        self.search.setAccessibleName("Поиск сценария по названию")
        self.archived = QCheckBox("Показать архивные")
        filters.addWidget(self.search, 1)
        filters.addWidget(self.archived)
        layout.addLayout(filters)
        self.table = QTableView()
        self.table.setAccessibleName("Сохранённые сценарии вкладов")
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setSortingEnabled(False)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table, 1)
        self.selection_info = _label("Выберите сценарий.")
        layout.addWidget(self.selection_info)
        self.buttons: dict[ScenarioAction, QPushButton] = {}
        grid = QGridLayout()
        for index, (action, text) in enumerate(
            [
                (ScenarioAction.CREATE, "Создать"),
                (ScenarioAction.CALCULATE, "Рассчитать"),
                (ScenarioAction.EDIT, "Изменить условия"),
                (ScenarioAction.COPY, "Создать копию"),
                (ScenarioAction.RENAME, "Переименовать"),
                (ScenarioAction.ARCHIVE, "В архив"),
                (ScenarioAction.HISTORY, "История"),
            ]
        ):
            button = QPushButton(text)
            button.setAutoDefault(False)
            button.clicked.connect(lambda checked=False, a=action: self.choose(a))
            self.buttons[action] = button
            grid.addWidget(button, index // 4, index % 4)
        layout.addLayout(grid)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        close.button(QDialogButtonBox.StandardButton.Close).setAutoDefault(False)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.search.textChanged.connect(self.refresh)
        self.archived.toggled.connect(self.refresh)
        self.refresh()

    def refresh(self, *unused: object) -> None:
        query = self.search.text().strip().casefold()
        self.visible = tuple(
            v
            for v in self.records
            if (self.archived.isChecked() or not v.is_deleted) and query in v.name.casefold()
        )
        self.model = replace_table(
            self.table,
            ["Название", "Срок", "Начальная сумма", "Валюта", "Состояние"],
            [
                [
                    v.name,
                    date_text(v.scenario.opened_on) + " — " + date_text(v.scenario.matures_on),
                    format_report_amount(v.scenario.initial.minor, v.scenario.initial.currency),
                    v.scenario.initial.currency.code,
                    "В архиве" if v.is_deleted else "Активный",
                ]
                for v in self.visible
            ],
            right_columns=(2,),
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.clearSelection()
        self.table.setCurrentIndex(QModelIndex())
        self.table.selectionModel().selectionChanged.connect(self.update_actions)
        self.update_actions()

    def selected(self) -> SavedDepositScenario | None:
        rows = self.table.selectionModel().selectedRows()
        if len(rows) != 1 or not 0 <= rows[0].row() < len(self.visible):
            return None
        return self.visible[rows[0].row()]

    def update_actions(self, *unused: object) -> None:
        current = self.selected()
        for action, button in self.buttons.items():
            button.setEnabled(action is ScenarioAction.CREATE or current is not None)
        if current is None:
            self.selection_info.setText(f"Найдено: {len(self.visible)}. Выберите сценарий.")
            self.buttons[ScenarioAction.ARCHIVE].setText("В архив")
        else:
            self.selection_info.setText(
                "Выбран: " + current.name + (" · В архиве" if current.is_deleted else "")
            )
            self.buttons[ScenarioAction.ARCHIVE].setText(
                "Вернуть из архива" if current.is_deleted else "В архив"
            )

    def choose(self, action: object) -> None:
        if not isinstance(action, ScenarioAction):
            return
        record = None if action is ScenarioAction.CREATE else self.selected()
        if action is not ScenarioAction.CREATE and record is None:
            return
        if action is ScenarioAction.ARCHIVE and record is not None and record.is_deleted:
            action = ScenarioAction.RESTORE
        self.request = ScenarioRequest(action, record)
        self.accept()
