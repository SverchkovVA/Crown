"""Русский интерфейс закрытия месяцев и просмотра причин открытия."""

from datetime import datetime

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.errors import FinanceError
from personal_finance.domain.periods import AccountingMonth, PeriodRevision, validate_period_change
from personal_finance.presentation.tables import replace_table


def local_time(value: str) -> str:
    return datetime.fromisoformat(value).astimezone().strftime("%d.%m.%Y %H:%M:%S")


class PeriodChangeDialog(QDialog):
    def __init__(
        self, month: AccountingMonth, is_closed: bool, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(("Закрыть период " if is_closed else "Открыть период ") + month.title)
        self.setMinimumWidth(550)
        self._month, self._is_closed = month, is_closed
        self.result_reason: str | None = None
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Закрытие запретит добавление, исправление и удаление операций этого месяца. "
            "Просмотр и резервирование останутся доступны."
            if is_closed
            else "Повторное открытие разрешит изменения операций этого месяца. "
            "Укажите причину: она останется в истории периода."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.reason = QPlainTextEdit()
        self.reason.setMaximumHeight(130)
        form = QFormLayout()
        form.addRow("Причина (необязательно)" if is_closed else "Причина открытия", self.reason)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Закрыть период" if is_closed else "Открыть период"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate(self) -> None:
        self.result_reason = None
        try:
            self.result_reason = validate_period_change(
                self._month, self._is_closed, self.reason.toPlainText()
            )
        except FinanceError as exc:
            QMessageBox.warning(self, "Проверьте причину", str(exc))
            return
        self.accept()


class PeriodHistoryDialog(QDialog):
    def __init__(self, versions: list[PeriodRevision], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("История периода" + (" " + versions[0].month.title if versions else ""))
        self.resize(850, 500)
        layout = QVBoxLayout(self)
        text = QLabel(
            "Закрытие и повторное открытие сохраняются отдельными версиями. Время показано местное."
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        self.table = QTableView()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.model = replace_table(
            self.table,
            ["Версия", "Дата и время", "Состояние", "Причина"],
            [
                [
                    str(index + 1),
                    local_time(value.created_at),
                    "Закрыт" if value.is_closed else "Открыт",
                    value.reason or "—",
                ]
                for index, value in enumerate(versions)
            ],
        )
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class PeriodPage(QWidget):
    closeRequested = Signal()
    reopenRequested = Signal()
    historyRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._states: dict[AccountingMonth, PeriodRevision] = {}
        self._available = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 20, 0, 0)
        intro = QLabel(
            "Закройте проверенный месяц, чтобы защитить его операции от изменений. "
            "Для исправления сначала явно откройте месяц с указанием причины."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        row = QHBoxLayout()
        row.addWidget(QLabel("Месяц"))
        self.month_edit = QDateEdit(QDate.currentDate())
        self.month_edit.setMinimumDate(QDate(1, 1, 1))
        self.month_edit.setDisplayFormat("MM.yyyy")
        self.month_edit.setCalendarPopup(True)
        row.addWidget(self.month_edit)
        self.close_button = QPushButton("Закрыть период")
        self.reopen_button = QPushButton("Открыть период")
        self.history_button = QPushButton("История")
        for widget in (self.close_button, self.reopen_button, self.history_button):
            row.addWidget(widget)
        row.addStretch()
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QTableView()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.month_edit.dateChanged.connect(self._update_buttons)
        self.close_button.clicked.connect(self.closeRequested)
        self.reopen_button.clicked.connect(self.reopenRequested)
        self.history_button.clicked.connect(self.historyRequested)
        self.set_states([], False)

    def month(self) -> AccountingMonth:
        value = self.month_edit.date()
        return AccountingMonth(value.year(), value.month())

    def selected_state(self) -> PeriodRevision | None:
        return self._states.get(self.month())

    def set_states(self, states: list[PeriodRevision], available: bool) -> None:
        self._states = {value.month: value for value in states}
        self._ordered = states
        self._available = available
        self.model = replace_table(
            self.table,
            ["Месяц", "Состояние", "Последнее изменение", "Причина"],
            [
                [
                    value.month.title,
                    "Закрыт" if value.is_closed else "Открыт",
                    local_time(value.created_at),
                    value.reason or "—",
                ]
                for value in states
            ],
            selection_changed=self._select_month,
        )
        self._update_buttons()

    def _select_month(self) -> None:
        row = self.table.currentIndex().row()
        if self.table.selectionModel().hasSelection() and 0 <= row < len(self._ordered):
            month = self._ordered[row].month
            self.month_edit.setDate(QDate(month.year, month.month, 1))

    def _update_buttons(self) -> None:
        current = self.selected_state()
        closed = current is not None and current.is_closed
        self.month_edit.setEnabled(self._available)
        self.close_button.setEnabled(self._available and not closed)
        self.reopen_button.setEnabled(self._available and closed)
        self.history_button.setEnabled(self._available and current is not None)
        self.status.setText(
            "Период закрыт: операции защищены от добавления, исправления и удаления."
            if self._available and closed
            else (
                "Период открыт: финансовые операции разрешены. "
                "Ниже показаны месяцы с историей изменений."
            )
            if self._available
            else (
                "Для закрытия периодов создайте проверенную копию этой книги "
                "и восстановите её в новую папку."
            )
        )
