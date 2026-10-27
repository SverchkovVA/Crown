"""Редактор переносимого фильтра журнала без автоматического изменения данных."""

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.errors import FinanceError
from personal_finance.domain.ledger import OPERATION_LABELS, OperationKind
from personal_finance.domain.models import Account, Category, Institution
from personal_finance.domain.organization import Tag
from personal_finance.domain.query import OperationQuery
from personal_finance.presentation.ledger_dialogs import category_title
from personal_finance.presentation.tag_selector import CheckList


class QueryDialog(QDialog):
    def __init__(
        self,
        query: OperationQuery,
        accounts: list[Account],
        categories: list[Category],
        institutions: list[Institution],
        tags: list[Tag],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Фильтр журнала")
        self.resize(760, 580)
        self.result_query: OperationQuery | None = None
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Все выбранные группы применяются вместе. Внутри группы достаточно одного значения; "
            "у операции должны быть все выбранные теги. Фильтр меняет только список, не остатки."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        basic = QWidget()
        form = QFormLayout(basic)
        self.use_start = QCheckBox("С даты")
        self.use_end = QCheckBox("По дату включительно")
        self.start = QDateEdit(QDate.currentDate())
        self.end = QDateEdit(QDate.currentDate())
        for field, value in ((self.start, query.start), (self.end, query.end)):
            field.setMinimumDate(QDate(1, 1, 1))
            field.setCalendarPopup(True)
            field.setDisplayFormat("dd.MM.yyyy")
            if value:
                field.setDate(QDate(value.year, value.month, value.day))
        self.use_start.setChecked(query.start is not None)
        self.use_end.setChecked(query.end is not None)
        self.start.setEnabled(self.use_start.isChecked())
        self.end.setEnabled(self.use_end.isChecked())
        self.use_start.toggled.connect(self.start.setEnabled)
        self.use_end.toggled.connect(self.end.setEnabled)
        form.addRow(self.use_start, self.start)
        form.addRow(self.use_end, self.end)
        self.text = QLineEdit(query.text)
        self.text.setMaxLength(500)
        form.addRow("Текст примечания", self.text)
        self.deleted = QCheckBox("Показывать удалённые операции")
        self.deleted.setChecked(query.include_deleted)
        form.addRow(self.deleted)
        self.kinds = CheckList(
            [(kind.value, title) for kind, title in OPERATION_LABELS.items()],
            tuple(kind.value for kind in query.kinds),
        )
        form.addRow("Виды операций", self.kinds)
        tabs.addTab(basic, "Период и текст")
        account_page = QWidget()
        account_layout = QHBoxLayout(account_page)
        self.accounts = CheckList([(a.id, a.name) for a in accounts], query.account_ids)
        self.institutions = CheckList(
            [(bank.id, bank.name) for bank in institutions], query.institution_ids
        )
        for title, choices in (("Счета", self.accounts), ("Банки", self.institutions)):
            column = QVBoxLayout()
            column.addWidget(QLabel(title))
            column.addWidget(choices)
            account_layout.addLayout(column)
        tabs.addTab(account_page, "Счета и банки")
        self.categories = CheckList(
            [(c.id, category_title(c, categories)) for c in categories], query.category_ids
        )
        tabs.addTab(self.categories, "Категории")
        self.tags = CheckList(
            [(tag.id, tag.name + (" (архив)" if tag.is_deleted else "")) for tag in tags],
            query.tag_ids,
        )
        tabs.addTab(self.tags, "Теги")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Применить фильтр")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate(self) -> None:
        from datetime import date

        self.result_query = None

        def day(field: QDateEdit) -> date:
            value = field.date()
            return date(value.year(), value.month(), value.day())

        try:
            self.result_query = OperationQuery(
                day(self.start) if self.use_start.isChecked() else None,
                day(self.end) if self.use_end.isChecked() else None,
                self.accounts.values(),
                self.categories.values(),
                self.institutions.values(),
                self.tags.values(),
                tuple(OperationKind(value) for value in self.kinds.values()),
                self.text.text(),
                self.deleted.isChecked(),
            )
        except FinanceError as exc:
            QMessageBox.warning(self, "Проверьте фильтр", str(exc))
            return
        self.accept()
