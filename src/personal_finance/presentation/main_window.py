"""Рабочая основа: защищённые книги, справочники и резервирование."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from functools import partial
from html import escape
from pathlib import Path
from typing import Any
from uuid import uuid4

from platformdirs import user_data_dir
from PySide6.QtCore import QDate, Qt, QThreadPool, Slot
from PySide6.QtGui import QAction, QCloseEvent
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
from personal_finance.application.analytics import Analytics
from personal_finance.application.classification import (
    PreparedImportCategories,
    prepare_import_categories,
)
from personal_finance.application.importing import read_statement
from personal_finance.application.ocr_cards import (
    CardRecognition,
    OcrCardPreview,
    OcrHistoryPreview,
)
from personal_finance.application.ocr_review import OcrReviewDefaults
from personal_finance.application.vault import VaultSession, create_vault, open_vault
from personal_finance.domain.analytics import IncomeExpenseReport, ReportPeriod
from personal_finance.domain.errors import FinanceError, ValidationError
from personal_finance.domain.exporting import ExportPreview
from personal_finance.domain.imports import ImportPreview, ImportResult
from personal_finance.domain.ledger import OPERATION_LABELS, Operation, OperationKind
from personal_finance.domain.models import (
    ACCOUNT_LABELS,
    Account,
    AccountKind,
    Category,
    CategoryKind,
    validate_name,
)
from personal_finance.domain.ocr_cards import ReviewedOcrCard
from personal_finance.domain.ocr_imports import OcrImportPreview, OcrImportResult, OcrImportStatus
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.domain.organization import SavedFilter, Tag
from personal_finance.domain.periods import AccountingMonth
from personal_finance.domain.query import OperationQuery
from personal_finance.domain.reconciliation import Reconciliation
from personal_finance.domain.refunds import RefundCandidate
from personal_finance.infrastructure.backup.container import (
    create_backup,
    restore_backup,
    verify_backup,
)
from personal_finance.infrastructure.ocr_preferences import LocalOcrPreferences
from personal_finance.presentation.analytics import IncomeExpenseDialog, ReportPeriodDialog
from personal_finance.presentation.classification import (
    CategoryRuleDialog,
    CategoryRuleHistoryDialog,
    CategoryRulePage,
)
from personal_finance.presentation.dialogs import (
    CreateDialog,
    NameDialog,
    OpenDialog,
    PasswordDialog,
    RecoveryDialog,
)
from personal_finance.presentation.export_dialogs import (
    ExportOptions,
    ExportOptionsDialog,
    ExportPreviewDialog,
)
from personal_finance.presentation.import_dialogs import (
    ImportOptions,
    ImportOptionsDialog,
    ImportPreviewDialog,
)
from personal_finance.presentation.jobs import Job, JobSignals
from personal_finance.presentation.ledger_dialogs import (
    OperationDialog,
    OperationHistoryDialog,
    category_title,
    format_money,
)
from personal_finance.presentation.ocr_batch import OcrBatchDialog
from personal_finance.presentation.ocr_confirmation import OcrConfirmationDialog, OcrOperationDialog
from personal_finance.presentation.ocr_history import OcrHistoryDialog
from personal_finance.presentation.ocr_preview import OcrPreviewDialog
from personal_finance.presentation.ocr_settings import OcrSettingsDialog
from personal_finance.presentation.organization import (
    DefinitionHistoryDialog,
    OrganizationPage,
    describe_query,
)
from personal_finance.presentation.periods import (
    PeriodChangeDialog,
    PeriodHistoryDialog,
    PeriodPage,
)
from personal_finance.presentation.query_dialog import QueryDialog
from personal_finance.presentation.reconciliation import (
    ReconciliationInputDialog,
    ReconciliationResultDialog,
)
from personal_finance.presentation.tables import ACCOUNT_HEADERS, JOURNAL_HEADERS, replace_table

STYLE = """
QMainWindow, QDialog, QWidget {
    background-color: #f5f7f9; color: #172b35;
    font-family: 'Segoe UI', 'DejaVu Sans'; font-size: 14px;
}
QLabel { background: transparent; }
QLabel#heading { font-size: 29px; font-weight: 600; }
QLabel#muted { color: #4e626e; }
QLabel#brand { font-size: 23px; font-weight: 600; color: #12634b; }
QLabel#number { font-size: 28px; font-weight: 600; color: #12634b; }
QLabel#emptyState { background: #e8f0f4; color: #263f4c; padding: 12px;
                   border: 1px solid #a9bdc8; border-radius: 6px; }
QLabel#feedbackError { background: #fff0ed; color: #9c291e; padding: 8px;
                      border: 1px solid #b23a2c; border-radius: 4px; }
QFrame#card { background: white; border: 1px solid #a9bdc8; border-radius: 12px; }
QFrame#sidebar { background: #eaf0ed; border: 0; }
QPushButton { background: white; color: #172b35; border: 1px solid #728b97;
              border-radius: 7px; padding: 9px 14px; }
