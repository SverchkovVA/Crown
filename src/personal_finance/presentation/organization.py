"""Справочники тегов и сохранённых фильтров с архивом и историей."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.ledger import OPERATION_LABELS
from personal_finance.domain.models import Account, Category, Institution
from personal_finance.domain.organization import SavedFilter, Tag
from personal_finance.domain.query import OperationQuery
from personal_finance.presentation.ledger_dialogs import category_title
from personal_finance.presentation.periods import local_time
from personal_finance.presentation.tables import replace_table


def describe_query(
    query: OperationQuery,
    accounts: list[Account],
    categories: list[Category],
    institutions: list[Institution],
    tags: list[Tag],
) -> str:
    groups = [
        ("Счета", query.account_ids, {value.id: value.name for value in accounts}),
        (
            "Категории",
            query.category_ids,
            {value.id: category_title(value, categories) for value in categories},
        ),
        ("Банки", query.institution_ids, {value.id: value.name for value in institutions}),
        (
            "Все теги",
            query.tag_ids,
            {value.id: value.name + (" (архив)" if value.is_deleted else "") for value in tags},
        ),
    ]
    lines = []
    if query.start:
        lines.append("С " + query.start.strftime("%d.%m.%Y"))
    if query.end:
        lines.append("По " + query.end.strftime("%d.%m.%Y") + " включительно")
    for title, ids, names in groups:
        if ids:
            lines.append(
                title
                + ": "
                + "; ".join(names.get(identifier, "Недоступная запись") for identifier in ids)
            )
    if query.kinds:
        lines.append("Виды: " + "; ".join(OPERATION_LABELS[kind] for kind in query.kinds))
    if query.text:
        lines.append("Примечание содержит: " + query.text)
    if query.include_deleted:
        lines.append("Включая удалённые операции")
    return "\n".join(lines) or "Все действующие операции"


class DefinitionHistoryDialog(QDialog):
    def __init__(
        self,
        versions: list[Tag | SavedFilter],
        parent: QWidget | None = None,
        *,
        accounts: list[Account],
        categories: list[Category],
        institutions: list[Institution],
        tags: list[Tag],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("История тега или фильтра")
        self.resize(900, 620)
        self._versions = versions
        self._lookups = (accounts, categories, institutions, tags)
        layout = QVBoxLayout(self)
        self.table = QTableView()
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.model = replace_table(
            self.table,
            ["Версия", "Дата и время", "Название", "Состояние"],
            [
                [
                    str(index + 1),
                    local_time(value.created_at),
                    value.name,
                    "Архив" if value.is_deleted else "Действует",
                ]
                for index, value in enumerate(versions)
            ],
            selection_changed=self._details,
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(230)
        layout.addWidget(self.details)
        if versions:
            self.table.selectRow(len(versions) - 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _details(self) -> None:
        row = self.table.currentIndex().row()
        if not 0 <= row < len(self._versions):
            self.details.clear()
            return
        value = self._versions[row]
        content = (
            "Название: "
            + value.name
            + "\nСостояние: "
            + ("Архив" if value.is_deleted else "Действует")
        )
        if isinstance(value, SavedFilter):
            content += "\n" + describe_query(value.query, *self._lookups)
        self.details.setPlainText(content)


class OrganizationPage(QWidget):
    changedArchive = Signal()
    addTagRequested = Signal()
    renameTagRequested = Signal()
    archiveTagRequested = Signal()
    restoreTagRequested = Signal()
    tagHistoryRequested = Signal()
    renameFilterRequested = Signal()
    archiveFilterRequested = Signal()
    restoreFilterRequested = Signal()
    filterHistoryRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._tags: list[Tag] = []
        self._filters: list[SavedFilter] = []
        self._available = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 20, 0, 0)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.archived = QCheckBox("Показывать архивные записи")
        self.archived.toggled.connect(self.changedArchive)
        layout.addWidget(self.archived)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        self.tag_table = QTableView()
        self.filter_table = QTableView()
        for table in (self.tag_table, self.filter_table):
            table.verticalHeader().hide()
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            table.horizontalHeader().setStretchLastSection(True)
        tag_page = QWidget()
        tag_layout = QVBoxLayout(tag_page)
        tag_buttons = QHBoxLayout()
        self.add_tag = QPushButton("Создать тег")
        self.rename_tag = QPushButton("Переименовать")
        self.archive_tag = QPushButton("В архив")
        self.restore_tag = QPushButton("Восстановить")
        self.tag_history = QPushButton("История")
        for widget, signal in (
            (self.add_tag, self.addTagRequested),
            (self.rename_tag, self.renameTagRequested),
            (self.archive_tag, self.archiveTagRequested),
            (self.restore_tag, self.restoreTagRequested),
            (self.tag_history, self.tagHistoryRequested),
        ):
            tag_buttons.addWidget(widget)
            widget.clicked.connect(signal)
        tag_layout.addLayout(tag_buttons)
        tag_layout.addWidget(self.tag_table, 1)
        tabs.addTab(tag_page, "Теги")
        filter_page = QWidget()
        filter_layout = QVBoxLayout(filter_page)
        filter_buttons = QHBoxLayout()
        info = QLabel(
            "Новые фильтры создаются в журнале: настройте условия и нажмите «Сохранить как…»."
        )
        info.setWordWrap(True)
        filter_layout.addWidget(info)
        self.rename_filter = QPushButton("Переименовать")
        self.archive_filter = QPushButton("В архив")
        self.restore_filter = QPushButton("Восстановить")
        self.filter_history = QPushButton("История")
        for widget, signal in (
            (self.rename_filter, self.renameFilterRequested),
            (self.archive_filter, self.archiveFilterRequested),
            (self.restore_filter, self.restoreFilterRequested),
            (self.filter_history, self.filterHistoryRequested),
        ):
            filter_buttons.addWidget(widget)
            widget.clicked.connect(signal)
        filter_layout.addLayout(filter_buttons)
        filter_layout.addWidget(self.filter_table, 1)
        tabs.addTab(filter_page, "Сохранённые фильтры")
        self.set_records([], [], False)

    def selected_tag(self) -> Tag | None:
        row = self.tag_table.currentIndex().row()
        return (
            self._tags[row]
            if self.tag_table.selectionModel().hasSelection() and 0 <= row < len(self._tags)
            else None
        )

    def selected_filter(self) -> SavedFilter | None:
        row = self.filter_table.currentIndex().row()
        return (
            self._filters[row]
            if self.filter_table.selectionModel().hasSelection() and 0 <= row < len(self._filters)
            else None
        )

    def set_records(self, tags: list[Tag], filters: list[SavedFilter], available: bool) -> None:
        self._tags = tags
        self._filters = filters
        self._available = available
        self.tag_model = replace_table(
            self.tag_table,
            ["Тег", "Состояние"],
            [[value.name, "Архив" if value.is_deleted else "Действует"] for value in tags],
            selection_changed=self._buttons,
        )
        self.filter_model = replace_table(
            self.filter_table,
            ["Фильтр", "Состояние"],
            [[value.name, "Архив" if value.is_deleted else "Действует"] for value in filters],
            selection_changed=self._buttons,
        )
        self.archived.setEnabled(available)
        self.add_tag.setEnabled(available)
        self.notice.setText(
            "Теги не заменяют категории и не меняют суммы. "
            "Архив сохраняет историю и прежние назначения."
            if available
            else (
                "Для тегов и сохранённых фильтров создайте проверенную копию "
                "и восстановите её в новую папку."
            )
        )
        self._buttons()

    def _buttons(self) -> None:
        for value, rename, archive, restore, history in (
            (
                self.selected_tag(),
                self.rename_tag,
                self.archive_tag,
                self.restore_tag,
                self.tag_history,
            ),
            (
                self.selected_filter(),
                self.rename_filter,
                self.archive_filter,
                self.restore_filter,
                self.filter_history,
            ),
        ):
            rename.setEnabled(self._available and value is not None)
            archive.setEnabled(self._available and value is not None and not value.is_deleted)
            restore.setEnabled(self._available and value is not None and value.is_deleted)
            history.setEnabled(self._available and value is not None)
