"""Явный выбор финансового смысла OCR и последнее подтверждение записи."""

from dataclasses import replace

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QMessageBox,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from personal_finance.application.ocr_cards import OcrCardPreview
from personal_finance.application.ocr_importing import OcrImporting
from personal_finance.domain.errors import FinanceError, ValidationError
from personal_finance.domain.ledger import OPERATION_LABELS, OperationDraft, OperationKind
from personal_finance.domain.models import Account, Category, Institution
from personal_finance.domain.ocr_cards import CashDirection, ReviewedOcrCard
from personal_finance.domain.ocr_imports import OcrImportPreview, OcrImportStatus, OcrSelection
from personal_finance.domain.organization import Tag
from personal_finance.domain.refunds import RefundCandidate
from personal_finance.presentation.ledger_dialogs import OperationDialog, category_title
from personal_finance.presentation.money import format_money
from personal_finance.presentation.ocr_preview import plain


class OcrOperationDialog(OperationDialog):
    """Общая форма только готовит выбор; запись возможна на следующем экране."""

    def __init__(
        self,
        preview: OcrCardPreview,
        reviewed: ReviewedOcrCard,
        accounts: list[Account],
        categories: list[Category],
        institutions: list[Institution],
        parent: QWidget | None = None,
        *,
        allow_splits: bool = True,
        allow_refunds: bool = False,
        refund_candidates: list[RefundCandidate] | None = None,
        tags: list[Tag] | None = None,
        allow_tags: bool = False,
    ) -> None:
        self.ocr_preview = preview
        self.ocr_reviewed = reviewed
        self.result_selection: OcrSelection | None = None
        rub_accounts = [account for account in accounts if account.currency == "RUB"]
        super().__init__(
            rub_accounts,
            categories,
            institutions,
            parent,
            preview_mode=True,
            require_kind_choice=True,
            preview_title="Подготовить операцию OCR",
            preview_message=(
                "Выберите вид операции и счёт на изображении. Для перевода между своими "
                "счетами укажите оба счёта. Проверьте категорию и остальные поля. "
                "Запись потребует отдельного подтверждения на следующем экране. "
                "Дата и движение выбранного счёта должны совпадать с проверенными полями OCR."
            ),
            allow_splits=allow_splits,
            allow_refunds=allow_refunds,
            refund_candidates=refund_candidates,
            tags=tags,
            allow_tags=allow_tags,
        )
        buttons = self.findChild(QDialogButtonBox)
        if buttons is not None:
            buttons.button(QDialogButtonBox.StandardButton.Save).setText(
                "Продолжить к подтверждению"
            )
        self.source_account = QComboBox()
        self.source_account.addItem("Выберите счёт на изображении", None)
        for account in rub_accounts:
            self.source_account.addItem(account.name, account.id)
        self.form.insertRow(1, "Счёт на изображении", self.source_account)
        self.source_account.currentIndexChanged.connect(self._sync_source_account)
        self.kind.currentIndexChanged.connect(self._restore_checked_fields)
        self.purchase.currentIndexChanged.connect(self._restore_checked_fields)
        self._restore_checked_fields()
        self.note.setPlainText(reviewed.description)

    def _restore_checked_fields(self) -> None:
        value = self.ocr_reviewed
        self.amount.setText(format_money(value.amount, include_currency=False))
        self.occurred_on.setDate(
            QDate(value.occurred_on.year, value.occurred_on.month, value.occurred_on.day)
        )
        if self.kind.currentData() == OperationKind.TRANSFER.value:
            self.account_label.setText("Счёт списания")
        self._sync_source_account()

    def _sync_source_account(self) -> None:
        identifier = self.source_account.currentData()
        if identifier is None or self.kind.currentData() is None:
            return
        target = (
            self.destination
            if (
                self.kind.currentData() == OperationKind.TRANSFER.value
                and self.ocr_reviewed.direction == CashDirection.IN
            )
            else self.account
        )
        target.setCurrentIndex(target.findData(identifier))

    def accept(self) -> None:
        self.result_selection = None
        if self.result_draft is None:
            return
        try:
            identifier = self.source_account.currentData()
            if identifier is None:
                raise ValidationError("Явно выберите счёт на изображении.")
            self.result_selection = OcrImporting.prepare(
                self.ocr_preview, self.ocr_reviewed, identifier, self.result_draft
            )
        except FinanceError as error:
            self.result_draft = None
            QMessageBox.warning(self, "Проверьте операцию OCR", str(error))
            return
        super().accept()

    def reject(self) -> None:
        self.result_selection = None
        self.result_draft = None
        super().reject()


def operation_summary(
    draft: OperationDraft,
    accounts: list[Account],
    categories: list[Category],
    tags: list[Tag],
) -> str:
    account_names = {account.id: account.name for account in accounts}
    category_names = {category.id: category_title(category, categories) for category in categories}
    tag_names = {tag.id: tag.name for tag in tags}
    rows = [
        "Вид: " + OPERATION_LABELS[draft.kind],
        "Дата: " + draft.occurred_on.strftime("%d.%m.%Y"),
        "Счёт: " + account_names.get(draft.account_id, "Неизвестный счёт"),
        "Сумма операции: " + format_money(draft.amount),
    ]
    if draft.destination_account_id is not None:
        rows.append(
            "Счёт получения: " + account_names.get(draft.destination_account_id, "Неизвестный счёт")
        )
        rows.append("Комиссия: " + format_money(draft.fee))
        if draft.fee_category_id:
            rows.append(
                "Категория комиссии: "
                + category_names.get(draft.fee_category_id, "Неизвестная категория")
            )
    if draft.category_id is not None:
        rows.append("Категория: " + category_names.get(draft.category_id, "Неизвестная категория"))
    if draft.parts:
        rows.append("Части:")
        rows.extend(
            category_names.get(part.category_id, "Неизвестная категория")
            + ": "
            + format_money(part.amount)
            for part in draft.parts
        )
    if draft.refund_of is not None:
        rows.append("Возврат связан с выбранным исходным расходом.")
    if draft.tag_ids:
        rows.append(
            "Теги: "
            + ", ".join(
                tag_names.get(identifier, "Недоступный тег") for identifier in draft.tag_ids
            )
        )
    if draft.note:
        rows.append("Примечание: " + draft.note)
    return "\n".join(rows)


