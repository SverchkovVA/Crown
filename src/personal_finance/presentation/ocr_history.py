"""Выбор строки истории для отдельной проверки без записи финансов."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from personal_finance.application.ocr_cards import OcrCardPreview, OcrHistoryPreview
from personal_finance.domain.ocr_cards import CashDirection, ReviewedOcrCard
from personal_finance.presentation.ocr_preview import OcrPreviewDialog, SourceImageView, plain
from personal_finance.presentation.tables import replace_table


class OcrHistoryDialog(QDialog):
    def __init__(self, preview: OcrHistoryPreview, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.preview = preview
        self.reviews: dict[int, ReviewedOcrCard] = {}
        self.setWindowTitle(f"История · {preview.bank_name}: проверка строк")
        self.resize(1180, 720)
        self.setMinimumSize(920, 580)
        layout = QVBoxLayout(self)
        notice = preview.history.blocked_reason or (
            f"Предложено строк: {len(preview.history.cards)}. "
            f"Денежных фрагментов вне строк: {preview.history.unassigned_fragments}. "
            "Проверьте источник и каждую выбранную строку. "
            "Проверки хранятся до закрытия окна; запись в книгу ещё не подключена."
        )
        layout.addWidget(plain(notice))
        split = QSplitter(Qt.Orientation.Horizontal)
        self.image = SourceImageView(preview.source.image, preview.history.document)
        split.addWidget(self.image)
        panel = QWidget()
        right = QVBoxLayout(panel)
        self.table = QTableView()
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        right.addWidget(self.table)
        self.review_button = QPushButton("Проверить выбранную строку")
        self.review_button.clicked.connect(self.review_selected)
        right.addWidget(self.review_button)
        tools = QHBoxLayout()
        for title, action in (
            ("Вписать", self.image.fit_image),
            ("Увеличить", lambda: self.image.zoom(1.3)),
        ):
            button = QPushButton(title)
            button.clicked.connect(action)
            tools.addWidget(button)
        right.addLayout(tools)
        split.addWidget(panel)
        split.setSizes([480, 680])
        layout.addWidget(split, 1)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self._refresh()
        if preview.history.cards:
            self.table.selectRow(0)

    def _refresh(self) -> None:
        rows = []
        for index, original in enumerate(self.preview.history.cards):
            reviewed = self.reviews.get(index)
            minor = reviewed.amount.minor if reviewed else original.amount.value
            occurred = reviewed.occurred_on if reviewed else original.occurred_on.value
            direction = reviewed.direction if reviewed else original.direction.value
            rows.append(
                [
                    str(index + 1),
                    occurred.isoformat() if occurred else "Уточните дату/год",
                    (
                        (
                            "−"
                            if direction == CashDirection.OUT
                            else "+"
                            if direction == CashDirection.IN
                            else "?"
                        )
                        + f"{minor // 100},{minor % 100:02d} "
                        + ("RUB" if reviewed else original.currency.value or "?")
                    )
                    if minor is not None
                    else "Не определена",
                    "Проверено" if reviewed else original.blocked_reason or "Требует проверки",
                ]
            )
        self.model = replace_table(
            self.table,
            ["Строка", "Дата", "Сумма и валюта", "Проверка"],
            rows,
            selection_changed=self._selected,
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.review_button.setEnabled(bool(rows) and not self.preview.history.blocked_reason)

    def _selected(self) -> None:
        selection = self.table.selectionModel().selectedRows()
        index = selection[0].row() if selection else -1
        if 0 <= index < len(self.preview.history.cards):
            self.image.show_evidence(self.preview.history.cards[index].amount.word_indices)

    def review_selected(self) -> None:
        index = self.table.currentIndex().row()
        if self.preview.history.blocked_reason or not 0 <= index < len(self.preview.history.cards):
            return
        dialog = OcrPreviewDialog(
            OcrCardPreview(
                self.preview.source, self.preview.history.cards[index], self.preview.bank_name
            ),
            self,
        )
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.reviewed is not None:
                self.reviews[index] = dialog.reviewed
                self._refresh()
                self.table.selectRow(index)
        finally:
            dialog.deleteLater()
