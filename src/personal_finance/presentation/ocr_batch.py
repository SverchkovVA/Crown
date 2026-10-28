"""Редактируемый список OCR: общие настройки, одна атомарная запись и сохранение правок."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from html import escape
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QThreadPool, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from personal_finance.application.ocr_cards import OcrCardPreview, OcrHistoryPreview
from personal_finance.application.ocr_importing import OcrImporting
from personal_finance.application.ocr_review import OcrReviewDefaults, suggest_corrections
from personal_finance.domain.errors import FinanceError, ValidationError
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import Account, Category, CategoryKind
from personal_finance.domain.ocr_cards import CardCorrections, CashDirection, review_card
from personal_finance.domain.ocr_imports import (
    OcrImportPreview,
    OcrImportResult,
    OcrImportStatus,
    OcrSelection,
)
from personal_finance.domain.ocr_settings import OcrPreferences
from personal_finance.presentation.jobs import Job
from personal_finance.presentation.ocr_preview import (
    OcrPreviewDialog,
    OptionalMaskedEdit,
    SourceImageView,
    plain,
)


@dataclass
class BatchRow:
    include: QCheckBox
    occurred_on: OptionalMaskedEdit
    amount: QLineEdit
    account: QComboBox
    kind: QComboBox
    category: QComboBox
    other: QComboBox
    state: QTableWidgetItem
    description: str
    local_time: str
    saved: bool = False


class BatchDecisionDialog(QDialog):
    def __init__(
        self, previews: tuple[OcrImportPreview, ...], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Добавить выбранные операции OCR")
        self.resize(1000, 520)
        self.result_selections: tuple[OcrSelection, ...] = ()
        self.previews = previews
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain(
                "Сверьте список. Связь с существующей операцией не создаёт нового движения. "
                "Для возможного повтора выберите действие; "
                "точные повторы и конфликты не записываются."
            )
        )
        self.table = QTableWidget(len(previews), 4)
        self.table.setHorizontalHeaderLabels(["Дата", "Движение, RUB", "Состояние", "Действие"])
        self.choices: list[QComboBox] = []
        for index, preview in enumerate(previews):
            value = preview.selection.evidence.reviewed
            self.table.setItem(index, 0, QTableWidgetItem(value.occurred_on.strftime("%d.%m.%Y")))
            sign = "+" if value.direction == CashDirection.IN else "−"
            self.table.setItem(
                index,
                1,
                QTableWidgetItem(
                    sign + f"{value.amount.minor // 100},{value.amount.minor % 100:02d}"
                ),
            )
            self.table.setItem(index, 2, QTableWidgetItem(preview.message))
            combo = QComboBox()
            combo.addItem("Не добавлять", None)
            if preview.status in (OcrImportStatus.NEW, OcrImportStatus.POSSIBLE):
                combo.addItem("Добавить отдельную операцию", "new")
                if preview.status == OcrImportStatus.NEW:
                    combo.setCurrentIndex(1)
                for candidate in preview.candidates:
                    title = (
                        candidate.draft.occurred_on.strftime("%d.%m.%Y")
                        + " · "
                        + candidate.draft.note[:60]
                    )
                    combo.addItem("Связать: " + title, candidate)
            self.choices.append(combo)
            self.table.setCellWidget(index, 3, combo)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Добавить в книгу")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Вернуться к списку")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        values = []
        for preview, combo in zip(self.previews, self.choices, strict=True):
            choice = combo.currentData()
            if choice is None:
                continue
            value = preview.selection
            if choice == "new":
                value = replace(
                    value, acknowledged_duplicate=preview.status == OcrImportStatus.POSSIBLE
                )
            else:
                value = replace(
                    value,
                    draft=choice.draft,
                    existing_operation_id=choice.id,
                    expected_revision_id=choice.revision_id,
                    acknowledged_duplicate=False,
                )
            values.append(value)
        if not values:
            QMessageBox.information(
                self, "Ничего не выбрано", "Выберите хотя бы одну новую операцию или связь."
            )
            return
        self.result_selections = tuple(values)
        self.accept()


class OcrBatchDialog(QDialog):
    def __init__(
        self,
        preview: OcrHistoryPreview,
        importing: OcrImporting,
        accounts: list[Account],
        categories: list[Category],
        preferences: OcrPreferences,
        parent: QWidget | None = None,
        *,
        on_saved: Callable[[tuple[OcrImportResult, ...]], None] = lambda _: None,
        on_busy: Callable[[bool], None] = lambda _: None,
    ) -> None:
        super().__init__(parent)
        self.preview, self.importing, self.preferences = preview, importing, preferences
        self.accounts, self.categories = accounts, categories
        self._on_saved, self._on_busy = on_saved, on_busy
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._job: Job | None = None
        self._result: Any = None
        self._error = ""
        self._callback: Callable[[Any], None] = lambda _: None
        self.saved_count = 0
        self.setWindowTitle(f"{preview.bank_name} · Операции из изображения")
        self.resize(1380, 800)
        self.setMinimumSize(900, 540)
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            self.resize(
                min(1380, max(900, available.width() - 50)),
                min(800, max(540, available.height() - 70)),
            )
        layout = QVBoxLayout(self)
        self.notice = plain(
            preview.history.blocked_reason
            or f"Найдено строк: {len(preview.history.cards)}. "
            "Выберите счёт для списка и проверьте предложения. "
            f"Неизвестная валюта: {preferences.default_currency}; "
            f"год без указания: {preferences.year}. "
            "Правки остаются в этом окне после отмены или ошибки. "
            "Перевод между своими счетами не является доходом."
        )
        layout.addWidget(self.notice)
        self.content = QWidget()
        body = QVBoxLayout(self.content)
        common = QHBoxLayout()
        self.common_account = QComboBox()
        self.common_account.addItem("Выберите счёт для списка", None)
        for account in accounts:
            self.common_account.addItem(account.name, account.id)
        common.addWidget(plain("Счёт для незаполненных строк"))
        common.addWidget(self.common_account, 1)
        body.addLayout(common)
        self.selection_note = plain("Выберите строку для просмотра источника")
        body.addWidget(self.selection_note)
        details = QPushButton("Изменить поля выбранной строки")
        details.clicked.connect(self.edit_selected)
        common.addWidget(details)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.image = SourceImageView(preview.source.image, preview.history.document)
        split.addWidget(self.image)
        self.table = QTableWidget(len(preview.history.cards), 8)
        self.table.setHorizontalHeaderLabels(
            [
                "Включить",
                "Дата",
                "Сумма, RUB",
                "Счёт",
                "Вид",
                "Категория",
                "Второй свой счёт",
                "Состояние",
            ]
        )
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.rows: list[BatchRow] = []
        self._row_widgets: dict[QObject, int] = {}
        defaults = OcrReviewDefaults(
            preferences.default_currency, preferences.year, preferences.confirm_fields
        )
        for index, card in enumerate(preview.history.cards):
            corrections = suggest_corrections(card, defaults)
            include = QCheckBox()
            include.setAccessibleName(f"Включить строку {index + 1}")
            include.setChecked(not bool(card.blocked_reason))
            occurred = OptionalMaskedEdit("99.99.9999;_")
            occurred.setText(corrections.occurred_on)
            amount = QLineEdit(
                (
                    "+"
                    if corrections.direction == CashDirection.IN
                    else "−"
                    if corrections.direction == CashDirection.OUT
                    else ""
                )
                + corrections.amount
            )
            account_combo = QComboBox()
            other = QComboBox()
            for combo in (account_combo, other):
                combo.addItem("Выберите счёт", None)
                for account in accounts:
                    combo.addItem(account.name, account.id)
            kind = QComboBox()
            kind.addItem("Расход", OperationKind.EXPENSE)
            kind.addItem("Доход", OperationKind.INCOME)
            kind.addItem("Перевод между своими", OperationKind.TRANSFER)
            kind.addItem("Не импортировать", "exclude")
            kind.setCurrentIndex(1 if corrections.direction == CashDirection.IN else 0)
            category = QComboBox()
            state = QTableWidgetItem(card.blocked_reason or "Предложение")
            state.setToolTip("<p>" + escape(corrections.description) + "</p>")
            self.table.setItem(index, 7, state)
            for editor_widget, title in (
                (occurred, "Дата"),
                (amount, "Сумма"),
                (account_combo, "Счёт"),
                (kind, "Вид"),
                (category, "Категория"),
                (other, "Второй свой счёт"),
            ):
                editor_widget.setAccessibleName(f"{title} строки {index + 1}")
            for column, widget in enumerate(
                (include, occurred, amount, account_combo, kind, category, other)
            ):
                self.table.setCellWidget(index, column, widget)
                self._row_widgets[widget] = index
                widget.installEventFilter(self)
            row = BatchRow(
                include,
                occurred,
                amount,
                account_combo,
                kind,
                category,
                other,
                state,
                corrections.description,
                corrections.local_time,
            )
            self.rows.append(row)
            matches = preferences.matching_parties(corrections.description)
            if len(matches) == 1:
                party = matches[0]
                kind.setCurrentIndex(3 if party.exclude else 2)
                other.setCurrentIndex(max(0, other.findData(party.account_id)))
                if party.exclude:
                    include.setChecked(False)
            elif len(matches) > 1:
                include.setChecked(False)
                state.setText("Несколько правил имени: выберите действие")
            self._kind_changed(index)
            kind.currentIndexChanged.connect(lambda _, i=index: self._kind_changed(i))
            for field in (occurred, amount):
                field.textChanged.connect(self._edited)
            for combo in (account_combo, category, other):
                combo.currentIndexChanged.connect(self._edited)
            include.toggled.connect(self._edited)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._show_source)
        split.addWidget(self.table)
        split.setSizes([360, 1000])
        body.addWidget(split, 1)
        layout.addWidget(self.content, 1)
        self.checked: QCheckBox | None = None
        if preferences.confirm_fields:
            self.checked = QCheckBox("Суммы, валюты, направления и даты выбранных строк сверены")
            self.checked.toggled.connect(self._update_button)
            layout.addWidget(self.checked)
        tools = QHBoxLayout()
        self.save = QPushButton("Добавить выбранные операции")
        self.save.clicked.connect(self.prepare)
        tools.addWidget(self.save)
        self.close_button = QPushButton("Закрыть")
        self.close_button.clicked.connect(self.reject)
        tools.addWidget(self.close_button)
        layout.addLayout(tools)
        self.common_account.currentIndexChanged.connect(self._common_changed)
        if self.rows:
            self.table.selectRow(0)
        self._update_button()

    def _kind_changed(self, index: int) -> None:
        row = self.rows[index]
        kind = row.kind.currentData()
        selected = row.category.currentData()
        row.category.clear()
        if kind in (OperationKind.EXPENSE, OperationKind.INCOME):
            desired = CategoryKind.EXPENSE if kind == OperationKind.EXPENSE else CategoryKind.INCOME
            for category in self.categories:
                if category.kind == desired:
                    row.category.addItem(category.name, category.id)
            target = row.category.findData(selected)
            if target >= 0:
                row.category.setCurrentIndex(target)
            else:
                other = next(
                    (
                        c.id
                        for c in self.categories
                        if c.kind == desired and c.name.startswith("Другие")
                    ),
                    None,
                )
                if other:
                    row.category.setCurrentIndex(row.category.findData(other))
        row.category.setEnabled(kind in (OperationKind.INCOME, OperationKind.EXPENSE))
        row.other.setEnabled(kind == OperationKind.TRANSFER)
        if kind == "exclude":
            row.include.setChecked(False)
            row.state.setText("Исключено: не меняет остатки и доходы")
        self._edited()

    def _common_changed(self) -> None:
        identifier = self.common_account.currentData()
        if identifier is not None:
            for row in self.rows:
                if not row.saved and row.account.currentData() is None:
                    row.account.setCurrentIndex(row.account.findData(identifier))

    def _edited(self) -> None:
        if hasattr(self, "checked") and self.checked is not None:
            self.checked.setChecked(False)
        if hasattr(self, "save"):
            self._update_button()

    def _update_button(self) -> None:
        selected = sum(row.include.isChecked() and not row.saved for row in self.rows)
        self.save.setText(f"Добавить выбранные ({selected})")
        self.save.setEnabled(
            self._job is None
            and selected > 0
            and not self.preview.history.blocked_reason
            and (self.checked is None or self.checked.isChecked())
        )

    def _show_source(self) -> None:
        index = self.table.currentRow()
        if 0 <= index < len(self.rows):
            self.image.show_evidence(self.preview.history.cards[index].amount.word_indices)
            self.selection_note.setText(
                f"Выбрана строка {index + 1} из {len(self.rows)} · " + self.rows[index].description
            )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        index = self._row_widgets.get(watched)
        if index is not None and event.type() in (
            QEvent.Type.FocusIn,
            QEvent.Type.MouseButtonPress,
        ):
            self.table.setCurrentCell(index, 0)
            self._show_source()
        return super().eventFilter(watched, event)

    def edit_selected(self) -> None:
        index = self.table.currentRow()
        if not 0 <= index < len(self.rows) or self.rows[index].saved:
            return
        row, card = self.rows[index], self.preview.history.cards[index]
        defaults = OcrReviewDefaults(
            self.preferences.default_currency,
            self.preferences.year,
            self.preferences.confirm_fields,
        )
        dialog = OcrPreviewDialog(
            OcrCardPreview(self.preview.source, card, self.preview.bank_name),
            self,
            review_defaults=defaults,
        )
        raw = row.amount.text().strip()
        if raw.startswith(("+", "-", "−")):
            dialog.direction.setCurrentIndex(
                dialog.direction.findData(CashDirection.IN if raw[0] == "+" else CashDirection.OUT)
            )
            raw = raw[1:].strip()
        dialog.amount.setText(raw)
        dialog.occurred_on.setText(row.occurred_on.text())
        dialog.local_time.setText(row.local_time)
        dialog.description.setText(row.description)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.reviewed is not None:
                value = dialog.reviewed
                row.amount.setText(
                    ("+" if value.direction == CashDirection.IN else "−")
                    + f"{value.amount.minor // 100},{value.amount.minor % 100:02d}"
                )
                row.occurred_on.setText(value.occurred_on.strftime("%d.%m.%Y"))
                row.local_time = value.local_time.strftime("%H:%M") if value.local_time else ""
                row.description = value.description
                row.state.setText("Поля изменены")
                self._edited()
                self._show_source()
        finally:
            dialog.deleteLater()

    def _selection(self, index: int) -> OcrSelection:
        row, card = self.rows[index], self.preview.history.cards[index]
        raw = row.amount.text().strip()
        if not raw.startswith(("+", "-", "−")):
            raise ValidationError("Укажите сумму со знаком: + для поступления, − для списания.")
        direction = CashDirection.IN if raw[0] == "+" else CashDirection.OUT
        reviewed = review_card(
            card,
            CardCorrections(
                raw[1:].strip(),
                card.currency.value or self.preferences.default_currency,
                direction,
                row.occurred_on.text(),
                row.local_time,
                row.description,
            ),
            confirmed_fields=frozenset({"amount", "currency", "direction", "occurred_on"}),
            single_completed_operation=True,
        )
        account = row.account.currentData()
        if account is None:
            raise ValidationError("Выберите счёт этой строки или общий счёт списка.")
        kind = row.kind.currentData()
        if kind == "exclude":
            raise ValidationError("Снимите включение исключённой строки.")
        kind = OperationKind(kind)
        other = row.other.currentData()
        if kind == OperationKind.TRANSFER:
            if other is None or other == account:
                raise ValidationError("Для перевода выберите второй собственный счёт.")
            source, destination = (
                (other, account) if direction == CashDirection.IN else (account, other)
            )
            draft = OperationDraft(
                kind,
                reviewed.occurred_on,
                source,
                reviewed.amount,
                destination_account_id=destination,
                note=reviewed.description,
            )
        else:
            draft = OperationDraft(
                kind,
                reviewed.occurred_on,
                account,
                reviewed.amount,
                category_id=row.category.currentData(),
                note=reviewed.description,
            )
        return OcrImporting.prepare(
            OcrCardPreview(self.preview.source, card, self.preview.bank_name),
            reviewed,
            account,
            draft,
        )

    def prepare(self) -> None:
        if self.checked is not None and not self.checked.isChecked():
            self.notice.setText("Сверьте выбранные поля либо отключите флажки в настройках.")
            return
        values = []
        failed = False
        for index, row in enumerate(self.rows):
            if row.saved or not row.include.isChecked():
                continue
            try:
                values.append(self._selection(index))
                row.state.setText("Подготовлено")
            except FinanceError as error:
                row.state.setText(str(error))
                failed = True
        if failed or not values:
            self.notice.setText(
                "Исправьте отмеченные строки. Ничего не записано; правки сохранены в этом окне."
            )
            return
        selections = tuple(values)
        self._start(lambda: self.importing.preview_many(selections), self._prepared)

    def _prepared(self, previews: tuple[OcrImportPreview, ...]) -> None:
        if not self.preferences.review_before_save:
            blocked = {
                p.selection.evidence.source_key: p.message
                for p in previews
                if p.status in (OcrImportStatus.DUPLICATE, OcrImportStatus.CONFLICT)
            }
            for index, row in enumerate(self.rows):
                if row.saved or not row.include.isChecked():
                    continue
                key = self._selection(index).evidence.source_key
                if key in blocked:
                    row.state.setText(blocked[key])
                    row.include.setChecked(False)
            previews = tuple(
                p for p in previews if p.status in (OcrImportStatus.NEW, OcrImportStatus.POSSIBLE)
            )
            if not previews:
                self.notice.setText(
                    "Новых строк нет. Повторы и конфликты отмечены в списке; ничего не записано."
                )
                self._update_button()
                return
        if self.preferences.review_before_save or any(
            p.status != OcrImportStatus.NEW for p in previews
        ):
            dialog = BatchDecisionDialog(previews, self)
            try:
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                selections = dialog.result_selections
            finally:
                dialog.deleteLater()
        else:
            selections = tuple(p.selection for p in previews)
        self._start(
            lambda: self.importing.confirm_many(selections),
            lambda result: self._saved(result, selections),
        )

    def _saved(
        self, result: tuple[OcrImportResult, ...], selections: tuple[OcrSelection, ...]
    ) -> None:
        keys = {v.evidence.source_key for v in selections}
        for index, row in enumerate(self.rows):
            if row.saved:
                continue
            try:
                selected = self._selection(index)
            except FinanceError:
                continue
            if selected.evidence.source_key in keys:
                row.saved = True
                row.include.setChecked(False)
                for widget in (
                    row.include,
                    row.occurred_on,
                    row.amount,
                    row.account,
                    row.kind,
                    row.category,
                    row.other,
                ):
                    widget.setEnabled(False)
                row.state.setText("Сохранено")
        self.saved_count += len(result)
        self.notice.setText(
            f"Сохранено: {len(result)}. Всего в этом окне: {self.saved_count}. "
            "Остальные правки сохранены."
        )
        self._on_saved(result)
        self._update_button()

    def _start(self, action: Callable[[], object], callback: Callable[[Any], None]) -> None:
        if self._job is not None:
            return
        self._result, self._error, self._callback = None, "", callback
        self.content.setEnabled(False)
        self.save.setEnabled(False)
        self.close_button.setEnabled(False)
        self._on_busy(True)
        job = Job(action, self)
        self._job = job
        job.signals.success.connect(self._success)
        job.signals.failure.connect(self._failure)
        job.signals.finished.connect(self._finished)
        self._pool.start(job)

    @Slot(object)
    def _success(self, value: object) -> None:
        self._result = value

    @Slot(str)
    def _failure(self, message: str) -> None:
        self._error = message

    @Slot()
    def _finished(self) -> None:
        self._pool.waitForDone()
        job = self._job
        if job is None:
            return
        for signal, slot in (
            (job.signals.success, self._success),
            (job.signals.failure, self._failure),
            (job.signals.finished, self._finished),
        ):
            signal.disconnect(slot)
        job.signals.deleteLater()
        self._job = None
        callback, value, error = self._callback, self._result, self._error
        self._callback = lambda _: None
        self._result = None
        self.content.setEnabled(True)
        self.close_button.setEnabled(True)
        self._on_busy(False)
        self._update_button()
        if error:
            self.notice.setText(error + " Правки остаются в списке.")
            QMessageBox.warning(self, "Операции не записаны", error)
        else:
            callback(value)

    def reject(self) -> None:
        if self._job is None:
            super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._job is not None:
            event.ignore()
        else:
            super().closeEvent(event)
