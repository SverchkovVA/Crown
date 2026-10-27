"""Русские формы ручных финансовых операций и просмотра истории."""

from datetime import date
from html import escape

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.errors import FinanceError, ValidationError
from personal_finance.domain.ledger import (
    OPERATION_LABELS,
    Operation,
    OperationDraft,
    OperationKind,
)
from personal_finance.domain.models import (
    ACCOUNT_LABELS,
    Account,
    Category,
    CategoryKind,
    Institution,
    Money,
)
from personal_finance.domain.organization import Tag
from personal_finance.domain.refunds import RefundCandidate, validate_refund_reference
from personal_finance.presentation.money import format_money
from personal_finance.presentation.parts_editor import PartsEditor
from personal_finance.presentation.tag_selector import TagSelector


def category_title(category: Category, categories: list[Category]) -> str:
    by_id = {value.id: value for value in categories}
    names = [category.name]
    visited = {category.id}
    parent_id = category.parent_id
    while parent_id is not None and parent_id in by_id and parent_id not in visited:
        visited.add(parent_id)
        parent = by_id[parent_id]
        names.append(parent.name)
        parent_id = parent.parent_id
    return " / ".join(reversed(names))


class OperationDialog(QDialog):
    def __init__(
        self,
        accounts: list[Account],
        categories: list[Category],
        institutions: list[Institution],
        parent: QWidget | None = None,
        *,
        existing: Operation | None = None,
        initial_draft: OperationDraft | None = None,
        preview_mode: bool = False,
        kind: OperationKind = OperationKind.EXPENSE,
        account_id: str | None = None,
        allow_splits: bool = True,
        allow_refunds: bool = False,
        refund_candidates: list[RefundCandidate] | None = None,
        refund_of_id: str | None = None,
        tags: list[Tag] | None = None,
        allow_tags: bool = False,
    ) -> None:
        super().__init__(parent)
        if existing is not None and initial_draft is not None:
            raise ValidationError("Укажите один начальный снимок операции.")
        initial = existing.draft if existing is not None else initial_draft
        self.setWindowTitle(
            "Настроить строку импорта"
            if preview_mode
            else "Исправить операцию"
            if existing
            else "Добавить операцию"
        )
        self.setMinimumWidth(650)
        self.result_draft: OperationDraft | None = None
        self._categories = categories
        self._allow_splits = allow_splits
        self._allow_refunds = allow_refunds
        self._allow_tags = allow_tags
        self._existing_tag_ids = initial.tag_ids if initial else ()
        self._refund_candidates = {
            candidate.expense.id: candidate
            for candidate in refund_candidates or []
            if candidate.amount_remaining.minor > 0
            and (existing is None or candidate.expense.id != existing.id)
        }
        layout = QVBoxLayout(self)
        introduction = QLabel(
            "Это предпросмотр. Изменения попадут в книгу только после подтверждения всего импорта."
            if preview_mode
            else "Исправление создаст новую версию. Предыдущая версия останется в истории."
            if existing
            else "Операция изменяет остатки на указанную дату. "
            "Перевод между своими счетами не является доходом."
        )
        introduction.setWordWrap(True)
        layout.addWidget(introduction)
        self.form = QFormLayout()
        self.kind = QComboBox()
        for value, title in OPERATION_LABELS.items():
            if preview_mode and value == OperationKind.OPENING:
                continue
            if value != OperationKind.REFUND or allow_refunds:
                self.kind.addItem(title, value.value)
        self.occurred_on = QDateEdit(QDate.currentDate())
        self.occurred_on.setCalendarPopup(True)
        self.occurred_on.setDisplayFormat("dd.MM.yyyy")
        self.occurred_on.setMinimumDate(QDate(1, 1, 1))
        self.account = QComboBox()
        self.destination = QComboBox()
        banks = {bank.id: bank.name for bank in institutions}
        for combo in (self.account, self.destination):
            combo.addItem("Выберите счёт", None)
            for account in accounts:
                detail = banks.get(account.institution_id or "", ACCOUNT_LABELS[account.kind])
                combo.addItem(f"{account.name} · {detail} · {account.currency}", account.id)
        self.purchase = QComboBox()
        self.purchase.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.purchase.setMinimumContentsLength(24)
        self.purchase.addItem("Выберите исходный расход", None)
        account_names = {account.id: account.name for account in accounts}
        for candidate in self._refund_candidates.values():
            source = candidate.expense
            summary = (
                source.draft.occurred_on.strftime("%d.%m.%Y")
                + " · "
                + account_names.get(source.draft.account_id, "—")
                + " · "
                + format_money(source.draft.amount)
            )
            self.purchase.addItem(summary, source.id)
            self.purchase.setItemData(
                self.purchase.count() - 1,
                "<p>"
                + escape(summary)
                + "<br>"
                + escape(source.draft.note).replace("\n", "<br>")
                + "</p>",
                Qt.ItemDataRole.ToolTipRole,
            )
        self.refund_hint = QLabel("Выберите исходный расход для возврата.")
        self.refund_hint.setWordWrap(True)
        self.amount = QLineEdit()
        self.category = QComboBox()
        self.split = QCheckBox("Разделить по категориям")
        self.parts = PartsEditor()
        self.split.toggled.connect(self._update_part_fields)
        self.amount.textChanged.connect(self.parts.set_total_text)
        self.fee = QLineEdit("0,00")
        self.fee_category = QComboBox()
        self.fee_category.addItem("Без комиссии", None)
        for category in categories:
            if category.kind == CategoryKind.EXPENSE:
                self.fee_category.addItem(category_title(category, categories), category.id)
        self.note = QPlainTextEdit()
        self.note.setMaximumHeight(85)
        self.form.addRow("Вид операции", self.kind)
        self.form.addRow("Исходный расход", self.purchase)
        self.form.addRow("", self.refund_hint)
        self.form.addRow("Дата", self.occurred_on)
        self.account_label = QLabel("Счёт")
        self.form.addRow(self.account_label, self.account)
        self.form.addRow("Сумма, RUB", self.amount)
        self.form.addRow("Категория", self.category)
        self.form.addRow("", self.split)
        self.form.addRow("Части операции", self.parts)
        self.form.addRow("Счёт получения", self.destination)
        self.form.addRow("Комиссия, RUB", self.fee)
        self.form.addRow("Категория комиссии", self.fee_category)
        self.form.addRow("Примечание", self.note)
        self.tag_selector = TagSelector(tags or [])
        self.tag_selector.set_values(self._existing_tag_ids)
        self.form.addRow("Теги", self.tag_selector)
        self.form.setRowVisible(self.tag_selector, allow_tags)
        self.form_host = QWidget()
        self.form_host.setObjectName("operationBody")
        self.form_host.setLayout(self.form)
        self.form_scroll = QScrollArea()
        self.form_scroll.setObjectName("operationScroll")
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setWidget(self.form_host)
        layout.addWidget(self.form_scroll, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            "Применить к предпросмотру" if preview_mode else "Сохранить"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.purchase.currentIndexChanged.connect(self._refund_purchase_changed)
        self.kind.currentIndexChanged.connect(self._update_fields)
        self.kind.setCurrentIndex(self.kind.findData((initial.kind if initial else kind).value))
        self._update_fields()
        if initial is not None:
            draft = initial
            if draft.kind == OperationKind.REFUND:
                self.purchase.setCurrentIndex(self.purchase.findData(draft.refund_of))
            self.occurred_on.setDate(
                QDate(draft.occurred_on.year, draft.occurred_on.month, draft.occurred_on.day)
            )
            self.account.setCurrentIndex(self.account.findData(draft.account_id))
            self.amount.setText(format_money(draft.amount, include_currency=False))
            self.category.setCurrentIndex(self.category.findData(draft.category_id))
            self.destination.setCurrentIndex(
                self.destination.findData(draft.destination_account_id)
            )
            self.fee.setText(format_money(draft.fee, include_currency=False))
            self.fee_category.setCurrentIndex(self.fee_category.findData(draft.fee_category_id))
            self.note.setPlainText(draft.note)
            self.split.setChecked(bool(draft.parts))
            if draft.parts:
                self.parts.set_parts(draft.parts)
                self.split.setChecked(True)
        elif kind == OperationKind.REFUND:
            if refund_of_id is not None:
                self.purchase.setCurrentIndex(self.purchase.findData(refund_of_id))
            elif len(self._refund_candidates) == 1:
                self.purchase.setCurrentIndex(1)
        elif account_id is not None:
            self.account.setCurrentIndex(self.account.findData(account_id))
        screen = self.screen()
        available_height = screen.availableGeometry().height() if screen is not None else 900
        preferred = max(420, min(820, self.form_host.sizeHint().height() + 140))
        self.resize(650, min(preferred, max(360, available_height - 80)))

    def _update_fields(self) -> None:
        kind = OperationKind(self.kind.currentData())
        transfer = kind == OperationKind.TRANSFER
        self.account_label.setText("Счёт получения" if kind == OperationKind.REFUND else "Счёт")
        self.form.setRowVisible(
            self.category,
            kind in (OperationKind.INCOME, OperationKind.EXPENSE, OperationKind.REFUND),
        )
        for widget in (self.destination, self.fee, self.fee_category):
            self.form.setRowVisible(widget, transfer)
        self.form.setRowVisible(self.purchase, kind == OperationKind.REFUND)
        self.form.setRowVisible(self.refund_hint, kind == OperationKind.REFUND)
        self._update_category_choices()
        if kind == OperationKind.REFUND:
            self._refund_purchase_changed()
        self._update_part_fields()
        self.amount.setPlaceholderText(
            "Можно указать ноль или отрицательный остаток"
            if kind == OperationKind.OPENING
            else "Например, 1 234,56"
        )

    def _update_category_choices(self) -> None:
        kind = OperationKind(self.kind.currentData())
        required = CategoryKind.INCOME if kind == OperationKind.INCOME else CategoryKind.EXPENSE
        allowed: set[str] | None = None
        if kind == OperationKind.REFUND:
            candidate = self._refund_candidates.get(self.purchase.currentData())
            allowed = (
                {part.category_id for part in candidate.remaining_parts} if candidate else set()
            )
        choices = [
            (category.id, category_title(category, self._categories))
            for category in self._categories
            if category.kind == required and (allowed is None or category.id in allowed)
        ]
        previous = self.category.currentData()
        self.category.clear()
        self.category.addItem("Выберите категорию", None)
        for identifier, title in choices:
            self.category.addItem(title, identifier)
        index = self.category.findData(previous)
        self.category.setCurrentIndex(max(index, 0))
        self.parts.set_categories(choices)

    def _refund_purchase_changed(self) -> None:
        if self.kind.currentData() != OperationKind.REFUND.value:
            return
        self._update_category_choices()
        candidate = self._refund_candidates.get(self.purchase.currentData())
        if candidate is None:
            self.refund_hint.setText(
                "Выберите исходный расход. Возврат уменьшает расходы на свою дату."
            )
            return
        self.refund_hint.setText(
            "Осталось к возврату: "
            + format_money(candidate.amount_remaining)
            + ". Укажите фактически полученную сумму; можно выбрать другой счёт той же валюты."
        )
        self.amount.setText(format_money(candidate.amount_remaining, include_currency=False))
        self.account.setCurrentIndex(self.account.findData(candidate.expense.draft.account_id))
        source_date = candidate.expense.draft.occurred_on
        qdate = QDate(source_date.year, source_date.month, source_date.day)
        if self.occurred_on.date() < qdate:
            self.occurred_on.setDate(qdate)
        splitting = len(candidate.remaining_parts) > 1
        self.split.setChecked(splitting)
        if splitting:
            self.parts.set_parts(candidate.remaining_parts)
        else:
            self.category.setCurrentIndex(
                self.category.findData(candidate.remaining_parts[0].category_id)
            )
        self._update_part_fields()

    def _update_part_fields(self) -> None:
        financial = self.kind.currentData() in (
            OperationKind.INCOME.value,
            OperationKind.EXPENSE.value,
            OperationKind.REFUND.value,
        )
        splitting = financial and self._allow_splits and self.split.isChecked()
        candidate = self._refund_candidates.get(self.purchase.currentData())
        can_split = (
            self.kind.currentData() != OperationKind.REFUND.value
            or candidate is not None
            and len(candidate.remaining_parts) > 1
        )
        self.form.setRowVisible(self.split, financial and self._allow_splits and can_split)
        self.form.setRowVisible(self.parts, splitting)
        self.form.setRowVisible(self.category, financial and not splitting)

    def _validate(self) -> None:
        self.result_draft = None
        try:
            kind = OperationKind(self.kind.currentData())
            candidate = self._refund_candidates.get(self.purchase.currentData())
            if kind == OperationKind.REFUND and (not self._allow_refunds or candidate is None):
                raise FinanceError("Выберите исходный расход с доступной суммой возврата.")
            selected = self.account.currentData()
            if selected is None:
                raise FinanceError("Выберите счёт операции.")
            qdate = self.occurred_on.date()
            splitting = (
                self._allow_splits
                and self.split.isChecked()
                and kind in (OperationKind.INCOME, OperationKind.EXPENSE, OperationKind.REFUND)
            )
            draft = OperationDraft(
                kind,
                date(qdate.year(), qdate.month(), qdate.day()),
                selected,
                Money.parse(self.amount.text()),
                category_id=self.category.currentData()
                if kind in (OperationKind.INCOME, OperationKind.EXPENSE, OperationKind.REFUND)
                and not splitting
                else None,
                destination_account_id=self.destination.currentData()
                if kind == OperationKind.TRANSFER
                else None,
                fee=Money.parse(self.fee.text()) if kind == OperationKind.TRANSFER else Money(0),
                fee_category_id=self.fee_category.currentData()
                if kind == OperationKind.TRANSFER
                else None,
                note=self.note.toPlainText(),
                parts=self.parts.values() if splitting else (),
                tag_ids=self.tag_selector.values() if self._allow_tags else self._existing_tag_ids,
                refund_of=candidate.expense.id
                if kind == OperationKind.REFUND and candidate is not None
                else None,
                refund_of_revision_id=candidate.expense.revision_id
                if kind == OperationKind.REFUND and candidate is not None
                else None,
            )
            if kind == OperationKind.REFUND and candidate is not None:
                validate_refund_reference(draft, candidate.expense)
                remaining = {
                    part.category_id: part.amount.minor for part in candidate.remaining_parts
                }
                if any(
                    part.amount.minor > remaining.get(part.category_id, 0)
                    for part in draft.category_allocations()
                ):
                    raise FinanceError("Превышен остаток к возврату по одной из категорий.")
            self.result_draft = draft
        except FinanceError as exc:
            QMessageBox.warning(self, "Проверьте операцию", str(exc))
            return
        self.accept()


class OperationHistoryDialog(QDialog):
    def __init__(
        self,
        versions: list[Operation],
        parent: QWidget | None = None,
        *,
        accounts: list[Account] | None = None,
        categories: list[Category] | None = None,
        related_versions: list[Operation] | None = None,
        tags: list[Tag] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("История финансовой операции")
        self.resize(980, 660)
        self._tag_names = {
            tag.id: tag.name + (" (архив)" if tag.is_deleted else "") for tag in tags or []
        }
        self._versions = versions
        self._related_versions = {value.revision_id: value for value in related_versions or []}
        self._account_names = {a.id: a.name for a in accounts or []}
        self._category_names = {c.id: category_title(c, categories or []) for c in categories or []}
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Только последняя принятая версия влияет на остатки. Удаление сохраняет историю."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        table = self.table = QTableWidget(len(versions), 6)
        table.setHorizontalHeaderLabels(
            ["Версия", "Дата", "Вид", "Сумма", "Состояние", "Примечание"]
        )
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        for row, operation in enumerate(versions):
            values = [
                str(row + 1),
                operation.draft.occurred_on.strftime("%d.%m.%Y"),
                OPERATION_LABELS[operation.draft.kind],
                format_money(operation.draft.amount),
                "Удалена" if operation.is_deleted else ("Создана" if row == 0 else "Исправлена"),
                operation.draft.note,
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        layout.addWidget(table, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(210)
        self.details.setMaximumHeight(280)
        layout.addWidget(self.details)
        table.currentCellChanged.connect(self._show_details)
        if versions:
            table.setCurrentCell(len(versions) - 1, 0)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _show_details(self, row: int) -> None:
        if not 0 <= row < len(self._versions):
            self.details.clear()
            return
        operation = self._versions[row]
        draft = operation.draft

        def account(identifier: str | None) -> str:
            return self._account_names.get(identifier or "", identifier or "—")

        def category(identifier: str | None) -> str:
            return self._category_names.get(identifier or "", identifier or "—")

        lines = [
            f"Версия: {row + 1}",
            "Состояние: " + ("Удалена" if operation.is_deleted else "Сохранена"),
            "Дата: " + draft.occurred_on.strftime("%d.%m.%Y"),
            "Вид: " + OPERATION_LABELS[draft.kind],
            "Счёт: " + account(draft.account_id),
            "Сумма: " + format_money(draft.amount),
        ]
        if draft.parts:
            lines.append("Разделение по категориям:")
            lines.extend(
                f"  {category(part.category_id)}: {format_money(part.amount)}"
                for part in draft.parts
            )
        elif draft.category_id:
            lines.append("Категория: " + category(draft.category_id))
        if draft.tag_ids:
            lines.append(
                "Теги: "
                + "; ".join(
                    self._tag_names.get(identifier, "Тег недоступен")
                    for identifier in draft.tag_ids
                )
            )
        if draft.destination_account_id:
            lines += [
                "Счёт получения: " + account(draft.destination_account_id),
                "Комиссия: " + format_money(draft.fee),
            ]
            if draft.fee_category_id:
                lines.append("Категория комиссии: " + category(draft.fee_category_id))
        if draft.refund_of is not None:
            source = self._related_versions.get(draft.refund_of_revision_id or "")
            if source is not None:
                lines.append(
                    "Исходный расход: "
                    + source.draft.occurred_on.strftime("%d.%m.%Y")
                    + " · "
                    + account(source.draft.account_id)
                    + " · "
                    + format_money(source.draft.amount)
                )
                if source.draft.note:
                    lines.append("Примечание исходного расхода: " + source.draft.note)
            else:
                lines.append("Исходный расход: " + draft.refund_of)
        lines.append("Примечание: " + (draft.note or "—"))
        self.details.setPlainText("\n".join(lines))
