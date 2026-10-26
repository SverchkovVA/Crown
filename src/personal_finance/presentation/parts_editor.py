"""Точный ручной ввод частей дохода или расхода."""

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.errors import FinanceError, ValidationError
from personal_finance.domain.ledger import OperationPart
from personal_finance.domain.models import Money
from personal_finance.presentation.money import format_money


class PartsEditor(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._categories: list[tuple[str, str]] = []
        self._total_text = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Категория части", "Сумма, RUB"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(140)
        self.table.setMaximumHeight(190)
        self.table.verticalHeader().setDefaultSectionSize(44)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("Добавить часть")
        self.remove_button = QPushButton("Убрать часть")
        self.add_button.clicked.connect(self.add_row)
        self.remove_button.clicked.connect(self.remove_row)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.add_row()
        self.add_row()

    def _category(self, row: int) -> QComboBox:
        widget = self.table.cellWidget(row, 0)
        assert isinstance(widget, QComboBox)
        return widget

    def _amount(self, row: int) -> QLineEdit:
        widget = self.table.cellWidget(row, 1)
        assert isinstance(widget, QLineEdit)
        return widget

    def _populate(self, combo: QComboBox, selected: str | None = None) -> None:
        combo.clear()
        combo.addItem("Выберите категорию", None)
        for identifier, title in self._categories:
            combo.addItem(title, identifier)
        index = combo.findData(selected)
        combo.setCurrentIndex(max(index, 0))

    def set_categories(self, categories: list[tuple[str, str]]) -> None:
        self._categories = categories
        for row in range(self.table.rowCount()):
            combo = self._category(row)
            self._populate(combo, combo.currentData())

    def add_row(self) -> None:
        if self.table.rowCount() >= 100:
            return
        row = self.table.rowCount()
        self.table.insertRow(row)
        combo = QComboBox()
        self._populate(combo)
        amount = QLineEdit()
        amount.setPlaceholderText("0,00")
        amount.textChanged.connect(self._update_summary)
        self.table.setCellWidget(row, 0, combo)
        self.table.setCellWidget(row, 1, amount)
        self._update_summary()

    def remove_row(self) -> None:
        if self.table.rowCount() <= 2:
            return
        row = self.table.currentRow()
        self.table.removeRow(row if row >= 0 else self.table.rowCount() - 1)
        self._update_summary()

    def set_parts(self, parts: tuple[OperationPart, ...]) -> None:
        self.table.setRowCount(0)
        for part in parts:
            self.add_row()
            row = self.table.rowCount() - 1
            self._category(row).setCurrentIndex(self._category(row).findData(part.category_id))
            self._amount(row).setText(format_money(part.amount, include_currency=False))
        while self.table.rowCount() < 2:
            self.add_row()

    def values(self) -> tuple[OperationPart, ...]:
        result = []
        for row in range(self.table.rowCount()):
            identifier = self._category(row).currentData()
            if identifier is None:
                raise ValidationError("Выберите категорию каждой части.")
            result.append(OperationPart(identifier, Money.parse(self._amount(row).text())))
        return tuple(result)

    def set_total_text(self, value: str) -> None:
        self._total_text = value
        self._update_summary()

    def _update_summary(self) -> None:
        self.add_button.setEnabled(self.table.rowCount() < 100)
        self.remove_button.setEnabled(self.table.rowCount() > 2)
        try:
            amounts = [
                Money.parse(self._amount(row).text()).minor for row in range(self.table.rowCount())
            ]
            if any(amount <= 0 for amount in amounts):
                raise ValidationError("Суммы частей должны быть положительными.")
            total = Money(sum(amounts))
            expected = Money.parse(self._total_text)
            difference = Money(expected.minor - total.minor)
        except FinanceError:
            self.summary.setText("Укажите сумму операции и положительные суммы всех частей.")
            return
        if difference.minor == 0:
            self.summary.setText(
                f"Распределено {format_money(total, include_currency=False)} RUB — сумма совпадает."
            )
        elif difference.minor > 0:
            self.summary.setText(
                f"Распределено {format_money(total, include_currency=False)} RUB; "
                f"осталось {format_money(difference, include_currency=False)} RUB."
            )
        else:
            self.summary.setText(
                f"Сумма частей превышает сумму операции "
                f"на {format_money(Money(-difference.minor), include_currency=False)} RUB."
            )
