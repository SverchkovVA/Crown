"""Цельное обновление таблицы без публикации частично заполненных строк."""

from collections.abc import Callable
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QTableView

ACCOUNT_HEADERS = ["Название", "Тип", "Банк", "Валюта", "Остаток"]
JOURNAL_HEADERS = [
    "Дата",
    "Вид",
    "Счёт",
    "Назначение",
    "Сумма",
    "Комиссия",
    "Примечание",
    "Состояние",
    "Теги",
]


def replace_table(
    view: QTableView,
    headers: list[str],
    rows: list[list[str]],
    *,
    right_columns: tuple[int, ...] = (),
    selection_changed: Callable[[], None] | None = None,
) -> QStandardItemModel:
    model = QStandardItemModel(len(rows), len(headers), view)
    model.setHorizontalHeaderLabels(headers)
    for row, values in enumerate(rows):
        for column, value in enumerate(values):
            item = QStandardItem(value)
            item.setEditable(False)
            item.setToolTip("<p>" + escape(value).replace("\n", "<br>") + "</p>")
            if column in right_columns:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            model.setItem(row, column, item)
    old_model = view.model()
    old_selection = view.selectionModel()
    if old_selection is not None:
        old_selection.blockSignals(True)
    view.setModel(model)
    if selection_changed is not None:
        view.selectionModel().selectionChanged.connect(selection_changed)
    if old_selection is not None and old_selection.parent() is view:
        old_selection.deleteLater()
    if old_model is not None and old_model.parent() is view:
        old_model.deleteLater()
    return model
