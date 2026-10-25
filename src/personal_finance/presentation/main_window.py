"""Рабочая основа: защищённые книги, справочники и резервирование."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from platformdirs import user_data_dir
from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from personal_finance.application.vault import VaultSession, create_vault, open_vault
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import ACCOUNT_LABELS, AccountKind, CategoryKind, validate_name
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
from personal_finance.presentation.jobs import Job

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
QLineEdit, QComboBox, QPlainTextEdit { background: white; border: 1px solid #c8d6d8;
                                    border-radius: 5px; padding: 8px; }
QTableWidget, QTreeWidget { background: white; border: 1px solid #dce5e8;
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
        self._jobs: set[Job] = set()
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
            label("Предварительная сборка 0.1 · Используйте только проверочные данные", "muted")
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
        self.navigation.addItems(["Обзор", "Счета", "Категории", "Защита и копии"])
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
        self.pages.addWidget(self._categories())
        self.pages.addWidget(self._security())
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
        layout.addWidget(
            label(
                "В этой сборке доступны справочники и резервирование. "
                "Учёт операций и денежных остатков пока недоступен.",
                "muted",
            )
        )
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
        self.account_table = QTableWidget(0, 4)
        self.account_table.setHorizontalHeaderLabels(["Название", "Тип", "Банк", "Валюта"])
        self.account_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.account_table.verticalHeader().hide()
        self.account_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.account_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.account_table.setAlternatingRowColors(True)
        layout.addWidget(self.account_table, 1)
        return page

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
        job = Job(action)
        self._jobs.add(job)
        job.signals.success.connect(success)
        job.signals.failure.connect(
            lambda message: QMessageBox.warning(self, "Действие не выполнено", message)
        )

        def finished() -> None:
            self._jobs.discard(job)
            self._busy = False
            self.root_stack.setEnabled(True)
            state = "Книга открыта" if self.session else "Книга закрыта"
            self.statusBar().showMessage(f"Работает без интернета · {state}")

        job.signals.finished.connect(finished)
        self._pool.start(job)

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
        self.account_table.setRowCount(0)
        self.category_tree.clear()
        self.book_title.clear()
        self.account_count.setText("0")
        self.category_count.setText("0")
        self.last_backup = "Резервная копия ещё не создана в этом сеансе"
        self.backup_overview.setText(self.last_backup)
        self.backup_status.setText(self.last_backup)
        self.root_stack.setCurrentIndex(0)
        self.statusBar().showMessage("Работает без интернета · Книга закрыта")

    def refresh(self) -> None:
        if not self.session:
            return
        institutions = {bank.id: bank.name for bank in self.session.catalog.institutions()}
        accounts = self.session.catalog.accounts()
        self.account_table.setRowCount(len(accounts))
        for row, account in enumerate(accounts):
            values = [
                account.name,
                ACCOUNT_LABELS[account.kind],
                institutions.get(account.institution_id or "", "—"),
                account.currency,
            ]
            for column, value in enumerate(values):
                self.account_table.setItem(row, column, QTableWidgetItem(value))
        categories = self.session.catalog.categories()
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