QPushButton:hover { background: #e2f0e9; border-color: #17694f; }
QPushButton:pressed { background: #c7dfd2; border-color: #104c3a; }
QPushButton:focus { border: 2px solid #155c9b; padding: 8px 13px; }
QPushButton:default { border: 2px solid #17694f; padding: 8px 13px; }
QPushButton:disabled { color: #52636d; background: #e4e9ec; border-color: #a7b5bd; }
QPushButton#primary { background: #17694f; color: white; border: 1px solid #17694f; }
QPushButton#primary:hover { background: #11533e; }
QPushButton#primary:pressed { background: #0b402f; }
QPushButton#primary:focus { border: 2px solid #f5c842; padding: 8px 13px; }
QPushButton#primary:disabled { background: #d5dde2; color: #52636d; border-color: #a7b5bd; }
QListWidget { border: 0; background: transparent; color: #172b35; }
QListWidget::item { padding: 12px; margin: 3px 0; border: 1px solid transparent;
                    border-radius: 7px; }
QListWidget::item:hover { background: #e2f0e9; }
QListWidget::item:selected, QListWidget::item:selected:!active {
    background: #17694f; color: white; border-color: #104c3a;
}
QListWidget::item:focus { border: 2px solid #155c9b; padding: 11px; }
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QAbstractSpinBox {
    background: white; color: #172b35; border: 1px solid #728b97;
    border-radius: 5px; padding: 7px; selection-background-color: #17694f;
    selection-color: white; placeholder-text-color: #52636d;
}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus,
QAbstractSpinBox:focus { border: 2px solid #155c9b; padding: 6px; }
QLineEdit:disabled, QComboBox:disabled, QPlainTextEdit:disabled, QTextEdit:disabled,
QAbstractSpinBox:disabled { background: #e4e9ec; color: #52636d; border-color: #a7b5bd; }
QComboBox::drop-down { background: #edf3f1; border-left: 1px solid #a9bdc8; width: 24px; }
QComboBox::down-arrow { image: url("@ICONS@/chevron-down.svg"); width: 16px; height: 12px; }
QComboBox::down-arrow:disabled { image: url("@ICONS@/chevron-disabled.svg"); }
QSpinBox::up-arrow { image: url("@ICONS@/chevron-up.svg"); width: 16px; height: 12px; }
QSpinBox::down-arrow { image: url("@ICONS@/chevron-down.svg"); width: 16px; height: 12px; }
QComboBox QAbstractItemView { background: white; color: #172b35;
    selection-background-color: #17694f; selection-color: white; border: 1px solid #728b97; }
QCheckBox, QRadioButton { background: transparent; color: #172b35; spacing: 8px; padding: 3px; }
QCheckBox:disabled, QRadioButton:disabled { color: #52636d; }
QCheckBox:focus, QRadioButton:focus { background: #e3edf7; border-radius: 3px;
    border: 2px solid #155c9b; padding: 1px; }
QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px;
    background: white; border: 2px solid #526b79; }
QCheckBox::indicator { border-radius: 3px; }
QRadioButton::indicator { border-radius: 11px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: #17694f; }
QCheckBox::indicator:checked { background: #17694f; border-color: #17694f;
    image: url("@ICONS@/check.svg"); }
QCheckBox::indicator:indeterminate { background: #17694f; border: 5px solid #526b79; }
QRadioButton::indicator:checked { image: url("@ICONS@/radio.svg"); border-color: #17694f; }
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {
    background: #e4e9ec; border-color: #9aaab4; }
QCheckBox::indicator:checked:disabled { image: url("@ICONS@/check-disabled.svg"); }
QRadioButton::indicator:checked:disabled { image: url("@ICONS@/radio-disabled.svg"); }
QTableView, QTableWidget, QTreeWidget, QTreeView {
    background: white; color: #172b35; alternate-background-color: #edf3f1;
    selection-background-color: #17694f; selection-color: white;
    border: 1px solid #a9bdc8; border-radius: 6px; gridline-color: #d5dfe5;
}
QTableView::item:selected, QTableView::item:selected:!active,
QTreeView::item:selected, QTreeView::item:selected:!active {
    background: #17694f; color: white;
}
QTableView::item:focus, QTreeView::item:focus { border: 2px solid #155c9b; }
QListWidget::item:selected:focus, QTableView::item:selected:focus,
QTreeView::item:selected:focus { border: 2px solid #f5c842; }
QTableCornerButton::section, QHeaderView::section {
    background: #e6eeeb; color: #172b35; border: 0; padding: 10px; font-weight: 600;
}
QScrollArea { background: #f5f7f9; border: 0; }
QScrollArea#operationScroll, QScrollArea#ocrScroll, QWidget#operationBody, QWidget#ocrBody {
    background: #f5f7f9;
}
QScrollBar:vertical { background: #e1e8ed; width: 14px; margin: 1px; }
QScrollBar:horizontal { background: #e1e8ed; height: 14px; margin: 1px; }
QScrollBar::handle:vertical { background: #607d8b; min-height: 28px; border-radius: 5px; }
QScrollBar::handle:horizontal { background: #607d8b; min-width: 28px; border-radius: 5px; }
QScrollBar::handle:hover { background: #305f75; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: #e1e8ed; }
QTabWidget::pane { border: 1px solid #a9bdc8; background: #f5f7f9; }
QTabBar::tab { background: #e6eeeb; color: #172b35; border: 1px solid #a9bdc8;
               padding: 8px 12px; }
QTabBar::tab:selected { background: #17694f; color: white; }
QTabBar::tab:disabled { background: #e4e9ec; color: #52636d; }
QMenuBar, QMenu { background: white; color: #172b35; }
QMenuBar::item:selected, QMenu::item:selected { background: #17694f; color: white; }
QMenu::item:disabled { color: #52636d; }
QToolTip { background: #172b35; color: white; border: 1px solid #526b79; padding: 6px; }
QStatusBar { background: #eaf0ed; color: #172b35; }
""".replace("@ICONS@", Path(__file__).with_name("icons").as_posix())


def label(text: str, name: str = "") -> QLabel:
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
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
    def __init__(self, *, ocr_recognitions: tuple[CardRecognition, ...] = ()) -> None:
        super().__init__()
        self.setWindowTitle("Личные финансы")
        self.resize(1160, 760)
        self.setMinimumSize(920, 620)
        self.setStyleSheet(STYLE)
        self._local_ocr_preferences = LocalOcrPreferences()
        self._ocr_preferences = self._local_ocr_preferences.load()
        self._ocr_recognitions = ocr_recognitions
        self._ocr_actions: list[QAction] = []
        self.session: VaultSession | None = None
        self._account_records: list[Account] = []
        self._journal_operations: list[Operation] = []
        self._refund_candidates: list[RefundCandidate] = []
        self._closed_months: set[AccountingMonth] = set()
        self._tag_records: list[Tag] = []
        self._saved_filters: list[SavedFilter] = []
        self._active_query = OperationQuery()
        self._jobs: set[Job] = set()
        self._job_callbacks: dict[JobSignals, Callable[[Any], object]] = {}
        self._job_deferred: set[JobSignals] = set()
        self._job_results: dict[JobSignals, object] = {}
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._busy = False
        self.last_backup = "Резервная копия ещё не создана в этом сеансе"
        self.root_stack = QStackedWidget()
        self.setCentralWidget(self.root_stack)
        self.root_stack.addWidget(self._welcome())
        self.root_stack.addWidget(self._workspace())
        self.statusBar().showMessage("Работает без интернета · Книга закрыта")
        if self._ocr_recognitions:
            menu = self.menuBar().addMenu("Распознавание")
            for service in self._ocr_recognitions:
                if service.bank_name == UNIVERSAL_PROFILE:
                    automatic = menu.addAction("Распознать изображение…")
                    automatic.triggered.connect(
                        partial(self.review_ocr, service=service, auto_detect=True)
                    )
                    self._ocr_actions.append(automatic)
                bank = menu.addMenu(service.bank_name)
                for title, history in (("Карточка операции", False), ("История операций", True)):
                    if history and not service.supports_history:
                        continue
                    action = bank.addAction(title)
                    action.triggered.connect(
                        partial(self.review_ocr, service=service, history=history)
                    )
                    self._ocr_actions.append(action)
        settings_menu = self.menuBar().addMenu("Настройки")
        settings_menu.addAction("Распознавание и свои переводы", self.configure_ocr)
        reports_menu = self.menuBar().addMenu("Отчёты")
        self.report_action = reports_menu.addAction("Доходы и расходы…", self.request_analytics)
        self.report_action.setEnabled(False)

    def _update_report_action(self) -> None:
        self.report_action.setEnabled(
            not self._busy and self.session is not None and self.session.ledger is not None
        )

    def request_analytics(self) -> None:
        session = self.session
        if self._busy or session is None or session.ledger is None:
            return
        dialog = ReportPeriodDialog(self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_period is None:
                return
            period = dialog.result_period
        finally:
            dialog.deleteLater()
        self.build_analytics(period)

    def build_analytics(self, period: ReportPeriod) -> None:
        session = self.session
        if self._busy or session is None or session.ledger is None:
            return
        analytics = Analytics(session.ledger, session.mutex, session.ensure_open)

        def build() -> tuple[IncomeExpenseReport, list[Category], list[Account]]:
            with session.mutex:
                return (
                    analytics.report(period),
                    session.catalog.categories(),
                    session.catalog.accounts(),
                )

        self._job(
            build,
            lambda data: self._show_analytics(session, data),
            "Расчёт доходов и расходов…",
            defer_success=True,
        )

    def _show_analytics(
        self,
        session: VaultSession,
        result: tuple[IncomeExpenseReport, list[Category], list[Account]],
    ) -> None:
        if self.session is not session:
            return
        dialog = IncomeExpenseDialog(result[0], result[1], self, accounts=result[2])
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _review_defaults(self) -> OcrReviewDefaults:
        value = self._ocr_preferences
        return OcrReviewDefaults(value.default_currency, value.year, value.confirm_fields)

    def configure_ocr(self) -> None:
        if self._busy:
            return
        session = self.session
        service = session.ocr_settings if session is not None else None
        revision = None
        if service is not None:
            _, revision = service.load()
        dialog = OcrSettingsDialog(
            self._ocr_preferences, self._account_records, self, allow_parties=service is not None
        )
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_preferences is None:
                return
            value = dialog.result_preferences
        finally:
            dialog.deleteLater()

        def applied(_: object = None) -> None:
            if self.session is session:
                self._ocr_preferences = value
                self._local_ocr_preferences.save(value)
                self.statusBar().showMessage("Настройки распознавания сохранены")

        if service is None:
            applied()
        else:
            self._job(lambda: service.save(value, revision), applied, "Сохранение настроек OCR…")

    def _ocr_batch_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_report_action()
        self.root_stack.setEnabled(not busy)
        for action in self._ocr_actions:
            action.setEnabled(not busy)

    def review_ocr(
        self,
        checked: bool = False,
        *,
        service: CardRecognition | None = None,
        history: bool = False,
        auto_detect: bool = False,
    ) -> None:
        if self._busy or not self._ocr_recognitions:
            return
        service = service or self._ocr_recognitions[0]
        if service not in self._ocr_recognitions:
            return
        title = (
            "Распознать изображение"
            if auto_detect
            else "История операций"
            if history
            else "Карточка одной операции"
        )
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"{title} · {service.bank_name}",
            "",
            "Изображения (*.png *.jpg *.jpeg)",
        )
        if not path:
            return
        self._job(
            lambda: (
                service.analyze(Path(path))
                if auto_detect
                else service.history(Path(path))
                if history
                else service.preview(Path(path))
            ),
            self._show_ocr_preview,
            f"Локальное распознавание · {service.bank_name}…",
            defer_success=True,
        )

    def _show_ocr_preview(self, preview: OcrCardPreview | OcrHistoryPreview) -> None:
        session = self.session
        universal = preview.bank_name == UNIVERSAL_PROFILE
        allow = session is not None and session.ocr_importing is not None and not universal
        if isinstance(preview, OcrHistoryPreview) and universal:
            batch = OcrBatchDialog(preview, None, [], [], self._ocr_preferences, self)
            try:
                batch.exec()
            finally:
                batch.deleteLater()
            return
        if (
            isinstance(preview, OcrHistoryPreview)
            and session is not None
            and session.ocr_importing is not None
            and session.ocr_importing.supports_batches
        ):
            batch = OcrBatchDialog(
                preview,
                session.ocr_importing,
                self._account_records,
                session.catalog.categories(),
                self._ocr_preferences,
                self,
                on_saved=lambda _: self.refresh() if self.session is session else None,
                on_busy=self._ocr_batch_busy,
                classification=session.classification.snapshot()
                if session.classification is not None
                else None,
            )
            try:
                batch.exec()
            finally:
                batch.deleteLater()
            return
        dialog = (
            OcrHistoryDialog(
                preview,
                self,
                allow_financial_prepare=allow,
                review_defaults=self._review_defaults(),
            )
            if isinstance(preview, OcrHistoryPreview)
            else OcrPreviewDialog(
                preview,
                self,
                allow_financial_prepare=allow,
                review_defaults=self._review_defaults(),
            )
        )
        requested: tuple[OcrCardPreview, ReviewedOcrCard] | None = None
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                if isinstance(dialog, OcrHistoryDialog):
                    requested = dialog.selected_for_financial
                elif dialog.financial_requested and dialog.reviewed is not None:
                    requested = (dialog.preview, dialog.reviewed)
                if requested is None:
                    self.statusBar().showMessage(
                        "Поля OCR проверены · Операция в книгу не записана"
                    )
        finally:
            dialog.deleteLater()
        if requested is not None and session is not None and self.session is session:
            self._prepare_ocr_operation(session, *requested)

    def _prepare_ocr_operation(
        self, session: VaultSession, preview: OcrCardPreview, reviewed: ReviewedOcrCard
    ) -> None:
        if self.session is not session or session.ocr_importing is None or session.ledger is None:
            return
        if not any(account.currency == "RUB" for account in self._account_records):
            QMessageBox.information(
                self, "Сначала добавьте счёт", "Создайте счёт RUB для операции OCR."
            )
            return
        dialog = OcrOperationDialog(
            preview,
            reviewed,
            self._account_records,
            session.catalog.categories(),
            session.catalog.institutions(),
            self,
            allow_splits=session.ledger.supports_splits,
            allow_refunds=session.ledger.supports_refunds,
            refund_candidates=self._refund_candidates,
            tags=self._tag_records,
            allow_tags=session.ledger.supports_tags,
        )
        if not self._ocr_preferences.review_before_save:
            buttons = dialog.findChild(QDialogButtonBox)
            if buttons is not None:
                buttons.button(QDialogButtonBox.StandardButton.Save).setText("Добавить операцию")
        parties = self._ocr_preferences.matching_parties(reviewed.description)
        if len(parties) == 1 and parties[0].exclude:
            QMessageBox.information(
                self,
                "Исключённый отправитель",
                "По настройке эта строка не импортируется и не меняет остатки.",
            )
            dialog.deleteLater()
            return
        if len(parties) == 1:
            dialog.kind.setCurrentIndex(dialog.kind.findData(OperationKind.TRANSFER.value))
            other = parties[0].account_id
            if other is not None:
                target = dialog.account if reviewed.direction.value == "in" else dialog.destination
                target.setCurrentIndex(target.findData(other))
        selection = None
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                selection = dialog.result_selection
        finally:
            dialog.deleteLater()
        if selection is None or self.session is not session:
            return
        service = session.ocr_importing
        self._job(
            lambda: service.preview(selection),
            lambda prepared: self._show_ocr_confirmation(session, prepared),
            "Проверка повторов и финансового смысла OCR…",
            defer_success=True,
        )

    def _show_ocr_confirmation(self, session: VaultSession, prepared: OcrImportPreview) -> None:
        if self.session is not session or session.ocr_importing is None:
            return
        if not self._ocr_preferences.review_before_save and prepared.status in (
            OcrImportStatus.DUPLICATE,
            OcrImportStatus.CONFLICT,
        ):
            self.statusBar().showMessage(prepared.message + " · Новых движений нет")
            return
        if not self._ocr_preferences.review_before_save and prepared.status == OcrImportStatus.NEW:
            service = session.ocr_importing
            command_id = str(uuid4())
            self._job(
                lambda: service.confirm(prepared.selection, command_id=command_id),
                lambda result: self._ocr_saved(session, result),
                "Добавление операции OCR…",
                defer_success=True,
            )
            return
        dialog = OcrConfirmationDialog(
            prepared,
            self._account_records,
            session.catalog.categories(),
            self._tag_records,
            self,
            require_agreement=self._ocr_preferences.review_before_save,
        )
        selection = None
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                selection = dialog.result_selection
        finally:
            dialog.deleteLater()
        if selection is None or self.session is not session:
            return
        service, command_id = session.ocr_importing, str(uuid4())
        self._job(
            lambda: service.confirm(selection, command_id=command_id),
            lambda result: self._ocr_saved(session, result),
            "Подтверждение операции и происхождения OCR…",
            defer_success=True,
        )

    def _ocr_saved(self, session: VaultSession, result: OcrImportResult) -> None:
        if self.session is not session:
            return
        self.refresh()
        self.statusBar().showMessage(
            "Источник OCR связан с операцией · Новые движения не создавались"
            if result.linked
            else "Операция OCR и её происхождение сохранены"
        )

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
            [
                "Операции",
                "Счета",
                "Теги и фильтры",
                "Категории",
                "Периоды",
                "Правила категорий",
                "Защита и копии",
            ]
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
        journal_page = self._journal()
        accounts_page = self._accounts()
        categories_page = self._categories()
        security_page = self._security()
        self.period_page = PeriodPage()
        self.period_page.closeRequested.connect(self.close_period)
        self.period_page.reopenRequested.connect(self.reopen_period)
        self.period_page.historyRequested.connect(self.period_history)
        self.organization_page = OrganizationPage()
        self.organization_page.changedArchive.connect(self.refresh)
        self.organization_page.addTagRequested.connect(lambda: self.definition_action("tag", "add"))
        self.organization_page.renameTagRequested.connect(
            lambda: self.definition_action("tag", "rename")
        )
        self.organization_page.archiveTagRequested.connect(
            lambda: self.definition_action("tag", "archive")
        )
        self.organization_page.restoreTagRequested.connect(
            lambda: self.definition_action("tag", "restore")
        )
        self.organization_page.tagHistoryRequested.connect(
            lambda: self.definition_action("tag", "history")
        )
        self.organization_page.renameFilterRequested.connect(
            lambda: self.definition_action("saved_filter", "rename")
        )
        self.organization_page.archiveFilterRequested.connect(
            lambda: self.definition_action("saved_filter", "archive")
        )
        self.organization_page.restoreFilterRequested.connect(
            lambda: self.definition_action("saved_filter", "restore")
        )
        self.organization_page.filterHistoryRequested.connect(
            lambda: self.definition_action("saved_filter", "history")
        )
        self.rule_page = CategoryRulePage()
        self.rule_page.changedFilter.connect(self.refresh)
        self.rule_page.addRequested.connect(lambda: self.rule_action("add"))
        self.rule_page.editRequested.connect(lambda: self.rule_action("edit"))
        self.rule_page.toggleRequested.connect(lambda: self.rule_action("toggle"))
        self.rule_page.historyRequested.connect(lambda: self.rule_action("history"))
        for section in (
            journal_page,
            accounts_page,
            self.organization_page,
            categories_page,
            self.period_page,
            self.rule_page,
            security_page,
        ):
            self.pages.addWidget(section)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        content.addWidget(self.pages, 1)
        layout.addLayout(content, 1)
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
        self.account_empty = label(
            "Счетов пока нет. Нажмите «Добавить счёт», чтобы начать.", "emptyState"
        )
        layout.addWidget(self.account_empty)
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
        layout.addLayout(actions)
        refund_row = QHBoxLayout()
        self.refund_button = button("Оформить возврат", self.add_refund)
        refund_row.addWidget(self.refund_button)
        self.import_button = button("Импорт CSV/OFX…", self.import_statement)
        refund_row.addWidget(self.import_button)
        self.export_button = button("Экспорт…", self.export_journal)
        refund_row.addWidget(self.export_button)
        refund_row.addStretch()
        layout.addLayout(refund_row)
        filter_row = QHBoxLayout()
        self.filter_button = button("Фильтр…", self.edit_query)
        self.reset_filter_button = button("Сбросить фильтр", self.reset_query)
        filter_row.addWidget(self.filter_button)
        filter_row.addWidget(self.reset_filter_button)
        filter_row.addWidget(self.show_deleted)
        filter_row.addStretch()
        layout.addLayout(filter_row)
        saved_row = QHBoxLayout()
        self.saved_filter_combo = QComboBox()
        self.apply_filter_button = button("Применить", self.apply_saved_filter)
        self.save_filter_button = button("Сохранить как…", self.save_query)
        self.update_filter_button = button("Обновить", self.update_saved_filter)
        saved_row.addWidget(self.saved_filter_combo, 1)
        for widget in (
            self.apply_filter_button,
            self.save_filter_button,
            self.update_filter_button,
        ):
            saved_row.addWidget(widget)
        layout.addLayout(saved_row)
        self.saved_filter_combo.currentIndexChanged.connect(self._update_saved_filter_actions)
        self.query_status = label("Все действующие операции", "muted")
        layout.addWidget(self.query_status)
        self.operation_table = QTableView()
        self.operation_model = replace_table(self.operation_table, JOURNAL_HEADERS, [])
        self.operation_table.verticalHeader().hide()
        self.operation_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.operation_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.operation_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.operation_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.operation_table.horizontalHeader().setMaximumSectionSize(400)
        for column, width in ((2, 160), (3, 180), (6, 200)):
            self.operation_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.Interactive
            )
            self.operation_table.setColumnWidth(column, width)
        self.operation_table.selectionModel().selectionChanged.connect(self._update_journal_actions)
        self.operation_table.doubleClicked.connect(self.edit_operation)
        self.journal_empty = label("Операций пока нет.", "emptyState")
        layout.addWidget(self.journal_empty)
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
        self.export_button.setEnabled(
            self.session is not None and self.session.exporting is not None
        )
        can_import = self.session is not None and self.session.importing is not None
        self.import_button.setEnabled(
            can_import and any(a.currency == "RUB" for a in self._account_records)
        )
        self.import_button.setToolTip(
            "Локальный файл, предпросмотр и подтверждение выбранных строк."
            if can_import
            else "Для импорта восстановите копию старой книги в новую папку."
        )
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

    @Slot()
    def export_journal(self) -> None:
        session = self.session
        if session is None or session.exporting is None or session.ledger is None:
            return
        dialog = ExportOptionsDialog(
            self._active_query,
            self._account_records,
            session.catalog.categories(),
            session.catalog.institutions(),
            self._tag_records,
            allow_tags=session.ledger.supports_tags,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.options is None:
            return
        options, service = dialog.options, session.exporting
        self._job(
            lambda: service.preview(options.settings),
            lambda preview: self._show_export_preview(session, options, preview),
            "Подготовка снимка экспорта…",
            defer_success=True,
        )

    def _show_export_preview(
        self, session: VaultSession, options: ExportOptions, preview: ExportPreview
    ) -> None:
        if self.session is not session or session.exporting is None:
            return
        dialog = ExportPreviewDialog(preview, options.format, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        base = Path(user_data_dir("PersonalFinance", appauthor=False))
        folder = base / "exports"
        if folder.resolve().is_relative_to(session.root.resolve()):
            folder = session.root.parent
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            folder = base.parent
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Новый файл экспорта",
            str(folder / f"crown-journal-{stamp}-{uuid4().hex[:4]}.{options.format.value}"),
            f"{options.format.value.upper()} (*.{options.format.value})",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not filename or self.session is not session:
            return
        destination = Path(filename)
        if not destination.suffix:
            destination = destination.with_suffix("." + options.format.value)
        service = session.exporting
        self._job(
            lambda: service.save(destination, preview, options.format),
            lambda path: QMessageBox.information(
                self, "Экспорт сохранён", f"Файл сохранён:\n{path}"
            ),
            "Сохранение нового файла экспорта…",
            defer_success=True,
        )

    def rule_action(self, action: str) -> None:
        session = self.session
        if session is None or session.classification is None:
            return
        service, selected = session.classification, self.rule_page.selected()
        if action != "add" and selected is None:
            return
        categories = session.catalog.categories()
        if action == "history" and selected is not None:
            CategoryRuleHistoryDialog(
                service.history(selected.rule.id), self._account_records, categories, self
            ).exec()
            return
        if action == "toggle" and selected is not None:
            rule = replace(selected.rule, enabled=not selected.rule.enabled)
        else:
            dialog = CategoryRuleDialog(
                self._account_records,
                categories,
                self,
                existing=selected.rule if action == "edit" and selected is not None else None,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.rule is None:
                return
            rule = dialog.rule
        expected = selected.revision_id if action != "add" and selected is not None else None
        command = str(uuid4())
        self._job(
            lambda: service.save(rule, expected_revision_id=expected, command_id=command),
            lambda _: self.refresh(),
            "Сохранение версии правила…",
        )

    @Slot()
    def import_statement(self) -> None:
        session = self.session
        if session is None or session.importing is None:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "Выбрать локальную выписку", "", "Выписки (*.csv *.ofx)"
        )
        if not filename:
            return
        path = Path(filename)
        self._job(
            lambda: read_statement(path),
            lambda contents: self._configure_import(
                session, path.suffix.lower() == ".csv", contents
            ),
            "Чтение локального файла…",
            defer_success=True,
        )

    def _configure_import(self, session: VaultSession, is_csv: bool, contents: bytes) -> None:
        if self.session is not session or session.importing is None:
            return
        dialog = ImportOptionsDialog(
            contents,
            is_csv=is_csv,
            accounts=self._account_records,
            categories=session.catalog.categories(),
            allow_rules=session.classification is not None,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.options is None:
            return
        options, service = dialog.options, session.importing

        def prepare() -> tuple[ImportPreview, PreparedImportCategories | None]:
            document = (
                service.parse_csv(contents, options.mapping)
                if options.mapping is not None
                else service.parse_ofx(contents)
            )
            preview = service.preview(document, options.account_id)
            classification = (
                session.classification.snapshot()
                if options.use_rules and session.classification is not None
                else None
            )
            prepared = (
                prepare_import_categories(
                    classification, preview, options.expense_category_id, options.income_category_id
                )
                if classification is not None
                else None
            )
            return preview, prepared

        self._job(
            prepare,
            lambda prepared: self._preview_import(
                session, options, prepared[0], classification=prepared[1]
            ),
            "Подготовка предпросмотра без записи…",
            defer_success=True,
        )

    def _preview_import(
        self,
        session: VaultSession,
        options: ImportOptions,
        preview: ImportPreview,
        *,
        classification: PreparedImportCategories | None = None,
    ) -> None:
        if self.session is not session or session.importing is None or session.ledger is None:
            return
        dialog = ImportPreviewDialog(
            preview,
            options,
            self._account_records,
            session.catalog.categories(),
            session.catalog.institutions(),
            self._tag_records,
            session.ledger.refund_candidates(),
            self,
            classification=classification,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selections:
            return
        service, selected, command = session.importing, dialog.selections, str(uuid4())
        self._job(
            lambda: service.confirm(
                preview.document, options.account_id, selected, command_id=command
            ),
            self._import_complete,
            "Сохранение всей группы импорта…",
            defer_success=True,
        )

    def _import_complete(self, result: ImportResult) -> None:
        self.refresh()
        self.navigation.setCurrentRow(0)
        QMessageBox.information(
            self,
            "Импорт подтверждён",
            f"Создано операций: {len(result.operations) - result.linked_count}. "
            f"Связано с существующими: {result.linked_count}. "
            "Связи не создают повторного движения. Текущий фильтр может скрывать строки.",
        )

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
            tags=self._tag_records,
            allow_tags=session.ledger.supports_tags,
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
            tags=self._tag_records,
            allow_tags=session.ledger.supports_tags,
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
            tags=self._tag_records,
        ).exec()

    @Slot()
    def edit_query(self) -> None:
        session = self.session
        if session is None or session.ledger is None:
            return
        dialog = QueryDialog(
            self._active_query,
            self._account_records,
            session.catalog.categories(),
            session.catalog.institutions(),
            self._tag_records,
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_query is not None:
            self._set_query(dialog.result_query)

    def _set_query(self, query: OperationQuery) -> None:
        self._active_query = query
        self.show_deleted.blockSignals(True)
        self.show_deleted.setChecked(query.include_deleted)
        self.show_deleted.blockSignals(False)
        self.refresh()

    @Slot()
    def reset_query(self) -> None:
        self._set_query(OperationQuery())

    def _selected_saved_filter(self) -> SavedFilter | None:
        identifier = self.saved_filter_combo.currentData()
        return next((value for value in self._saved_filters if value.id == identifier), None)

    @Slot()
    def _update_saved_filter_actions(self) -> None:
        available = self.session is not None and self.session.organization is not None
        selected = self._selected_saved_filter() is not None
        self.apply_filter_button.setEnabled(available and selected)
        self.update_filter_button.setEnabled(available and selected)
        self.save_filter_button.setEnabled(available)

    @Slot()
    def apply_saved_filter(self) -> None:
        selected = self._selected_saved_filter()
        if selected is not None:
            self._set_query(selected.query)

    @Slot()
    def save_query(self) -> None:
        session = self.session
        if session is None or session.organization is None:
            return
        dialog = NameDialog(self)
        dialog.setWindowTitle("Сохранить фильтр")
        dialog.setLabelText("Название фильтра")
        dialog.setOkButtonText("Сохранить")
        dialog.setCancelButtonText("Отмена")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        organization, query, name, command = (
            session.organization,
            self._active_query,
            dialog.textValue(),
            str(uuid4()),
        )
        self._job(
            lambda: organization.save_filter(name, query, command_id=command),
            lambda _: self.refresh(),
            "Сохранение фильтра…",
        )

    @Slot()
    def update_saved_filter(self) -> None:
        session = self.session
        selected = self._selected_saved_filter()
        if session is None or session.organization is None or selected is None:
            return
        organization, query, command = session.organization, self._active_query, str(uuid4())
        self._job(
            lambda: organization.save_filter(
                selected.name,
                query,
                identifier=selected.id,
                expected_revision_id=selected.revision_id,
                command_id=command,
            ),
            lambda _: self.refresh(),
            "Сохранение новой версии фильтра…",
        )

    def definition_action(self, kind: str, action: str) -> None:
        session = self.session
        if session is None or session.organization is None:
            return
        selected = (
            self.organization_page.selected_tag()
            if kind == "tag"
            else self.organization_page.selected_filter()
        )
        if action != "add" and selected is None:
            return
        organization = session.organization
        if action == "history":
            assert selected is not None
            DefinitionHistoryDialog(
                organization.history(kind, selected.id),
                self,
                accounts=self._account_records,
                categories=session.catalog.categories(),
                institutions=session.catalog.institutions(),
                tags=self._tag_records,
            ).exec()
            return
        name = ""
        if action in ("add", "rename"):
            dialog = NameDialog(self)
            dialog.setWindowTitle("Создать тег" if action == "add" else "Переименовать")
            dialog.setLabelText("Название")
            dialog.setOkButtonText("Сохранить")
            dialog.setCancelButtonText("Отмена")
            if selected is not None:
                dialog.setTextValue(selected.name)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            name = dialog.textValue()
        if (
            action == "archive"
            and QMessageBox.question(
                self,
                "Переместить в архив",
                "Убрать запись из активного списка? История и прежние назначения сохранятся.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        command = str(uuid4())

        def save() -> object:
            if kind == "tag":
                if action == "add":
                    return organization.add_tag(name, command_id=command)
                assert selected is not None
                if action == "rename":
                    return organization.rename_tag(
                        selected.id, selected.revision_id, name, command_id=command
                    )
                return organization.archive_tag(
                    selected.id,
                    selected.revision_id,
                    archived=action == "archive",
                    command_id=command,
                )
            assert selected is not None
            if action == "rename":
                return organization.rename_filter(
                    selected.id, selected.revision_id, name, command_id=command
                )
            return organization.archive_filter(
                selected.id, selected.revision_id, archived=action == "archive", command_id=command
            )

        self._job(save, lambda _: self.refresh(), "Сохранение тега или фильтра…")

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
        self,
        action: Callable[[], object],
        success: Callable[[Any], object],
        message: str,
        *,
        defer_success: bool = False,
    ) -> None:
        if self._busy:
            return
        self._busy = True
        self._update_report_action()
        self.root_stack.setEnabled(False)
        for ocr_action in self._ocr_actions:
            ocr_action.setEnabled(False)
        self.statusBar().showMessage(message)
        job = Job(action, self)
        self._jobs.add(job)
        self._job_callbacks[job.signals] = success
        if defer_success:
            self._job_deferred.add(job.signals)
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
                if signals in self._job_deferred:
                    self._job_results[signals] = result
                else:
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
        callback = self._job_callbacks.pop(signals, None)
        has_result = signals in self._job_results
        result = self._job_results.pop(signals, None)
        self._job_deferred.discard(signals)
        for job in tuple(self._jobs):
            if job.signals is signals:
                self._jobs.discard(job)
        signals.success.disconnect(self._job_succeeded)
        signals.failure.disconnect(self._job_failed)
        signals.finished.disconnect(self._job_finished)
        signals.deleteLater()
        self._busy = False
        self._update_report_action()
        self.root_stack.setEnabled(True)
        for ocr_action in self._ocr_actions:
            ocr_action.setEnabled(True)
        state = "Книга открыта" if self.session else "Книга закрыта"
        self.statusBar().showMessage(f"Работает без интернета · {state}")
        if has_result and callback is not None:
            callback(result)

    def activate_session(self, session: VaultSession) -> None:
        if self.session and self.session is not session:
            self.session.close()
        self.session = session
        self._update_report_action()
        stored = session.ocr_settings.load()[0] if session.ocr_settings is not None else None
        self._ocr_preferences = stored or self._local_ocr_preferences.load()
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
        self._update_report_action()
        self._ocr_preferences = self._local_ocr_preferences.load()
        self.account_model = replace_table(self.account_table, ACCOUNT_HEADERS, [])
        self.operation_model = replace_table(self.operation_table, JOURNAL_HEADERS, [])
        self._journal_operations = []
        self._account_records = []
        self.reconcile_button.setEnabled(False)
        self._refund_candidates = []
        self._closed_months = set()
        self._active_query = OperationQuery()
        self._tag_records = []
        self._saved_filters = []
        self.show_deleted.blockSignals(True)
        self.show_deleted.setChecked(False)
        self.show_deleted.blockSignals(False)
        self.saved_filter_combo.clear()
        self.saved_filter_combo.addItem("Сохранённые фильтры", None)
        self.query_status.setText("Все действующие операции")
        for widget in (
            self.filter_button,
            self.reset_filter_button,
            self.apply_filter_button,
            self.save_filter_button,
            self.update_filter_button,
        ):
            widget.setEnabled(False)
        self.organization_page.set_records([], [], False)
        self.rule_page.set_records([], False, [], [])
        self.period_page.set_states([], False)
        self._update_journal_actions()
        self.category_tree.clear()
        self.book_title.clear()
        self.last_backup = "Резервная копия ещё не создана в этом сеансе"
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
        self.account_empty.setVisible(not accounts)
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
        self._tag_records = (
            session.organization.tags(include_archived=True) if session.organization else []
        )
        all_filters = (
            session.organization.filters(include_archived=True) if session.organization else []
        )
        self._saved_filters = [value for value in all_filters if not value.is_deleted]
        archived = self.organization_page.archived.isChecked()
        self.organization_page.set_records(
            [value for value in self._tag_records if archived or not value.is_deleted],
            [value for value in all_filters if archived or not value.is_deleted],
            session.organization is not None,
        )
        all_rules = (
            session.classification.rules(include_disabled=True) if session.classification else []
        )
        self.rule_page.set_records(
            [
                value
                for value in all_rules
                if self.rule_page.disabled.isChecked() or value.rule.enabled
            ],
            session.classification is not None,
            accounts,
            categories,
        )
        current_filter = self.saved_filter_combo.currentData()
        self.saved_filter_combo.blockSignals(True)
        self.saved_filter_combo.clear()
        self.saved_filter_combo.addItem("Сохранённые фильтры", None)
        for value in self._saved_filters:
            self.saved_filter_combo.addItem(value.name, value.id)
        self.saved_filter_combo.setCurrentIndex(
            max(0, self.saved_filter_combo.findData(current_filter))
        )
        self.saved_filter_combo.blockSignals(False)
        for widget in (self.filter_button, self.reset_filter_button):
            widget.setEnabled(ledger is not None)
        self.saved_filter_combo.setEnabled(session.organization is not None)
        for widget in (
            self.save_filter_button,
            self.update_filter_button,
            self.apply_filter_button,
        ):
            widget.setEnabled(session.organization is not None)
        self._update_saved_filter_actions()
        self._active_query = replace(
            self._active_query, include_deleted=self.show_deleted.isChecked()
        )
        query_description = describe_query(
            self._active_query,
            accounts,
            categories,
            session.catalog.institutions(),
            self._tag_records,
        )
        query_summary = query_description.replace("\n", " · ")
        self.query_status.setText(
            query_summary if len(query_summary) <= 220 else query_summary[:217] + "…"
        )
        self.query_status.setToolTip(
            "<p>" + escape(query_description).replace("\n", "<br>") + "</p>"
        )
        self._journal_operations = ledger.search(self._active_query) if ledger is not None else []
        self._refund_candidates = (
            ledger.refund_candidates() if ledger is not None and ledger.supports_refunds else []
        )
        account_names = {account.id: account.name for account in accounts}
        category_names = {
            category.id: category_title(category, categories) for category in categories
        }
        tag_names = {
            tag.id: tag.name + (" (архив)" if tag.is_deleted else "") for tag in self._tag_records
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
                    "; ".join(
                        tag_names.get(identifier, "Тег недоступен") for identifier in draft.tag_ids
                    ),
                ]
            )
        self.operation_model = replace_table(
            self.operation_table,
            JOURNAL_HEADERS,
            journal_rows,
            selection_changed=self._update_journal_actions,
        )
        self.journal_empty.setVisible(not journal_rows)
        self.journal_empty.setText(
            "Эта книга открыта со справочниками. Для операций создайте копию и восстановите "
            "её в новую папку через «Защита и копии». Исходная книга сохранится."
            if ledger is None
            else "Сначала добавьте счёт в разделе «Счета». Затем можно внести первую операцию."
            if not accounts
            else "По выбранному фильтру операций нет. Измените или сбросьте фильтр."
            if self._active_query != OperationQuery()
            else "Операций пока нет. Добавьте операцию вручную или выберите импорт CSV/OFX."
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
        self._update_journal_actions()
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
