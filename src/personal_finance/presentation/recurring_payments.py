"""Сценарный календарь платежей во встроенных формах отчётов."""

from collections.abc import Sequence
from datetime import date

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import RUB, Account, Money
from personal_finance.domain.recurring_payments import (
    PaymentDirection,
    PaymentForecast,
    PaymentSchedule,
    RepeatUnit,
)
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.deposit_scenarios import date_text
from personal_finance.presentation.inline_forms import present_form
from personal_finance.presentation.tables import replace_table

DIRECTION_LABELS = {
    PaymentDirection.INCOME: "Поступление",
    PaymentDirection.EXPENSE: "Расход",
}
REPEAT_LABELS = {
    RepeatUnit.ONCE: "Один раз",
    RepeatUnit.DAYS: "По дням",
    RepeatUnit.WEEKS: "По неделям",
    RepeatUnit.MONTHS: "По месяцам",
}

REPEAT_SUFFIXES = {
    RepeatUnit.DAYS: "дн.",
    RepeatUnit.WEEKS: "нед.",
    RepeatUnit.MONTHS: "мес.",
}


def _date_input(day: QDate, name: str) -> QDateEdit:
    field = QDateEdit()
    field.setDateRange(QDate(1, 1, 1), QDate(9999, 12, 31))
    field.setDate(day)
    field.setCalendarPopup(True)
    field.setDisplayFormat("dd.MM.yyyy")
    field.setAccessibleName(name)
    return field


def _date(field: QDateEdit) -> date:
    day = field.date()
    return date(day.year(), day.month(), day.day())


def _table(name: str) -> QTableView:
    table = QTableView()
    table.setAccessibleName(name)
    table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
    table.setWordWrap(False)
    table.setAlternatingRowColors(True)
    table.verticalHeader().hide()
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    return table


