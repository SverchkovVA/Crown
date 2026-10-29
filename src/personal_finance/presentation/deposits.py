"""Форма сценарного расчёта; не обращается к книге."""

from calendar import monthrange
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from PySide6.QtCore import QDate, QEvent, QObject
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableView,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.deposits import (
    AccrualBoundary,
    BalanceMoment,
    DayCount,
    DepositForecast,
    DepositMovement,
    DepositRate,
    DepositScenario,
    InterestRounding,
    RoundingStage,
)
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import RUB, Money
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.analytics_cashflow import _size
from personal_finance.presentation.tables import replace_table

BOUNDARIES = [
    ("Со следующего дня после открытия, по день окончания", AccrualBoundary.MATURITY_INCLUDED),
    ("Со дня открытия, до дня окончания", AccrualBoundary.OPENING_INCLUDED),
]
BALANCES = [
    ("После пополнений и снятий дня", BalanceMoment.AFTER_MOVEMENTS),
    ("До пополнений и снятий дня", BalanceMoment.BEFORE_MOVEMENTS),
]
DAY_COUNTS = [
    ("365 или 366 по году дня начисления", DayCount.CALENDAR_YEAR),
    ("Всегда 365", DayCount.FIXED_365),
]
ROUNDINGS = [
    ("Половина вверх", InterestRounding.HALF_UP),
    ("Половина к чётному", InterestRounding.HALF_EVEN),
    ("Отбросить дробную часть", InterestRounding.DOWN),
]
ROUNDING_STAGES = [
    ("В дату выплаты", RoundingStage.PAYOUT),
    ("Каждый день", RoundingStage.DAILY),
]


def monthly_payouts(start: date, end: date) -> tuple[date, ...]:
    if type(start) is not date or type(end) is not date or not start < end:
        raise ValidationError("Дата окончания должна быть позже открытия.")
    result = []
    year, month = start.year, start.month
    while (year, month) < (end.year, end.month):
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        candidate = date(year, month, min(start.day, monthrange(year, month)[1]))
        if candidate < end:
            result.append(candidate)
    result.append(end)
    return tuple(result)


def _combo(items: Sequence[tuple[str, object]], name: str) -> QComboBox:
    result = QComboBox()
    result.setAccessibleName(name)
    for text, value in items:
        result.addItem(text, value)
    result.setMinimumContentsLength(28)
    result.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    return result


class DepositScenarioDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Прогноз вклада · Условия сценария")
        _size(self, 820, 730)
        self.result_scenario: DepositScenario | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                "Введите условия своего договора. Это прогноз, без записи выплат в книгу. "
                "Валюта RUB. Изменения ставки и движения задаются на вкладке «Изменения»."
            )
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        scroll.setWidget(body)
        self.tabs = QTabWidget()
        self.tabs.addTab(scroll, "Основные условия")
        layout.addWidget(self.tabs, 1)
        self.initial = QLineEdit()
        self.initial.setPlaceholderText("Например, 100000,00")
        self.initial.setMaxLength(128)
        self.initial.setAccessibleName("Начальная сумма вклада в рублях")
        self.rate = QLineEdit()
        self.rate.setPlaceholderText("Процентов годовых, например 12,5")
        self.rate.setMaxLength(32)
        self.rate.setAccessibleName("Ставка вклада в процентах годовых")
        today = QDate.currentDate()
        self.start, self.end = QDateEdit(today), QDateEdit(today.addYears(1))
        for editor, name in (
            (self.start, "Дата открытия вклада"),
            (self.end, "Дата окончания вклада"),
        ):
            editor.setDisplayFormat("dd.MM.yyyy")
            editor.setCalendarPopup(True)
            editor.setMinimumDate(QDate(1, 1, 1))
            editor.setMaximumDate(QDate(9999, 12, 31))
            editor.setAccessibleName(name)
        self.frequency = _combo(
            [("В конце срока", "maturity"), ("Ежемесячно в число открытия", "monthly")],
            "Периодичность выплаты процентов",
        )
        self.capitalize = QCheckBox("Зачислять проценты на вклад после выплаты")
        self.capitalize.setAccessibleName("Капитализация процентов")
        self.boundary = _combo(BOUNDARIES, "Границы дней начисления")
        self.day_count = _combo(DAY_COUNTS, "Дней в году для процентов")
        self.rounding = _combo(ROUNDINGS, "Правило округления процентов")
        self.rounding_stage = _combo(ROUNDING_STAGES, "Момент округления процентов")
        for title, widget in [
            ("Сумма · RUB", self.initial),
            ("Ставка · % годовых", self.rate),
            ("Дата открытия", self.start),
            ("Дата окончания", self.end),
            ("Выплаты", self.frequency),
            ("", self.capitalize),
            ("Начисляемые дни", self.boundary),
            ("Дней в году", self.day_count),
            ("Округление до копейки", self.rounding),
            ("Когда округлять", self.rounding_stage),
        ]:
            form.addRow(title, widget)
        form.addRow(
            _label(
                "Ежемесячно: в число открытия; если его нет — в последний день месяца. "
                "Последняя выплата всегда в дату окончания. Капитализация влияет на базу "
                "со следующего дня. Проверьте правила в своём договоре. "
                "Налоги, комиссии, досрочное закрытие и банковские специальные условия не учтены."
            )
        )
        changes = QWidget()
        change_layout = QVBoxLayout(changes)
        change_layout.addWidget(
            _label(
                "Только сценарные предположения, без записи в книгу. Ставка первой вкладки "
                "действует с открытия; здесь укажите последующие изменения. "
                "Движения одного дня суммируются. До 1000 строк каждого вида."
            )
        )
        policies = QFormLayout()
        self.balance_moment = _combo(BALANCES, "Остаток дня для начисления")
        self.balance_moment.setMinimumHeight(self.balance_moment.sizeHint().height())
        self.minimum = QLineEdit("0,00")
        self.minimum.setMaxLength(128)
        self.minimum.setMinimumHeight(self.minimum.sizeHint().height())
        self.minimum.setAccessibleName("Неснижаемый остаток вклада в рублях")
        self.allow_topups = QCheckBox("Условия разрешают пополнения")
        self.allow_withdrawals = QCheckBox("Условия разрешают снятия")
        policies.addRow("Остаток для процентов", self.balance_moment)
        policies.addRow("Неснижаемый остаток · RUB", self.minimum)
        policies.addRow(self.allow_topups)
        policies.addRow(self.allow_withdrawals)
        change_layout.addLayout(policies)
        self.rate_table, rate_buttons = self._event_table("Изменения ставки", "Новая ставка · %")
        change_layout.addWidget(_label("Изменения ставки"))
        change_layout.addWidget(self.rate_table, 1)
        change_layout.addLayout(rate_buttons)
        self.movement_table, movement_buttons = self._event_table(
            "Сценарные пополнения и снятия", "Сумма · RUB: + пополнение, − снятие"
        )
        change_layout.addWidget(_label("Пополнения и снятия"))
        change_layout.addWidget(self.movement_table, 1)
        change_layout.addLayout(movement_buttons)
        change_scroll = QScrollArea()
        change_scroll.setWidgetResizable(True)
        change_scroll.setWidget(changes)
        self.tabs.addTab(change_scroll, "Изменения")
        self.feedback = _label("")
        self.feedback.setObjectName("feedbackError")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Рассчитать прогноз")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        try:
            start = date(
                self.start.date().year(), self.start.date().month(), self.start.date().day()
            )
            end = date(self.end.date().year(), self.end.date().month(), self.end.date().day())
            if not 1 <= (end - start).days <= 36600:
                raise ValidationError("Срок сценария должен быть от 1 до 36600 дней.")
            amount = Money.parse(self.initial.text(), RUB)
            rate = Decimal(self.rate.text().strip().replace(",", "."))
            self.result_scenario = DepositScenario(
                opened_on=start,
                matures_on=end,
                initial=amount,
                rates=self._rates(start, rate),
                movements=self._movements(),
                payout_dates=monthly_payouts(start, end)
                if self.frequency.currentData() == "monthly"
                else (end,),
                boundary=AccrualBoundary(self.boundary.currentData()),
                balance_moment=BalanceMoment(self.balance_moment.currentData()),
                day_count=DayCount(self.day_count.currentData()),
                rounding=InterestRounding(self.rounding.currentData()),
                rounding_stage=RoundingStage(self.rounding_stage.currentData()),
                capitalize=self.capitalize.isChecked(),
                allow_topups=self.allow_topups.isChecked(),
                allow_withdrawals=self.allow_withdrawals.isChecked(),
                minimum=Money.parse(self.minimum.text(), RUB),
            )
        except (ValidationError, InvalidOperation) as error:
            self.result_scenario = None
            self.feedback.setText(
                str(error)
                if isinstance(error, ValidationError)
                else "Укажите точную годовую ставку, например 12,5."
            )
            self.feedback.show()
            return
        self.feedback.hide()
        self.accept()

    def _event_table(self, name: str, value_title: str) -> tuple[QTableWidget, QHBoxLayout]:
        table = QTableWidget(0, 2)
        table.setAccessibleName(name)
        table.setHorizontalHeaderLabels(["Дата", value_title])
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().show()
        table.setMinimumHeight(180)
        row = QHBoxLayout()
        add = QPushButton("Добавить строку")
        remove = QPushButton("Удалить выбранную строку")
        add.setAccessibleName(name + ": добавить строку")
        remove.setAccessibleName(name + ": удалить выбранную строку")
        remove.setEnabled(False)

        def selection_changed() -> None:
            row = table.currentRow()
            remove.setEnabled(row >= 0)
            remove.setText(f"Удалить строку {row + 1}" if row >= 0 else "Удалить выбранную строку")

        table.itemSelectionChanged.connect(selection_changed)
        add.clicked.connect(lambda: self._add_event(table))
        remove.clicked.connect(lambda: table.removeRow(table.currentRow()))
        row.addWidget(add)
        row.addWidget(remove)
        return table, row

    def _add_event(self, table: QTableWidget) -> None:
        if table.rowCount() >= 1000:
            self.feedback.setText("В форме допустимо до 1000 строк каждого вида.")
            self.feedback.show()
            return
        row = table.rowCount()
        table.insertRow(row)
        day = self.start.date().addDays(1)
        if not day.isValid():
            day = self.start.date()
        editor = QDateEdit(day)
        editor.setDisplayFormat("dd.MM.yyyy")
        editor.setCalendarPopup(True)
        editor.setMinimumDate(QDate(1, 1, 1))
        editor.setMaximumDate(QDate(9999, 12, 31))
        editor.setProperty("recordId", str(uuid4()))
        editor.setAccessibleName(table.accessibleName() + ": дата")
        amount = QLineEdit()
        amount.setMaxLength(128 if table is self.movement_table else 32)
        amount.setAccessibleName(table.accessibleName() + ": значение")
        editor.installEventFilter(self)
        editor.lineEdit().installEventFilter(self)
        amount.installEventFilter(self)
        table.setCellWidget(row, 0, editor)
        table.setCellWidget(row, 1, amount)
        table.setRowHeight(row, max(editor.sizeHint().height(), amount.sizeHint().height()) + 6)
        table.selectRow(row)
        amount.setFocus()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress):
            for table in (self.rate_table, self.movement_table):
                for row in range(table.rowCount()):
                    editor, field = table.cellWidget(row, 0), table.cellWidget(row, 1)
                    if (
                        watched is editor
                        or watched is field
                        or isinstance(editor, QDateEdit)
                        and watched is editor.lineEdit()
                    ):
                        table.selectRow(row)
                        break
        return super().eventFilter(watched, event)

    def _rates(self, start: date, initial_rate: Decimal) -> tuple[DepositRate, ...]:
        result = [DepositRate(str(uuid4()), start, initial_rate)]
        for row in range(self.rate_table.rowCount()):
            editor, field = self.rate_table.cellWidget(row, 0), self.rate_table.cellWidget(row, 1)
            assert isinstance(editor, QDateEdit) and isinstance(field, QLineEdit)
            day = date(editor.date().year(), editor.date().month(), editor.date().day())
            try:
                result.append(
                    DepositRate(
                        editor.property("recordId"),
                        day,
                        Decimal(field.text().strip().replace(",", ".")),
                    )
                )
            except (ValidationError, InvalidOperation):
                raise ValidationError(
                    f"Изменение ставки {row + 1}: проверьте точную годовую ставку."
                ) from None
        return tuple(result)

    def _movements(self) -> tuple[DepositMovement, ...]:
        result = []
        for row in range(self.movement_table.rowCount()):
            editor, field = (
                self.movement_table.cellWidget(row, 0),
                self.movement_table.cellWidget(row, 1),
            )
            assert isinstance(editor, QDateEdit) and isinstance(field, QLineEdit)
            day = date(editor.date().year(), editor.date().month(), editor.date().day())
            try:
                result.append(
                    DepositMovement(
                        editor.property("recordId"),
                        day,
                        Money.parse(field.text().replace("−", "-"), RUB),
                    )
                )
            except ValidationError as error:
                raise ValidationError(f"Движение {row + 1}: {error}") from None
        return tuple(result)


