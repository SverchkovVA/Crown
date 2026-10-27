"""Рабочая основа: защищённые книги, справочники и резервирование."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from platformdirs import user_data_dir
from PySide6.QtCore import QDate, QThreadPool, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from personal_finance import __version__
from personal_finance.application.vault import VaultSession, create_vault, open_vault
from personal_finance.domain.errors import FinanceError, ValidationError
from personal_finance.domain.ledger import OPERATION_LABELS, Operation, OperationKind
from personal_finance.domain.models import (
    ACCOUNT_LABELS,
    Account,
    AccountKind,
    CategoryKind,
    validate_name,
)
from personal_finance.domain.periods import AccountingMonth
from personal_finance.domain.reconciliation import Reconciliation
from personal_finance.domain.refunds import RefundCandidate
from personal_finance.infrastructure.backup.container import (
    create_backup,
    restore_backup,
    verify_backup,
)
from personal_finance.presentation.dialogs import (
    CreateDialog,
    NameDialog,
    OpenDialog,
    PasswordDialog,
    RecoveryDialog,
)
from personal_finance.presentation.jobs import Job, JobSignals
from personal_finance.presentation.ledger_dialogs import (
    OperationDialog,
    OperationHistoryDialog,
    category_title,
    format_money,
)
from personal_finance.presentation.periods import (
    PeriodChangeDialog,
    PeriodHistoryDialog,
    PeriodPage,
)
from personal_finance.presentation.reconciliation import (
    ReconciliationInputDialog,
    ReconciliationResultDialog,
)
from personal_finance.presentation.tables import ACCOUNT_HEADERS, JOURNAL_HEADERS, replace_table

STYLE = """
QMainWindow, QDialog { background: #f5f7f9; color: #172b35; }
QWidget { font-family: 'Segoe UI', 'DejaVu Sans'; font-size: 14px; color: #172b35; }
QLabel#heading { font-size: 29px; font-weight: 600; }
QLabel#muted { color: #617580; }
QLabel#brand { font-size: 23px; font-weight: 600; color: #12634b; }
QLabel#number { font-size: 28px; font-weight: 600; color: #12634b; }
QFrame#card { background: white; border: 1px solid #dce5e8; border-radius: 12px; }
QFrame#sidebar { background: #eaf0ed; border: 0; }
QPushButton { background: white; border: 1px solid #c8d6d8; border-radius: 7px;
              padding: 10px 16px; }
QPushButton:hover { background: #e8f3ef; border-color: #91b7a8; }
QPushButton:disabled { color: #92a19f; background: #eef1ef; }
QPushButton#primary { background: #17694f; color: white; border: 1px solid #17694f; }
QPushButton#primary:hover { background: #11533e; }
QPushButton#primary:disabled { background: #8aa99d; border-color: #8aa99d; }
QListWidget { border: 0; background: transparent; outline: 0; }
QListWidget::item { padding: 13px; margin: 3px 0; border-radius: 7px; }
QListWidget::item:selected { background: #d2e6db; color: #104b38; }
QLineEdit, QComboBox, QPlainTextEdit, QDateEdit { background: white; border: 1px solid #c8d6d8;
                                    border-radius: 5px; padding: 8px; }
QTableView, QTableWidget, QTreeWidget { background: white; alternate-background-color: #f5f8f6;
                          selection-background-color: #d2e6db; selection-color: #104b38;
                          border: 1px solid #dce5e8;
                          border-radius: 8px; gridline-color: #edf1f2; }
QHeaderView::section { background: #edf3f1; border: 0; padding: 12px; font-weight: 600; }
QStatusBar { background: #eaf0ed; }
"""


def label(text: str, name: str = "") -> QLabel:
    result = QLabel(text)
    result.setWordWrap(True)
    if name:
        result.setObjectName(name)
    return result


def button(text: str, callback: Callable[..., Any], *, primary: bool = False) -> QPushButton:
    result = QPushButton(text)
    if primary:
        result.setObjectName("primary")
    result.clicked.connect(callback)
    return result


def accept_named_form(dialog: QDialog, field: QLineEdit) -> None:
    try:
        validate_name(field.text())
    except ValidationError as exc:
        QMessageBox.warning(dialog, "Проверьте название", str(exc))
        return
    dialog.accept()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Личные финансы")
        self.resize(1160, 760)
        self.setMinimumSize(920, 620)
        self.setStyleSheet(STYLE)
        self.session: VaultSession | None = None
        self._account_records: list[Account] = []
        self._journal_operations: list[Operation] = []
        self._refund_candidates: list[RefundCandidate] = []
        self._closed_months: set[AccountingMonth] = set()
        self._jobs: set[Job] = set()
        self._job_callbacks: dict[JobSignals, Callable[[Any], object]] = {}
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._busy = False
        self.last_backup = "Резервная копия ещё не создана в этом сеансе"
        self.root_stack = QStackedWidget()
        self.setCentralWidget(self.root_stack)
        self.root_stack.addWidget(self._welcome())
        self.root_stack.addWidget(self._workspace())
        self.statusBar().showMessage("Работает без интернета · Книга закрыта")

    def _welcome(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(100, 60, 100, 60)
        layout.addStretch()
        layout.addWidget(label("ЛИЧНЫЕ ФИНАНСЫ", "brand"))
        layout.addSpacing(20)
        layout.addWidget(label("Ваши данные — на вашем компьютере", "heading"))
        layout.addWidget(
            label(
                "Создайте финансовую книгу или откройте существующую. "
                "Для работы с книгой интернет не нужен.",
                "muted",
            )
        )
        layout.addSpacing(28)
        row = QHBoxLayout()
        row.addWidget(button("Создать книгу", self.create_book, primary=True))
        row.addWidget(button("Открыть книгу", self.open_book))
        row.addStretch()
        layout.addLayout(row)
        layout.addSpacing(12)
        restore = button("Восстановить из резервной копии", self.restore_book)
        restore.setMaximumWidth(355)
        layout.addWidget(restore)
        layout.addSpacing(32)
        layout.addWidget(
            label(
                f"Предварительная сборка {__version__} · Используйте только проверочные данные",
                "muted",
            )
        )
        layout.addStretch()
        return page

    def _workspace(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(230)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 28, 20, 20)
        side.addWidget(label("Личные\nфинансы", "brand"))
        side.addSpacing(25)
        self.navigation = QListWidget()
        self.navigation.addItems(
            ["Обзор", "Счета", "Операции", "Категории", "Защита и копии", "Периоды"]
        )
        side.addWidget(self.navigation, 1)
        side.addWidget(label("Локальная книга\nЗашифрованное хранение", "muted"))
        layout.addWidget(sidebar)
        content = QVBoxLayout()
        content.setContentsMargins(32, 26, 32, 24)
        top = QHBoxLayout()
        self.book_title = label("", "heading")
        top.addWidget(self.book_title, 1)
        top.addWidget(button("Закрыть книгу", self.lock_book))
        content.addLayout(top)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._overview())
        self.pages.addWidget(self._accounts())
        self.pages.addWidget(self._journal())
        self.pages.addWidget(self._categories())
        self.pages.addWidget(self._security())
        self.period_page = PeriodPage()
        self.period_page.closeRequested.connect(self.close_period)
        self.period_page.reopenRequested.connect(self.reopen_period)
        self.period_page.historyRequested.connect(self.period_history)
        self.pages.addWidget(self.period_page)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        content.addWidget(self.pages, 1)
        layout.addLayout(content, 1)
        return page

    def _overview(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 20, 0, 0)
        layout.addWidget(label("Начните со счетов и категорий", "muted"))
        cards = QHBoxLayout()
        self.account_count = label("0", "number")
        self.category_count = label("0", "number")
        for title, value in [
            ("Счета", self.account_count),
            ("Категории", self.category_count),
            ("Основная валюта", label("RUB", "number")),
        ]:
            card = QFrame()
            card.setObjectName("card")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(22, 20, 22, 20)
            card_layout.addWidget(label(title, "muted"))
            card_layout.addWidget(value)
            cards.addWidget(card)
        layout.addSpacing(16)
        layout.addLayout(cards)
        layout.addSpacing(24)
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.addWidget(label("Сохранность данных", "brand"))
        card_layout.addWidget(
            label(
                "Книга защищена парольной фразой. Резервную копию можно проверить "
                "и восстановить в отдельную папку."
            )
        )
        self.backup_overview = label(self.last_backup, "muted")
        card_layout.addWidget(self.backup_overview)
        actions = QHBoxLayout()
        actions.addWidget(button("Создать копию", self.backup_book, primary=True))
        actions.addWidget(button("Проверить книгу", self.check_book))
        actions.addStretch()
        card_layout.addLayout(actions)
        layout.addWidget(card)
        layout.addSpacing(16)
        self.feature_note = label("", "muted")
        layout.addWidget(self.feature_note)
        layout.addStretch()
        return page

    def _accounts(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 20, 0, 0)
        row = QHBoxLayout()
        row.addWidget(label("Банки, наличные и ваши счета", "muted"), 1)
        row.addWidget(button("Добавить банк", self.add_bank))
        row.addWidget(button("Добавить счёт", self.add_account, primary=True))
        layout.addLayout(row)
        balance_row = QHBoxLayout()
        balance_row.addWidget(label("Остаток на дату"))
        self.balance_date = QDateEdit(QDate.currentDate())
        self.balance_date.setCalendarPopup(True)
        self.balance_date.setDisplayFormat("dd.MM.yyyy")
        self.balance_date.setMinimumDate(QDate(1, 1, 1))
        self.balance_date.dateChanged.connect(self.refresh)
        balance_row.addWidget(self.balance_date)
        balance_row.addStretch()
        self.opening_button = button("Начальный остаток", self.add_opening_balance)
        balance_row.addWidget(self.opening_button)
        self.reconcile_button = button("Сверить остаток", self.reconcile_account)
        balance_row.addWidget(self.reconcile_button)
        layout.addLayout(balance_row)
        self.balance_notice = label("", "muted")
        layout.addWidget(self.balance_notice)
        self.account_table = QTableView()
        self.account_model = replace_table(self.account_table, ACCOUNT_HEADERS, [])
        self.account_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.account_table.verticalHeader().hide()
        self.account_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.account_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.account_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.account_table.setAlternatingRowColors(True)
        layout.addWidget(self.account_table, 1)
        return page

    def _journal(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 20, 0, 0)
        self.journal_notice = label(
            "Переводы исключены из доходов и расходов; комиссия — расход. "
            "Возврат уменьшает расходы.",
            "muted",
        )
        layout.addWidget(self.journal_notice)
        actions = QHBoxLayout()
        self.add_operation_button = button(
            "Добавить операцию", self.add_default_operation, primary=True
        )
        self.edit_operation_button = button("Исправить", self.edit_operation)
        self.delete_operation_button = button("Удалить", self.delete_operation)
        self.history_operation_button = button("История", self.operation_history)
        for action in (
            self.add_operation_button,
            self.edit_operation_button,
            self.delete_operation_button,
            self.history_operation_button,
        ):
            actions.addWidget(action)
        self.show_deleted = QCheckBox("Показывать удалённые")
        self.show_deleted.toggled.connect(self.refresh)
        actions.addWidget(self.show_deleted)
        layout.addLayout(actions)
        refund_row = QHBoxLayout()
        self.refund_button = button("Оформить возврат", self.add_refund)
        refund_row.addWidget(self.refund_button)
        refund_row.addWidget(
            label("Возврат связан с расходом и уменьшает расходы на дату получения.", "muted"), 1
        )
        layout.addLayout(refund_row)
        self.operation_table = QTableView()
        self.operation_model = replace_table(self.operation_table, JOURNAL_HEADERS, [])
        self.operation_table.verticalHeader().hide()
        self.operation_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.operation_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.operation_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.operation_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        for column in (2, 3, 6):
            self.operation_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.Stretch
            )
        self.operation_table.selectionModel().selectionChanged.connect(self._update_journal_actions)
        self.operation_table.doubleClicked.connect(self.edit_operation)
        layout.addWidget(self.operation_table, 1)
        return page

    def _selected_operation(self) -> Operation | None:
        if not self.operation_table.selectionModel().hasSelection():
            return None
        row = self.operation_table.currentIndex().row()
        if not 0 <= row < len(self._journal_operations):
            return None
        return self._journal_operations[row]

    @Slot()
    def _update_journal_actions(self) -> None:
        available = self.session is not None and self.session.ledger is not None
        self.add_operation_button.setEnabled(available and bool(self._account_records))
        self.show_deleted.setEnabled(available)
        selected = self._selected_operation() if available else None
        self.history_operation_button.setEnabled(selected is not None)
        editable = (
            selected is not None
            and not selected.is_deleted
            and AccountingMonth.from_date(selected.draft.occurred_on) not in self._closed_months
        )
        self.edit_operation_button.setEnabled(editable)
        self.delete_operation_button.setEnabled(editable)
        eligible = {
            candidate.expense.id
            for candidate in self._refund_candidates
            if candidate.amount_remaining.minor > 0
        }
        can_refund = (
            available
            and self.session is not None
            and self.session.ledger is not None
            and self.session.ledger.supports_refunds
            and bool(eligible)
        )
        if selected is not None and selected.draft.kind == OperationKind.EXPENSE:
            can_refund = can_refund and not selected.is_deleted and selected.id in eligible
        self.refund_button.setEnabled(can_refund)
        self.refund_button.setToolTip(
            "Оформить полный или частичный возврат по расходу."
            if can_refund
            else "Нужен действующий расход с остатком к возврату. "
            "Для новых функций старой книги восстановите копию в новую папку."
        )

    @Slot()
    def add_default_operation(self) -> None:
        self.add_operation()

    @Slot()
    def add_refund(self) -> None:
        if (
            self.session is not None
            and self.session.ledger is not None
            and self.session.ledger.supports_refunds
        ):
            self.add_operation(OperationKind.REFUND)

    @Slot()
    def reconcile_account(self) -> None:
        session = self.session
        if session is None or session.ledger is None:
            return
        selected = self.account_table.currentIndex().row()
        account_id = (
            self._account_records[selected].id
            if self.account_table.selectionModel().hasSelection()
            and 0 <= selected < len(self._account_records)
            else None
        )
        qdate = self.balance_date.date()
        dialog = ReconciliationInputDialog(
            self._account_records,
            date(qdate.year(), qdate.month(), qdate.day()),
            self,
            account_id=account_id,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_request is None:
            return
        identifier, as_of, statement = dialog.result_request
        ledger = session.ledger
        names = {account.id: account.name for account in self._account_records}

        def show(result: Reconciliation) -> None:
            ReconciliationResultDialog(result, names.get(identifier, "Счёт"), self).exec()

        self._job(lambda: ledger.reconcile(identifier, as_of, statement), show, "Сверка остатка…")

    @Slot()
    def add_opening_balance(self) -> None:
        self.add_operation(OperationKind.OPENING)

    def add_operation(self, kind: OperationKind = OperationKind.EXPENSE) -> None:
        session = self.session
        if session is None or session.ledger is None:
            return
        if not self._account_records:
            QMessageBox.information(
                self, "Сначала добавьте счёт", "Создайте счёт в разделе «Счета»."
            )
            return
        selected = self.account_table.currentIndex().row()
        account_id = (
            self._account_records[selected].id
            if kind == OperationKind.OPENING
            and self.account_table.selectionModel().hasSelection()
            and 0 <= selected < len(self._account_records)
            else None
        )
        selected_operation = self._selected_operation()
        refund_of_id = (
            selected_operation.id
            if kind == OperationKind.REFUND
            and selected_operation is not None
            and selected_operation.draft.kind == OperationKind.EXPENSE
            else None
        )
        dialog = OperationDialog(
            self._account_records,
            session.catalog.categories(),
            session.catalog.institutions(),
            self,
            kind=kind,
            account_id=account_id,
            allow_splits=session.ledger.supports_splits,
            allow_refunds=session.ledger.supports_refunds,
            refund_candidates=self._refund_candidates,
            refund_of_id=refund_of_id,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_draft is None:
            return
        draft, ledger, command_id = dialog.result_draft, session.ledger, str(uuid4())
        self._job(
            lambda: ledger.add(draft, command_id=command_id),
            lambda _: self.refresh(),
            "Сохранение операции и проводок…",
        )

    @Slot()
    def edit_operation(self) -> None:
        session, selected = self.session, self._selected_operation()
        if session is None or session.ledger is None or selected is None or selected.is_deleted:
            return
        dialog = OperationDialog(
            self._account_records,
            session.catalog.categories(),
            session.catalog.institutions(),
            self,
            existing=selected,
            allow_splits=session.ledger.supports_splits,
            allow_refunds=session.ledger.supports_refunds,
            refund_candidates=session.ledger.refund_candidates(
                excluding_refund_id=selected.id
                if selected.draft.kind == OperationKind.REFUND
                else None,
            )
            if session.ledger.supports_refunds
            else [],
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_draft is None:
            return
        draft, ledger, command_id = dialog.result_draft, session.ledger, str(uuid4())
        self._job(
            lambda: ledger.replace(
                selected.id,
                selected.revision_id,
                draft,
                command_id=command_id,
            ),
            lambda _: self.refresh(),
            "Сохранение новой версии операции…",
        )

    @Slot()
    def delete_operation(self) -> None:
        session, selected = self.session, self._selected_operation()
        if session is None or session.ledger is None or selected is None or selected.is_deleted:
            return
        answer = QMessageBox.question(
            self,
            "Удалить финансовую операцию",
            "Убрать операцию из расчёта остатков? Её прежние версии останутся в истории.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        ledger, command_id = session.ledger, str(uuid4())
        self._job(
            lambda: ledger.delete(selected.id, selected.revision_id, command_id=command_id),
            lambda _: self.refresh(),
            "Удаление операции с сохранением истории…",
        )

    @Slot()
    def operation_history(self) -> None:
        session, selected = self.session, self._selected_operation()
        if session is None or session.ledger is None or selected is None:
            return
        try:
            versions = session.ledger.history(selected.id)
            related_versions = []
            for identifier in {
                version.draft.refund_of
                for version in versions
                if version.draft.refund_of is not None
            }:
                related_versions.extend(session.ledger.history(identifier))
        except FinanceError as exc:
            QMessageBox.warning(self, "Действие не выполнено", str(exc))
            return
        OperationHistoryDialog(
            versions,
            self,
            accounts=self._account_records,
            categories=session.catalog.categories(),
            related_versions=related_versions,
        ).exec()

    @Slot()
    def close_period(self) -> None:
        self._change_period(True)

    @Slot()
    def reopen_period(self) -> None:
        self._change_period(False)

    def _change_period(self, is_closed: bool) -> None:
        session = self.session
        if session is None or session.periods is None:
            return
        month = self.period_page.month()
        current = self.period_page.selected_state()
        dialog = PeriodChangeDialog(month, is_closed, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_reason is None:
            return
        periods, reason, command = session.periods, dialog.result_reason, str(uuid4())
        expected = current.revision_id if current is not None else None
        if not is_closed and expected is None:
            return

        def save() -> object:
            if is_closed:
                return periods.close(
                    month, expected_revision_id=expected, reason=reason, command_id=command
                )
            assert expected is not None
            return periods.reopen(month, expected, reason=reason, command_id=command)

        self._job(save, lambda _: self.refresh(), "Сохранение состояния периода…")

    @Slot()
    def period_history(self) -> None:
        session = self.session
        if session is None or session.periods is None:
            return
        try:
            versions = session.periods.history(self.period_page.month())
        except FinanceError as exc:
            QMessageBox.warning(self, "Действие не выполнено", str(exc))
            return
        PeriodHistoryDialog(versions, self).exec()

    def _categories(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 20, 0, 0)
        row = QHBoxLayout()
        row.addWidget(label("Доходы, расходы и подкатегории", "muted"), 1)
        row.addWidget(button("Добавить категорию", self.add_category, primary=True))
        layout.addLayout(row)
        self.category_tree = QTreeWidget()
        self.category_tree.setHeaderLabels(["Категория", "Тип"])
        self.category_tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.category_tree, 1)
        return page

    def _security(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 20, 0, 0)
        layout.addWidget(label("Резервные копии и проверка", "brand"))
        layout.addWidget(
            label(
                "Копия имеет собственную парольную фразу. Для восстановления "
                "не нужен исходный компьютер. Храните хотя бы одну копию на другом носителе."
            )
        )
        layout.addSpacing(20)
        layout.addWidget(button("Создать зашифрованную копию", self.backup_book, primary=True))
        layout.addWidget(button("Проверить резервную копию", self.verify_copy))
        layout.addWidget(button("Восстановить в новую папку", self.restore_book))
        layout.addWidget(button("Проверить целостность книги", self.check_book))
        self.backup_status = label(self.last_backup, "muted")
        layout.addWidget(self.backup_status)
        layout.addSpacing(20)
        layout.addWidget(
            label(
                "Автоматическое резервирование и блокировка по таймеру ещё не включены. "
                "Закрывайте книгу кнопкой и создавайте копии вручную.",
                "muted",
            )
        )
        layout.addStretch()
        return page

    def _job(
        self, action: Callable[[], object], success: Callable[[Any], object], message: str
    ) -> None:
        if self._busy:
            return
        self._busy = True
        self.root_stack.setEnabled(False)
        self.statusBar().showMessage(message)
        job = Job(action, self)
        self._jobs.add(job)
        self._job_callbacks[job.signals] = success
        # Только QObject-слоты принимают сигналы рабочего потока. Пользовательские
        # callback вызываются уже в GUI, без циклов Job -> closure -> Job/window.
        job.signals.success.connect(self._job_succeeded)
        job.signals.failure.connect(self._job_failed)
        job.signals.finished.connect(self._job_finished)
        self._pool.start(job)

    @Slot(object)
    def _job_succeeded(self, result: object) -> None:
        signals = self.sender()
        if isinstance(signals, JobSignals):
            callback = self._job_callbacks.get(signals)
            if callback is not None:
                callback(result)

    @Slot(str)
    def _job_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Действие не выполнено", message)

    @Slot()
    def _job_finished(self) -> None:
        signals = self.sender()
        if not isinstance(signals, JobSignals):
            return
        # finished — последняя команда run(). Дождаться выхода QRunnable до
        # удаления его сигналов; ожидание Qt освобождает GIL.
        self._pool.waitForDone()
        self._job_callbacks.pop(signals, None)
        for job in tuple(self._jobs):
            if job.signals is signals:
                self._jobs.discard(job)
        signals.success.disconnect(self._job_succeeded)
        signals.failure.disconnect(self._job_failed)
        signals.finished.disconnect(self._job_finished)
        signals.deleteLater()
        self._busy = False
        self.root_stack.setEnabled(True)
        state = "Книга открыта" if self.session else "Книга закрыта"
        self.statusBar().showMessage(f"Работает без интернета · {state}")

    def activate_session(self, session: VaultSession) -> None:
        if self.session and self.session is not session:
            self.session.close()
        self.session = session
        self.last_backup = "Резервная копия ещё не создана в этом сеансе"
        self.book_title.setText(session.name)
        self.refresh()
        self.root_stack.setCurrentIndex(1)
        self.statusBar().showMessage("Работает без интернета · Книга открыта")

    def _created(self, result: tuple[VaultSession, str]) -> None:
        self.activate_session(result[0])
        RecoveryDialog(result[1], self).exec()

    def create_book(self) -> None:
        dialog = CreateDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        path, name, password = Path(dialog.path.text()), dialog.name.text(), dialog.password.text()

        def create() -> object:
            path.parent.mkdir(parents=True, exist_ok=True)
            return create_vault(path, name, password)

        self._job(create, self._created, "Создание защищённой книги…")

    def open_book(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Выберите папку книги (.pfvault)")
        if not path:
            return
        dialog = OpenDialog(Path(path), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        credential, recovery = dialog.credential.text(), dialog.recovery.isChecked()
        if recovery:
            credential = credential.strip()
        self._job(
            lambda: open_vault(Path(path), credential, recovery=recovery),
            self.activate_session,
            "Открытие и проверка книги…",
        )

    def restore_book(self) -> None:
        source, _ = QFileDialog.getOpenFileName(self, "Резервная копия", "", "Копия (*.pfbackup)")
        if not source:
            return
        dialog = CreateDialog(self, restoring=True)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        target = Path(dialog.path.text())
        backup_password, new_password = dialog.backup_password.text(), dialog.password.text()

        def restore() -> object:
            target.parent.mkdir(parents=True, exist_ok=True)
            return restore_backup(Path(source), target, backup_password, new_password)

        self._job(restore, self._created, "Проверка и восстановление в новую папку…")

    def lock_book(self) -> None:
        if self.session:
            self.session.close()
            self.session = None
        self.account_model = replace_table(self.account_table, ACCOUNT_HEADERS, [])
        self.operation_model = replace_table(self.operation_table, JOURNAL_HEADERS, [])
        self._journal_operations = []
        self._account_records = []
        self.reconcile_button.setEnabled(False)
        self._refund_candidates = []
        self._closed_months = set()
        self.period_page.set_states([], False)
        self._update_journal_actions()
        self.category_tree.clear()
        self.book_title.clear()
        self.account_count.setText("0")
        self.category_count.setText("0")
        self.last_backup = "Резервная копия ещё не создана в этом сеансе"
        self.backup_overview.setText(self.last_backup)
        self.backup_status.setText(self.last_backup)
        self.root_stack.setCurrentIndex(0)
        self.statusBar().showMessage("Работает без интернета · Книга закрыта")

    @Slot()
    def refresh(self) -> None:
        if not self.session:
            return
        session = self.session
        institutions = {bank.id: bank.name for bank in session.catalog.institutions()}
        accounts = session.catalog.accounts()
        self._account_records = accounts
        categories = session.catalog.categories()
        ledger = session.ledger
        self.balance_date.setEnabled(ledger is not None)
        self.opening_button.setEnabled(ledger is not None)
        self.reconcile_button.setEnabled(ledger is not None and bool(accounts))
        amounts = {}
        if ledger is not None:
            qdate = self.balance_date.date()
            amounts = {
                balance.account_id: format_money(balance.amount, include_currency=False)
                for balance in ledger.balances(date(qdate.year(), qdate.month(), qdate.day()))
            }
        self.balance_notice.setText(
            "Остаток включает начальную позицию и принятые операции по выбранную дату включительно."
            if ledger is not None
            else "Для остатков создайте копию в «Защита и копии» и восстановите её в новую папку."
        )
        account_rows = [
            [
                account.name,
                ACCOUNT_LABELS[account.kind],
                institutions.get(account.institution_id or "", "—"),
                account.currency,
                amounts.get(account.id, "—"),
            ]
            for account in accounts
        ]
        self.account_model = replace_table(
            self.account_table,
            ACCOUNT_HEADERS,
            account_rows,
            right_columns=(4,),
        )
        self.category_tree.clear()
        nodes = {
            category.id: QTreeWidgetItem(
                [category.name, "Расход" if category.kind == CategoryKind.EXPENSE else "Доход"]
            )
            for category in categories
        }
        for category in categories:
            node = nodes[category.id]
            if category.parent_id in nodes:
                nodes[category.parent_id].addChild(node)
            else:
                self.category_tree.addTopLevelItem(node)
        self.category_tree.expandAll()
        period_states = session.periods.states() if session.periods is not None else []
        self._closed_months = {value.month for value in period_states if value.is_closed}
        self.period_page.set_states(period_states, session.periods is not None)
        self._journal_operations = (
            ledger.operations(include_deleted=self.show_deleted.isChecked())
            if ledger is not None
            else []
        )
        self._refund_candidates = (
            ledger.refund_candidates() if ledger is not None and ledger.supports_refunds else []
        )
        account_names = {account.id: account.name for account in accounts}
        category_names = {
            category.id: category_title(category, categories) for category in categories
        }
        journal_rows = []
        for operation in self._journal_operations:
            draft = operation.draft
            target = (
                account_names.get(draft.destination_account_id or "", "—")
                if draft.kind == OperationKind.TRANSFER
                else category_names.get(draft.category_id or "", "Начальный остаток")
            )
            if draft.parts:
                target = "; ".join(
                    f"{category_names.get(part.category_id, '—')}: {format_money(part.amount)}"
                    for part in draft.parts
                )
            journal_rows.append(
                [
                    draft.occurred_on.strftime("%d.%m.%Y"),
                    OPERATION_LABELS[draft.kind],
                    account_names.get(draft.account_id, "—"),
                    target,
                    format_money(draft.amount),
                    format_money(draft.fee) if draft.fee.minor else "—",
                    draft.note,
                    "Удалена" if operation.is_deleted else "Учтена",
                ]
            )
        self.operation_model = replace_table(
            self.operation_table,
            JOURNAL_HEADERS,
            journal_rows,
            selection_changed=self._update_journal_actions,
        )
        self.journal_notice.setText(
            (
                "Переводы исключены из доходов и расходов; комиссия — расход. "
                "Возврат уменьшает расходы."
                + (
                    " Для новых функций восстановите копию этой книги в новую папку."
                    if not ledger.supports_refunds
                    else ""
                )
            )
            if ledger is not None
            else "Для журнала создайте резервную копию и восстановите её в новую папку. "
            "Исходная книга сохранится."
        )
        self.feature_note.setText(
            "Доступны ручные операции и остатки. "
            "OCR, импорт, аналитика и планирование ещё не включены."
            if ledger is not None
            else "Эта книга открыта со справочниками. "
            "Для финансовых операций восстановите её копию в новую папку."
        )
        self._update_journal_actions()
        self.account_count.setText(str(len(accounts)))
        self.category_count.setText(str(len(categories)))
        self.backup_overview.setText(self.last_backup)
        self.backup_status.setText(self.last_backup)

    def add_bank(self) -> None:
        session = self.session
        if not session:
            return
        dialog = NameDialog(self)
        dialog.setWindowTitle("Добавить банк")
        dialog.setLabelText("Название банка или организации")
        dialog.setOkButtonText("Добавить")
        dialog.setCancelButtonText("Отмена")
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = dialog.textValue()
            self._job(
                lambda: session.catalog.add_institution(name),
                lambda _: self.refresh(),
                "Сохранение банка…",
            )

    def add_account(self) -> None:
        session = self.session
        if not session:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Добавить счёт")
        dialog.setMinimumWidth(470)
        form = QFormLayout(dialog)
        name = QLineEdit()
        kind = QComboBox()
        for value, title in ACCOUNT_LABELS.items():
            kind.addItem(title, value.value)
        institution = QComboBox()
        institution.addItem("Без банка", None)
        for bank in session.catalog.institutions():
            institution.addItem(bank.name, bank.id)
        kind.currentIndexChanged.connect(
            lambda _: institution.setEnabled(kind.currentData() != AccountKind.CASH.value)
        )
        form.addRow("Название", name)
        form.addRow("Тип", kind)
        form.addRow("Банк", institution)
        form.addRow("Валюта", label("RUB"))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(lambda: accept_named_form(dialog, name))
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            title, account_kind = name.text(), AccountKind(kind.currentData())
            bank_id = None if account_kind == AccountKind.CASH else institution.currentData()
            self._job(
                lambda: session.catalog.add_account(title, account_kind, bank_id),
                lambda _: self.refresh(),
                "Сохранение счёта…",
            )

    def add_category(self) -> None:
        session = self.session
        if not session:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Добавить категорию")
        dialog.setMinimumWidth(470)
        form = QFormLayout(dialog)
        name, kind, parent = QLineEdit(), QComboBox(), QComboBox()
        kind.addItem("Расход", CategoryKind.EXPENSE.value)
        kind.addItem("Доход", CategoryKind.INCOME.value)
        categories = session.catalog.categories()

        def update_parents() -> None:
            parent.clear()
            parent.addItem("Без родительской категории", None)
            for category in categories:
                if category.kind.value == kind.currentData():
                    parent.addItem(category.name, category.id)

        kind.currentIndexChanged.connect(update_parents)
        update_parents()
        form.addRow("Название", name)
        form.addRow("Тип", kind)
        form.addRow("Родительская категория", parent)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(lambda: accept_named_form(dialog, name))
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            title, category_kind, parent_id = (
                name.text(),
                CategoryKind(kind.currentData()),
                parent.currentData(),
            )
            self._job(
                lambda: session.catalog.add_category(title, category_kind, parent_id),
                lambda _: self.refresh(),
                "Сохранение категории…",
            )

    def backup_book(self) -> None:
        session = self.session
        if not session:
            return
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        folder = Path(user_data_dir("PersonalFinance", appauthor=False)) / "backups"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Новая резервная копия",
            str(folder / f"copy-{stamp}-{uuid4().hex[:4]}.pfbackup"),
            "Защищённая копия (*.pfbackup)",
        )
        if not path:
            return
        dialog = PasswordDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        password, destination = dialog.password.text(), Path(path)

        def backup() -> object:
            destination.parent.mkdir(parents=True, exist_ok=True)
            return create_backup(session, destination, password)

        def completed(result: Path) -> None:
            self.last_backup = f"Копия создана и проверена: {result.name}"
            self.refresh()
            QMessageBox.information(self, "Копия готова", f"Создана и проверена копия:\n{result}")

        self._job(backup, completed, "Создание и полная проверка копии…")

    def verify_copy(self) -> None:
        source, _ = QFileDialog.getOpenFileName(self, "Проверить копию", "", "Копия (*.pfbackup)")
        if not source:
            return
        dialog = PasswordDialog(self, confirm=False)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        password = dialog.password.text()
        self._job(
            lambda: verify_backup(Path(source), password),
            lambda _: QMessageBox.information(
                self, "Проверка завершена", "Все блоки резервной копии прочитаны и проверены."
            ),
            "Проверка резервной копии…",
        )

    def check_book(self) -> None:
        session = self.session
        if session:
            self._job(
                session.check_integrity,
                lambda _: QMessageBox.information(
                    self, "Проверка завершена", "Целостность базы, связей и истории подтверждена."
                ),
                "Проверка книги…",
            )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._busy:
            self.statusBar().showMessage("Дождитесь завершения текущего действия перед закрытием.")
            event.ignore()
            return
        self.lock_book()
        self._pool.waitForDone()
        event.accept()