class ScheduleEditor(QDialog):
    def __init__(
        self, day: QDate, parent: QWidget, schedule: PaymentSchedule | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Изменить условия платежа" if schedule else "Новый ожидаемый платёж")
        self.schedule: PaymentSchedule | None = None
        layout = QVBoxLayout(self)
        form = self.fields = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(6)
        self.name = QLineEdit(schedule.name if schedule else "")
        self.name.setMaxLength(120)
        self.name.setAccessibleName("Название платежа")
        self.amount = QLineEdit(
            format_report_amount(schedule.amount.minor, RUB) if schedule else ""
        )
        self.amount.setMaxLength(128)
        self.amount.setAccessibleName("Сумма платежа в рублях")
        self.direction = QComboBox()
        self.direction.setAccessibleName("Движение денег")
        for value, text in DIRECTION_LABELS.items():
            self.direction.addItem(text, value.value)
        self.direction.setCurrentIndex(
            self.direction.findData(
                (schedule.direction if schedule else PaymentDirection.EXPENSE).value
            )
        )
        self.unit = QComboBox()
        self.unit.setAccessibleName("Периодичность")
        for repeat_unit, text in REPEAT_LABELS.items():
            self.unit.addItem(text, repeat_unit.value)
        self.unit.setCurrentIndex(
            self.unit.findData((schedule.unit if schedule else RepeatUnit.ONCE).value)
        )
        first = QDate(schedule.first_on) if schedule else day
        last = QDate(schedule.last_on) if schedule and schedule.last_on is not None else first
        self.first = _date_input(first, "Первый платёж")
        self.last = _date_input(last, "Последний день расписания")
        self.interval = QSpinBox()
        self.interval.setRange(1, 1200)
        self.interval.setValue(schedule.interval if schedule else 1)
        self.interval.setAccessibleName("Интервал повторения")
        self.has_last = QCheckBox("Задать дату окончания")
        self.has_last.setChecked(schedule is not None and schedule.last_on is not None)
        form.addRow("Название", self.name)
        form.addRow("Движение", self.direction)
        form.addRow("Сумма, RUB", self.amount)
        form.addRow("Первый платёж", self.first)
        form.addRow("Повторять", self.unit)
        form.addRow("Интервал", self.interval)
        form.addRow(self.has_last)
        form.addRow("До (включительно)", self.last)
        layout.addLayout(form)
        self.feedback = _label("")
        self.feedback.setObjectName("feedbackError")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        self.apply = QPushButton("Применить условия")
        self.apply.setObjectName("primary")
        self.apply.clicked.connect(self._apply)
        layout.addWidget(self.apply)
        self.unit.currentIndexChanged.connect(self._update_repeat)
        self.has_last.toggled.connect(self._update_repeat)
        self._update_repeat()

    def _update_repeat(self) -> None:
        repeat = self.unit.currentData() != RepeatUnit.ONCE
        self.fields.setRowVisible(self.interval, repeat)
        self.fields.setRowVisible(self.has_last, repeat)
        self.fields.setRowVisible(self.last, repeat and self.has_last.isChecked())

    def _apply(self) -> None:
        unit = RepeatUnit(self.unit.currentData())
        try:
            self.schedule = PaymentSchedule(
                self.name.text(),
                Money.parse(self.amount.text(), RUB),
                PaymentDirection(self.direction.currentData()),
                _date(self.first),
                unit,
                self.interval.value() if unit is not RepeatUnit.ONCE else 1,
                _date(self.last)
                if unit is not RepeatUnit.ONCE and self.has_last.isChecked()
                else None,
            )
        except ValidationError as error:
            self.feedback.setText(str(error))
            self.feedback.show()
            return
        self.accept()


class PaymentCalendarOptions(QDialog):
    calculationRequested = Signal()

    def __init__(self, parent: QWidget, *, accounts: Sequence[Account] = ()) -> None:
        super().__init__(parent)
        self.setWindowTitle("Календарь платежей · Условия")
        self.schedules: list[PaymentSchedule] = []
        self.request: tuple[tuple[PaymentSchedule, ...], ReportPeriod, Account | None] | None = None
        layout = QVBoxLayout(self)
        dates = QHBoxLayout()
        dates.setSpacing(8)
        today = QDate.currentDate()
        self.start = _date_input(today, "Начало прогноза")
        self.end = _date_input(today.addMonths(1), "Конец прогноза")
        dates.addWidget(_label("Период с"))
        dates.addWidget(self.start, 1)
        dates.addWidget(_label("по"))
        dates.addWidget(self.end, 1)
        layout.addLayout(dates)
        self.accounts = {
            account.id: account for account in accounts if account.currency == RUB.code
        }
        self.account = QComboBox()
        self.account.setAccessibleName("Счёт для прогноза остатка")
        self.account.addItem("Только календарь платежей", None)
        for account in sorted(self.accounts.values(), key=lambda row: row.name.casefold()):
            self.account.addItem(account.name, account.id)
        if self.accounts:
            account_row = QFormLayout()
            account_row.setHorizontalSpacing(8)
            account_row.addRow("Счёт", self.account)
            layout.addLayout(account_row)
        self.balance_hint = _label(
            "Прогноз включает проводки счёта и ожидаемые платежи. "
            "Если платёж уже внесён в журнал, уберите его из ожидаемых."
        )
        self.balance_hint.hide()
        layout.addWidget(self.balance_hint)
        self.table = _table("Условия ожидаемых платежей")
        layout.addWidget(self.table, 1)
        layout.addWidget(_label("Расписание хранится до закрытия календаря."))
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.add = QPushButton("Добавить платёж")
        self.edit = QPushButton("Изменить")
        self.remove = QPushButton("Убрать из прогноза")
        for button in (self.add, self.edit, self.remove):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.feedback = _label("")
        self.feedback.setObjectName("feedbackError")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        self.calculate = QPushButton("Рассчитать")
        self.calculate.setObjectName("primary")
        layout.addWidget(self.calculate)
        self.add.clicked.connect(lambda: self._edit(None))
        self.edit.clicked.connect(lambda: self._edit(self._selected()))
        self.remove.clicked.connect(self._remove)
        self.calculate.clicked.connect(self._calculate)
        self.account.currentIndexChanged.connect(self._update_calculate)
        self._refresh()

    def _selected(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _refresh(self) -> None:
        replace_table(
            self.table,
            ["Название", "Движение", "Сумма, ₽", "Начало", "Интервал", "По"],
            [
                [
                    value.name,
                    DIRECTION_LABELS[value.direction],
                    format_report_amount(value.amount.minor, RUB),
                    date_text(value.first_on),
                    "Один раз"
                    if value.unit is RepeatUnit.ONCE
                    else f"{value.interval} {REPEAT_SUFFIXES[value.unit]}",
                    date_text(value.last_on) if value.last_on is not None else "—",
                ]
                for value in self.schedules
            ],
            right_columns=(2,),
            selection_changed=self._selection_changed,
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._update_calculate()
        self._selection_changed()

    def _update_calculate(self) -> None:
        has_account = self.account.currentData() is not None
        self.balance_hint.setVisible(has_account)
        self.calculate.setEnabled(bool(self.schedules) or has_account)

    def _selection_changed(self) -> None:
        selected = self._selected() is not None
        self.edit.setEnabled(selected)
        self.remove.setEnabled(selected)

    def _edit(self, row: int | None) -> None:
        editor = ScheduleEditor(
            self.start.date(), self, self.schedules[row] if row is not None else None
        )

        def finished(code: int) -> None:
            if code != QDialog.DialogCode.Accepted or editor.schedule is None:
                return
            if row is None:
                self.schedules.append(editor.schedule)
            else:
                self.schedules[row] = editor.schedule
            self._refresh()

        present_form(editor, None, finished)

    def _remove(self) -> None:
        row = self._selected()
        if row is not None:
            del self.schedules[row]
            self._refresh()

    def _calculate(self) -> None:
        self.request = None
        try:
            period = ReportPeriod(_date(self.start), _date(self.end))
        except ValidationError as error:
            self.feedback.setText(str(error))
            self.feedback.show()
            return
        self.feedback.hide()
        self.request = tuple(self.schedules), period, self.accounts.get(self.account.currentData())
        self.calculationRequested.emit()


class PaymentCalendarDialog(QDialog):
    def __init__(self, forecast: PaymentForecast, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.forecast = forecast
        self.setWindowTitle("Календарь платежей · Прогноз")
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(f"{date_text(forecast.period.start)} — {date_text(forecast.period.end)}")
        )
        for total in forecast.totals:
            income = format_report_amount(total.income_minor, total.currency)
            expense = format_report_amount(total.expense_minor, total.currency)
            net = format_report_amount(total.net_minor, total.currency)
            layout.addWidget(
                _label(
                    f"{total.currency.code} · Поступления {income} · Расходы {expense} "
                    f"· Разница {net}"
                )
            )
        if not forecast.events:
            layout.addWidget(_label("В выбранном периоде ожидаемых платежей нет."))
        table = self.table = _table("Календарь ожидаемых платежей")
        replace_table(
            table,
            ["Дата", "Платёж", "Движение", "Сумма", "Валюта"],
            [
                [
                    date_text(event.occurs_on),
                    event.schedule.name,
                    DIRECTION_LABELS[event.schedule.direction],
                    format_report_amount(
                        event.schedule.amount.minor, event.schedule.amount.currency
                    ),
                    event.schedule.amount.currency.code,
                ]
                for event in forecast.events
            ],
            right_columns=(3,),
        )
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table, 1)