class DepositForecastDialog(QDialog):
    def __init__(self, report: DepositForecast, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.report = report
        self.setWindowTitle("Прогноз вклада · График выплат")
        _size(self, 1000, 660)
        layout = QVBoxLayout(self)
        value = report.scenario
        currency = value.initial.currency
        self.summary = _label(
            f"{value.opened_on:%d.%m.%Y} — {value.matures_on:%d.%m.%Y} · "
            f"Начальная сумма: {format_report_amount(value.initial.minor, currency)} "
            f"{currency.code} · Сценарных движений: {len(value.movements)}\n"
            f"Прогноз процентов: {format_report_amount(report.interest_minor, currency)} "
            f"{currency.code} · "
            f"Остаток вклада: {format_report_amount(report.closing_minor, currency)} "
            f"{currency.code}"
        )
        layout.addWidget(self.summary)
        first_rate = value.rates[0].annual_percent
        rate_text = format(first_rate, "f").replace(".", ",") if first_rate else "0"
        rate_caption = (
            f"Ставка: {rate_text} % годовых"
            if len(value.rates) == 1
            else f"Версий ставки: {len(value.rates)}"
        )
        self.assumptions = _label(
            " · ".join(
                [
                    rate_caption,
                    dict((v, k) for k, v in BALANCES)[value.balance_moment],
                    dict((v, k) for k, v in BOUNDARIES)[value.boundary],
                    dict((v, k) for k, v in DAY_COUNTS)[value.day_count],
                    dict((v, k) for k, v in ROUNDINGS)[value.rounding],
                    dict((v, k) for k, v in ROUNDING_STAGES)[value.rounding_stage],
                    "С капитализацией" if value.capitalize else "Выплата отдельно от вклада",
                ]
            )
        )
        layout.addWidget(self.assumptions)
        payout_dates = set(value.payout_dates)
        self.payouts = tuple(v for v in report.days if v.day in payout_dates)
        self.table = QTableView()
        self.table.setAccessibleName("Прогнозные выплаты процентов")
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.model = replace_table(
            self.table,
            ["Дата выплаты", "Проценты · " + currency.code, "На вклад", "Остаток вклада"],
            [
                [
                    f"{v.day:%d.%m.%Y}",
                    format_report_amount(v.payout_minor, currency),
                    format_report_amount(v.capitalized_minor, currency),
                    format_report_amount(v.closing_minor, currency),
                ]
                for v in self.payouts
            ],
            right_columns=(1, 2, 3),
        )
        tabs = QTabWidget()
        tabs.addTab(self.table, "Выплаты")
        conditions = QWidget()
        conditions_layout = QVBoxLayout(conditions)
        conditions_layout.addWidget(
            _label(
                "Неснижаемый остаток: "
                + format_report_amount(value.minimum.minor, currency)
                + " "
                + currency.code
                + ". Это введённые условия сценария."
            )
        )
        self.rate_table = self._conditions_table(
            ["Действует с", "Ставка · % годовых"],
            [
                [
                    f"{v.effective_on:%d.%m.%Y}",
                    format(v.annual_percent, "f").replace(".", ",") if v.annual_percent else "0",
                ]
                for v in value.rates
            ],
            "Версии ставок сценария",
        )
        self.movement_table = self._conditions_table(
            ["Дата", "Сценарное движение · " + currency.code],
            [
                [f"{v.occurred_on:%d.%m.%Y}", format_report_amount(v.amount.minor, currency)]
                for v in value.movements
            ],
            "Движения сценария",
        )
        conditions_layout.addWidget(self.rate_table, 1)
        conditions_layout.addWidget(self.movement_table, 1)
        tabs.addTab(conditions, "Введённые условия")
        self.tabs = tabs
        layout.addWidget(tabs, 1)
        layout.addWidget(
            _label(
                "Это сценарий, не фактическая банковская выплата. Отдельно выплаченные "
                "проценты не входят в остаток вклада. Средства в книге не меняются. "
                "Условия и расчёт не сохраняются после закрытия."
            )
        )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _conditions_table(headers: list[str], rows: list[list[str]], name: str) -> QTableView:
        table = QTableView()
        table.setAccessibleName(name)
        table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        replace_table(table, headers, rows, right_columns=(1,))
        return table
