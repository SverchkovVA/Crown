"""Правила категоризации: русский справочник, форма и неизменяемая история."""

from uuid import uuid4

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.classification import CategoryRule, CategoryRuleVersion
from personal_finance.domain.errors import FinanceError
from personal_finance.domain.ledger import OPERATION_LABELS, OperationKind
from personal_finance.domain.models import Account, Category
from personal_finance.presentation.ledger_dialogs import category_title
from personal_finance.presentation.periods import local_time
from personal_finance.presentation.tables import replace_table


def plain(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class CategoryRuleDialog(QDialog):
    def __init__(
        self,
        accounts: list[Account],
        categories: list[Category],
        parent: QWidget | None = None,
        *,
        existing: CategoryRule | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            "Изменить правило категории" if existing else "Создать правило категории"
        )
        self.resize(680, 500)
        self.rule: CategoryRule | None = None
        self._existing, self._categories = existing, categories
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain(
                "Правило подсказывает категорию до подтверждения импорта. "
                "Ручная категория и части имеют приоритет. Сохранённые операции не меняются."
            )
        )
        form = QFormLayout()
        self.name, self.contains = QLineEdit(), QLineEdit()
        self.name.setMaxLength(120)
        self.contains.setMaxLength(200)
        self.kind = QComboBox()
        for kind in (OperationKind.EXPENSE, OperationKind.INCOME):
            self.kind.addItem(OPERATION_LABELS[kind], kind.value)
        self.category, self.account = QComboBox(), QComboBox()
        self.account.addItem("Любой счёт", None)
        for account in accounts:
            self.account.addItem(account.name, account.id)
        self.priority = QSpinBox()
        self.priority.setRange(0, 999)
        for title, widget in (
            ("Название", self.name),
            ("Вид операции", self.kind),
            ("Описание содержит", self.contains),
            ("Предложить категорию", self.category),
            ("Приоритет: больше — раньше", self.priority),
            ("Ограничить счётом", self.account),
        ):
            form.addRow(title, widget)
        layout.addLayout(form)
        layout.addWidget(
            plain(
                "Текст ищется буквально без учёта регистра. "
                "При одинаковом приоритете разных категорий нужно выбрать категорию вручную."
            )
        )
        layout.addStretch()
        self.kind.currentIndexChanged.connect(self._categories_for_kind)
        if existing:
            self.name.setText(existing.name)
            self.contains.setText(existing.contains)
            self.kind.setCurrentIndex(self.kind.findData(existing.kind.value))
            self.priority.setValue(existing.priority)
            self.account.setCurrentIndex(max(0, self.account.findData(existing.account_id)))
        self._categories_for_kind()
        if existing:
            self.category.setCurrentIndex(max(0, self.category.findData(existing.category_id)))
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить правило")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self._validate)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _categories_for_kind(self) -> None:
        previous = self.category.currentData()
        self.category.clear()
        self.category.addItem("Выберите категорию", None)
        for category in self._categories:
            if category.kind.value == self.kind.currentData():
                self.category.addItem(category_title(category, self._categories), category.id)
        self.category.setCurrentIndex(max(0, self.category.findData(previous)))

    def _validate(self) -> None:
        try:
            self.rule = CategoryRule(
                self._existing.id if self._existing else str(uuid4()),
                self.name.text(),
                OperationKind(self.kind.currentData()),
                self.contains.text(),
                self.category.currentData(),
                self.priority.value(),
                self.account.currentData(),
                self._existing.enabled if self._existing else True,
            )
        except FinanceError as error:
            QMessageBox.warning(self, "Проверьте правило", str(error))
            return
        self.accept()


def rule_row(
    value: CategoryRuleVersion, accounts: list[Account], categories: list[Category]
) -> list[str]:
    rule = value.rule
    account_names = {a.id: a.name for a in accounts}
    category_names = {c.id: category_title(c, categories) for c in categories}
    return [
        str(rule.priority),
        rule.name,
        OPERATION_LABELS[rule.kind],
        category_names.get(rule.category_id, "Недоступная категория"),
        rule.contains,
        account_names.get(rule.account_id, "Недоступный счёт") if rule.account_id else "Любой счёт",
        "Включено" if rule.enabled else "Выключено",
    ]


class CategoryRuleHistoryDialog(QDialog):
    def __init__(
        self,
        versions: list[CategoryRuleVersion],
        accounts: list[Account],
        categories: list[Category],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("История правила категории")
        self.resize(1000, 580)
        layout = QVBoxLayout(self)
        self.table = QTableView()
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.model = replace_table(
            self.table,
            [
                "Версия",
                "Дата и время",
                "Приоритет",
                "Название",
                "Вид",
                "Категория",
                "Описание содержит",
                "Счёт",
                "Состояние",
            ],
            [
                [str(i + 1), local_time(v.created_at)] + rule_row(v, accounts, categories)
                for i, v in enumerate(versions)
            ],
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setMaximumSectionSize(350)
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class CategoryRulePage(QWidget):
    addRequested = Signal()
    editRequested = Signal()
    toggleRequested = Signal()
    historyRequested = Signal()
    changedFilter = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._records: list[CategoryRuleVersion] = []
        self._available = False
        layout = QVBoxLayout(self)
        self.notice = plain(
            "Правила подсказывают категории перед подтверждением импорта. "
            "Ручной выбор главнее; прежние операции не меняются."
        )
        layout.addWidget(self.notice)
        self.disabled = QCheckBox("Показывать выключенные")
        self.disabled.toggled.connect(self.changedFilter)
        layout.addWidget(self.disabled)
        actions = QHBoxLayout()
        self.add, self.edit, self.toggle, self.history = (
            QPushButton(text) for text in ("Создать…", "Изменить…", "Выключить", "История…")
        )
        for widget, signal in (
            (self.add, self.addRequested),
            (self.edit, self.editRequested),
            (self.toggle, self.toggleRequested),
            (self.history, self.historyRequested),
        ):
            actions.addWidget(widget)
            widget.clicked.connect(signal)
        actions.addStretch()
        layout.addLayout(actions)
        self.table = QTableView()
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.table, 1)
        self.set_records([], False, [], [])

    def selected(self) -> CategoryRuleVersion | None:
        row = self.table.currentIndex().row()
        return (
            self._records[row]
            if self.table.selectionModel().hasSelection() and 0 <= row < len(self._records)
            else None
        )

    def set_records(
        self,
        values: list[CategoryRuleVersion],
        available: bool,
        accounts: list[Account],
        categories: list[Category],
    ) -> None:
        self._records, self._available = values, available
        self.model = replace_table(
            self.table,
            ["Приоритет", "Название", "Вид", "Категория", "Описание содержит", "Счёт", "Состояние"],
            [rule_row(v, accounts, categories) for v in values],
            selection_changed=self._update_actions,
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setMaximumSectionSize(350)
        self.notice.setText(
            "Правила подсказывают категории перед подтверждением импорта. "
            "Ручной выбор главнее; прежние операции не меняются."
            if available
            else "Для правил создайте проверенную резервную копию и восстановите её в новую папку."
        )
        self.disabled.setEnabled(available)
        self._update_actions()

    def _update_actions(self) -> None:
        selected = self.selected()
        self.add.setEnabled(self._available)
        for widget in (self.edit, self.toggle, self.history):
            widget.setEnabled(self._available and selected is not None)
        self.toggle.setText(
            "Выключить" if selected is None or selected.rule.enabled else "Включить"
        )