class OcrConfirmationDialog(QDialog):
    def __init__(
        self,
        preview: OcrImportPreview,
        accounts: list[Account],
        categories: list[Category],
        tags: list[Tag],
        parent: QWidget | None = None,
        *,
        require_agreement: bool = True,
    ) -> None:
        super().__init__(parent)
        self.require_agreement = require_agreement
        self.preview = preview
        self.result_selection: OcrSelection | None = None
        self._accounts, self._categories, self._tags = accounts, categories, tags
        self.setWindowTitle("Подтвердить операцию OCR")
        self.setMinimumSize(650, 480)
        self.resize(760, 650)
        layout = QVBoxLayout(self)
        self.notice = plain(preview.message)
        layout.addWidget(self.notice)
        checked = preview.selection.evidence.reviewed
        source_name = next(
            (a.name for a in accounts if a.id == preview.selection.account_id), "Неизвестный счёт"
        )
        direction = "поступление" if checked.direction == CashDirection.IN else "списание"
        layout.addWidget(
            plain(
                "Проверено по изображению: "
                + direction
                + ", "
                + format_money(checked.amount)
                + ", "
                + checked.occurred_on.strftime("%d.%m.%Y")
                + ". Счёт: "
                + source_name
            )
        )
        self.separate = QRadioButton("Создать отдельную операцию")
        self.link = QRadioButton("Связать источник с существующей операцией без нового движения")
        group = QButtonGroup(self)
        group.addButton(self.separate)
        group.addButton(self.link)
        self.candidate = QComboBox()
        self.candidate.addItem("Выберите существующую операцию", None)
        names = {a.id: a.name for a in accounts}
        for index, operation in enumerate(preview.candidates):
            draft = operation.draft
            title = (
                f"{index + 1}. "
                + OPERATION_LABELS[draft.kind]
                + " · "
                + names.get(draft.account_id, "Счёт")
                + " · "
                + format_money(draft.amount)
            )
            if draft.note:
                title += " · " + draft.note.replace("\n", " ")[:80]
            self.candidate.addItem(title, index)
        choosing = preview.status == OcrImportStatus.POSSIBLE
        for widget in (self.separate, self.link, self.candidate):
            widget.setVisible(choosing)
            layout.addWidget(widget)
        self.link.setEnabled(bool(preview.candidates))
        body = QWidget()
        body.setObjectName("operationBody")
        column = QVBoxLayout(body)
        self.summary = plain("")
        column.addWidget(self.summary)
        column.addStretch()
        scroll = QScrollArea()
        scroll.setObjectName("operationScroll")
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        self.verified = QCheckBox("Я проверил вид операции, счета, категории и сумму")
        layout.addWidget(self.verified)
        self.verified.setVisible(require_agreement)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.confirm = buttons.addButton("Добавить в книгу", QDialogButtonBox.ButtonRole.ActionRole)
        self.confirm.clicked.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.verified.toggled.connect(self._update)
        self.separate.toggled.connect(self._choice_changed)
        self.link.toggled.connect(self._choice_changed)
        self.candidate.currentIndexChanged.connect(self._choice_changed)
        self._choice_changed()

    def _choice(self) -> OcrSelection:
        value = self.preview.selection
        if self.preview.status not in (OcrImportStatus.NEW, OcrImportStatus.POSSIBLE):
            raise ValidationError("Повторная или противоречивая строка не подтверждается.")
        if self.preview.status == OcrImportStatus.NEW:
            return value
        if self.separate.isChecked():
            return replace(
                value,
                acknowledged_duplicate=True,
                existing_operation_id=None,
                expected_revision_id=None,
            )
        index = self.candidate.currentData()
        if (
            not self.link.isChecked()
            or type(index) is not int
            or not 0 <= index < len(self.preview.candidates)
        ):
            raise ValidationError("Выберите отдельную операцию или конкретную связь.")
        operation = self.preview.candidates[index]
        return replace(
            value,
            draft=operation.draft,
            acknowledged_duplicate=False,
            existing_operation_id=operation.id,
            expected_revision_id=operation.revision_id,
        )

    def _choice_changed(self) -> None:
        self.verified.setChecked(False)
        self.candidate.setEnabled(self.link.isChecked())
        try:
            value = self._choice()
        except ValidationError:
            value = self.preview.selection
        self.summary.setText(
            operation_summary(value.draft, self._accounts, self._categories, self._tags)
        )
        self.confirm.setText(
            "Связать с выбранной операцией" if value.existing_operation_id else "Добавить в книгу"
        )
        self._update()

    def _update(self) -> None:
        try:
            self._choice()
        except ValidationError:
            ready = False
        else:
            ready = not self.require_agreement or self.verified.isChecked()
        self.confirm.setEnabled(ready)

    def _validate(self) -> None:
        self.result_selection = None
        try:
            if self.require_agreement and not self.verified.isChecked():
                raise ValidationError("Подтвердите проверку финансовой операции.")
            self.result_selection = self._choice()
        except FinanceError as error:
            QMessageBox.warning(self, "Проверьте подтверждение OCR", str(error))
            return
        self.accept()

    def reject(self) -> None:
        self.result_selection = None
        super().reject()
