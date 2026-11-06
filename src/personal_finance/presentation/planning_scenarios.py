"""Подготовленная форма выбора условий; рабочее меню книги пока её не открывает."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.planning_scenarios import (
    BudgetScenario,
    PlanningKind,
    SavedPlanningScenario,
)
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.analytics_cashflow import _size
from personal_finance.presentation.deposit_scenarios import date_text
from personal_finance.presentation.tables import replace_table

KIND_LABELS = {
    PlanningKind.BUDGET: "Бюджеты",
    PlanningKind.SAVINGS_GOAL: "Цели накоплений",
}


class PlanningAction(StrEnum):
    CREATE = "create"
    CALCULATE = "calculate"
    EDIT = "edit"
    COPY = "copy"
    RENAME = "rename"
    ARCHIVE = "archive"
    RESTORE = "restore"
    HISTORY = "history"


@dataclass(frozen=True, slots=True)
class PlanningRequest:
    action: PlanningAction
    kind: PlanningKind
    record: SavedPlanningScenario | None


class SavedPlanningDialog(QDialog):
    def __init__(
        self,
        records: Sequence[SavedPlanningScenario],
        parent: QWidget | None = None,
        *,
        kind: PlanningKind = PlanningKind.BUDGET,
    ) -> None:
        super().__init__(parent)
        if (
            not isinstance(kind, PlanningKind)
            or any(not isinstance(v, SavedPlanningScenario) for v in records)
            or len({v.id for v in records}) != len(records)
        ):
            raise ValidationError("Некорректный список сохранённых условий.")
        self.records = tuple(sorted(records, key=lambda v: (v.kind.value, v.name.casefold(), v.id)))
        self.visible: tuple[SavedPlanningScenario, ...] = ()
        self.request: PlanningRequest | None = None
        self.setWindowTitle("Сохранённые бюджеты и цели")
        _size(self, 1000, 680)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                "Здесь сохраняются условия расчёта. Бюджет пересчитывается по журналу; "
                "накопления цели задаются вручную. Сохранение не меняет денежные остатки."
            )
        )
        filters = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.setAccessibleName("Вид сохранённых условий")
        for value, title in KIND_LABELS.items():
            self.kind.addItem(title, value.value)
        self.kind.setCurrentIndex(self.kind.findData(kind.value))
        self.search = QLineEdit()
        self.search.setMaxLength(120)
        self.search.setPlaceholderText("Найти по названию")
        self.search.setAccessibleName("Поиск бюджета или цели по названию")
        self.archived = QCheckBox("Показать архивные")
        filters.addWidget(self.kind)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.archived)
        layout.addLayout(filters)
        self.table = QTableView()
        self.table.setAccessibleName("Сохранённые условия бюджета и цели")
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setSortingEnabled(False)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table, 1)
        self.selection_info = _label("Выберите бюджет или цель.")
        layout.addWidget(self.selection_info)
        self.buttons: dict[PlanningAction, QPushButton] = {}
        grid = QGridLayout()
        for index, (action, title) in enumerate(
            [
                (PlanningAction.CREATE, "Создать"),
                (PlanningAction.CALCULATE, "Рассчитать"),
                (PlanningAction.EDIT, "Изменить условия"),
                (PlanningAction.COPY, "Создать копию"),
                (PlanningAction.RENAME, "Переименовать"),
                (PlanningAction.ARCHIVE, "В архив"),
                (PlanningAction.HISTORY, "История"),
            ]
        ):
            button = QPushButton(title)
            button.setAutoDefault(False)
            button.clicked.connect(lambda checked=False, action=action: self.choose(action))
            self.buttons[action] = button
            grid.addWidget(button, index // 4, index % 4)
        layout.addLayout(grid)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        close.button(QDialogButtonBox.StandardButton.Close).setAutoDefault(False)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.kind.currentIndexChanged.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.archived.toggled.connect(self.refresh)
        self.refresh()

    def refresh(self, *unused: object) -> None:
        query = self.search.text().strip().casefold()
        kind = self.kind.currentData()
        self.visible = tuple(
            v
            for v in self.records
            if v.kind == kind
            and (self.archived.isChecked() or not v.is_deleted)
            and query in v.name.casefold()
        )
        rows = []
        for record in self.visible:
            scenario = record.scenario
            if isinstance(scenario, BudgetScenario):
                period = date_text(scenario.period.start) + " — " + date_text(scenario.period.end)
                amount = scenario.limit
            else:
                period = (
                    date_text(scenario.as_of) + " — " + date_text(scenario.deadline)
                    if scenario.deadline is not None
                    else "С " + date_text(scenario.as_of)
                )
                amount = scenario.target
            rows.append(
                [
                    record.name,
                    period,
                    format_report_amount(amount.minor, amount.currency)
                    + " "
                    + amount.currency.code,
                    "В архиве" if record.is_deleted else "Активный",
                ]
            )
        self.model = replace_table(
            self.table,
            [
                "Название",
                "Период" if kind == PlanningKind.BUDGET else "Дата и срок",
                "Лимит" if kind == PlanningKind.BUDGET else "Цель",
                "Состояние",
            ],
            rows,
            right_columns=(2,),
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.clearSelection()
        self.table.setCurrentIndex(QModelIndex())
        self.table.selectionModel().selectionChanged.connect(self.update_actions)
        self.update_actions()

    def selected(self) -> SavedPlanningScenario | None:
        rows = self.table.selectionModel().selectedRows()
        if len(rows) != 1 or not 0 <= rows[0].row() < len(self.visible):
            return None
        return self.visible[rows[0].row()]

    def update_actions(self, *unused: object) -> None:
        current = self.selected()
        for action, button in self.buttons.items():
            button.setEnabled(action is PlanningAction.CREATE or current is not None)
        if current is None:
            self.selection_info.setText(f"Найдено: {len(self.visible)}. Выберите строку.")
            self.buttons[PlanningAction.ARCHIVE].setText("В архив")
        else:
            self.selection_info.setText(
                "Выбран: " + current.name + (" · В архиве" if current.is_deleted else "")
            )
            self.buttons[PlanningAction.ARCHIVE].setText(
                "Вернуть из архива" if current.is_deleted else "В архив"
            )

    def choose(self, action: object) -> None:
        try:
            kind = PlanningKind(self.kind.currentData())
        except (ValueError, TypeError):
            return
        if not isinstance(action, PlanningAction):
            return
        record = None if action is PlanningAction.CREATE else self.selected()
        if action is not PlanningAction.CREATE and record is None:
            return
        if action is PlanningAction.ARCHIVE and record is not None and record.is_deleted:
            action = PlanningAction.RESTORE
        self.request = PlanningRequest(action, kind, record)
        self.accept()


class PlanningHistoryDialog(QDialog):
    def __init__(
        self, records: Sequence[SavedPlanningScenario], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        if (
            not records
            or any(not isinstance(v, SavedPlanningScenario) for v in records)
            or len({v.id for v in records}) != 1
            or len({v.kind for v in records}) != 1
            or len({v.revision_id for v in records}) != len(records)
        ):
            raise ValidationError("Неверный список версий условий.")
        self.records = tuple(records)
        self.request: PlanningRequest | None = None
        self.setWindowTitle("История условий бюджета и цели")
        _size(self, 1000, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                "Версии условий неизменяемы. Выбранную версию можно рассчитать или "
                "скопировать в отдельную запись. Бюджет считается по текущему журналу."
            )
        )
        self.table = QTableView()
        self.table.setAccessibleName("Неизменяемые версии условий бюджета и цели")
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.model = replace_table(
            self.table,
            ["Название", "Сохранено · UTC", "Состояние", "Версия условий"],
            [
                [v.name, v.created_at, "В архиве" if v.is_deleted else "Активный", v.revision_id]
                for v in self.records
            ],
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.calculate = buttons.addButton("Рассчитать", QDialogButtonBox.ButtonRole.ActionRole)
        self.copy = buttons.addButton("Создать копию", QDialogButtonBox.ButtonRole.ActionRole)
        for button in (self.calculate, self.copy):
            button.setAutoDefault(False)
        self.calculate.clicked.connect(lambda: self.choose(PlanningAction.CALCULATE))
        self.copy.clicked.connect(lambda: self.choose(PlanningAction.COPY))
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.table.selectionModel().selectionChanged.connect(self._selection)
        self._selection()

    def selected(self) -> SavedPlanningScenario | None:
        rows = self.table.selectionModel().selectedRows()
        if len(rows) != 1 or not 0 <= rows[0].row() < len(self.records):
            return None
        return self.records[rows[0].row()]

    def _selection(self, *unused: object) -> None:
        enabled = self.selected() is not None
        self.calculate.setEnabled(enabled)
        self.copy.setEnabled(enabled)

    def choose(self, action: object) -> None:
        record = self.selected()
        if (
            record is None
            or not isinstance(action, PlanningAction)
            or action not in (PlanningAction.CALCULATE, PlanningAction.COPY)
        ):
            return
        self.request = PlanningRequest(action, record.kind, record)
        self.accept()
