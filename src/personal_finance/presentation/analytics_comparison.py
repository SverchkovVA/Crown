"""Сравнение двух периодов и переход к источникам того же снимка."""

from datetime import date
from html import escape

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.analytics import IncomeExpenseReport, ReportPeriod
from personal_finance.domain.analytics_comparison import IncomeExpenseComparison
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import Account, Category, Currency, Institution
from personal_finance.presentation.analytics import (
    IncomeExpenseDialog,
    ReportPeriodDialog,
    _label,
    format_report_amount,
    scope_captions,
)
from personal_finance.presentation.ledger_dialogs import category_title


def _period(start: QDateEdit, end: QDateEdit) -> ReportPeriod:
    a, b = start.date(), end.date()
    return ReportPeriod(date(a.year(), a.month(), a.day()), date(b.year(), b.month(), b.day()))


def period_note(before: ReportPeriod, after: ReportPeriod) -> str:
    first, second = (before.end - before.start).days + 1, (after.end - after.start).days + 1
    return (
        f"Дней в периодах: {first} и {second}. "
        "Изменение = второй − первый; суммы не пересчитываются на день."
    )


class ComparisonPeriodDialog(ReportPeriodDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        accounts: list[Account] | None = None,
        categories: list[Category] | None = None,
        institutions: list[Institution] | None = None,
    ) -> None:
        super().__init__(
            parent, accounts=accounts, categories=categories, institutions=institutions
        )
        self.setWindowTitle("Доходы и расходы · Сравнение периодов")
        self.resize(760, 560)
        self.description.setText(
            "Выберите два периода и общий отбор. Валюты сравниваются отдельно. "
            "Одинаковые и пересекающиеся периоды допустимы."
        )
        self.result_second_period: ReportPeriod | None = None
        self.second_start, self.second_end = (
            QDateEdit(self.start.date()),
            QDateEdit(self.end.date()),
        )
        self.start.setDate(self.start.date().addMonths(-1))
        self.end.setDate(self.end.date().addMonths(-1))
        for editor, title in (
            (self.start, "Начало первого периода"),
            (self.end, "Конец первого периода"),
            (self.second_start, "Начало второго периода"),
            (self.second_end, "Конец второго периода"),
        ):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd.MM.yyyy")
            editor.setMinimumDate(self.start.minimumDate())
            editor.setMaximumDate(self.start.maximumDate())
            editor.setAccessibleName(title)
            editor.dateChanged.connect(self._update_note)
        for editor, title in ((self.start, "Первый период: с"), (self.end, "По включительно")):
            label = self.form.labelForField(editor)
            if isinstance(label, QLabel):
                label.setText(title)
        self.form.insertRow(2, "Второй период: с", self.second_start)
        self.form.insertRow(3, "По включительно", self.second_end)
        self.note = _label("")
        self.form.insertRow(4, self.note)
        buttons = self.findChild(QDialogButtonBox)
        if buttons is not None:
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Сравнить")
        self._update_note()

    def _update_note(self) -> None:
        try:
            self.note.setText(
                period_note(
                    _period(self.start, self.end), _period(self.second_start, self.second_end)
                )
            )
        except ValidationError:
            self.note.setText("Проверьте порядок дат обоих периодов.")

    def _accept(self) -> None:
        try:
            try:
                first = _period(self.start, self.end)
            except ValidationError as error:
                raise ValidationError("Первый период: " + str(error)) from error
            try:
                second = _period(self.second_start, self.second_end)
            except ValidationError as error:
                raise ValidationError("Второй период: " + str(error)) from error
            scope = self._scope()
        except ValidationError as error:
            self.result_period = self.result_second_period = None
            self.result_scope = None
            self.feedback.setText(str(error))
            self.feedback.show()
            return
        self.result_period, self.result_second_period, self.result_scope = first, second, scope
        self.accept()


def _delta(minor: int, currency: Currency) -> str:
    return ("+" if minor > 0 else "") + format_report_amount(minor, currency)


class IncomeExpenseComparisonDialog(QDialog):
    def __init__(
        self,
        comparison: IncomeExpenseComparison,
        categories: list[Category],
        parent: QWidget | None = None,
        *,
        accounts: list[Account] | None = None,
    ) -> None:
        super().__init__(parent)
        self.comparison = comparison
        self._categories = list(categories)
        self._accounts = list(accounts or [])
        self._names = {c.id: category_title(c, categories) for c in categories}
        self.setWindowTitle("Доходы и расходы · Сравнение периодов")
        self.resize(1120, 700)
        self.setMinimumSize(800, 500)
        if (screen := self.screen()) is not None:
            area = screen.availableGeometry()
            self.resize(
                min(1120, max(800, area.width() - 50)), min(700, max(500, area.height() - 70))
            )
        layout = QVBoxLayout(self)
        before, after = comparison.before.period, comparison.after.period
        layout.addWidget(
            _label(
                f"Первый: {before.start:%d.%m.%Y} — {before.end:%d.%m.%Y} · "
                f"Второй: {after.start:%d.%m.%Y} — {after.end:%d.%m.%Y}"
            )
        )
        layout.addWidget(_label(period_note(before, after)))
        brief, full = scope_captions(comparison.before, categories, self._accounts)
        self.scope_context = _label(brief)
        self.scope_context.setToolTip("<qt>" + escape(full).replace("\n", "<br>") + "</qt>")
        self.scope_context.setAccessibleName("Общие фильтры сравнения")
        layout.addWidget(self.scope_context)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        totals = []
        self._total_targets: list[tuple[str, str | None]] = []
        for row in comparison.totals:
            currency = row.after.currency
            for title, first, last, delta in (
                ("Доходы", row.before.income_minor, row.after.income_minor, row.income_delta),
                (
                    "Расходы с возвратами",
                    row.before.expense_minor,
                    row.after.expense_minor,
                    row.expense_delta,
                ),
                (
                    "Разница",
                    row.before.difference_minor,
                    row.after.difference_minor,
                    row.difference_delta,
                ),
            ):
                totals.append(
                    [
                        currency.code,
                        title,
                        format_report_amount(first, currency),
                        format_report_amount(last, currency),
                        _delta(delta, currency),
                    ]
                )
                self._total_targets.append((currency.code, None))
        self.total_table = IncomeExpenseDialog._view(
            ["Валюта", "Показатель", "Первый период", "Второй период", "Изменение"],
            totals,
            (2, 3, 4),
        )
        self.total_table.setAccessibleName("Итоги сравнения периодов")
        self.tabs.addTab(self.total_table, "Итоги")
        category_rows = []
        self._category_targets: list[tuple[str, str | None]] = []
        for item in sorted(
            comparison.categories,
            key=lambda v: (
                v.row.after.currency.code,
                self._names.get(v.category_id, "").casefold(),
            ),
        ):
            row = item.row
            currency = row.after.currency
            category_rows.append(
                [
                    self._names.get(item.category_id, "Недоступная категория"),
                    currency.code,
                    format_report_amount(row.before.income_minor, currency),
                    format_report_amount(row.after.income_minor, currency),
                    _delta(row.income_delta, currency),
                    format_report_amount(row.before.expense_minor, currency),
                    format_report_amount(row.after.expense_minor, currency),
                    _delta(row.expense_delta, currency),
                ]
            )
            self._category_targets.append((currency.code, item.category_id))
        self.category_table = IncomeExpenseDialog._view(
            [
                "Категория",
                "Валюта",
                "Доход · 1",
                "Доход · 2",
                "Изменение дохода",
                "Расход · 1",
                "Расход · 2",
                "Изменение расхода",
            ],
            category_rows,
            (2, 3, 4, 5, 6, 7),
        )
        self.category_table.setAccessibleName("Категории сравнения периодов")
        self.tabs.addTab(self.category_table, "Категории")
        self.notice = _label(
            "Операций в выбранных периодах и отборе нет."
            if not comparison.totals
            else "Операции периода: в итогах — выбранная валюта, "
            "в категориях — выбранная категория. "
            "Используется тот же снимок; после изменения книги постройте сравнение заново."
        )
        layout.addWidget(self.notice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        self.before_button = QPushButton("Операции первого периода")
        self.after_button = QPushButton("Операции второго периода")
        for button in (self.before_button, self.after_button):
            buttons.addButton(button, QDialogButtonBox.ButtonRole.ActionRole)
        self.before_button.clicked.connect(lambda: self.open_period(False))
        self.after_button.clicked.connect(lambda: self.open_period(True))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.tabs.currentChanged.connect(self._update_buttons)
        for table in (self.total_table, self.category_table):
            table.selectionModel().currentRowChanged.connect(self._update_buttons)
            table.doubleClicked.connect(self._open_available)
        self._update_buttons()

    def _target(self) -> tuple[str, str | None] | None:
        table = self.tabs.currentWidget()
        if table is self.total_table:
            targets = self._total_targets
            index = self.total_table.currentIndex()
        else:
            targets = self._category_targets
            index = self.category_table.currentIndex()
        return targets[index.row()] if index.isValid() and 0 <= index.row() < len(targets) else None

    def _report(self, second: bool) -> IncomeExpenseReport:
        return self.comparison.after if second else self.comparison.before

    def _available(self, second: bool) -> bool:
        target = self._target()
        if target is None:
            return False
        code, category = target
        report = self._report(second)
        return any(v.currency.code == code for v in report.totals) and (
            category is None
            or any(
                v.category_id == category and v.totals.currency.code == code
                for v in report.categories
            )
        )

    def _update_buttons(self) -> None:
        for second, button in ((False, self.before_button), (True, self.after_button)):
            available = self._available(second)
            button.setEnabled(available)
            button.setToolTip(
                "Открыть исходные операции этого периода"
                if available
                else "В этом периоде нет операций выбранной строки"
            )

    def _open_available(self, index: QModelIndex) -> None:
        self.open_period(self._available(True))

    def open_period(self, second: bool) -> None:
        if not self._available(second):
            self.notice.setText("В выбранном периоде нет операций этой строки.")
            return
        target = self._target()
        if target is None:
            return
        dialog = IncomeExpenseDialog(
            self._report(second), self._categories, self, accounts=self._accounts
        )
        try:
            dialog._select_sources(*target)
            dialog.exec()
        finally:
            dialog.charts.dispose()
            dialog.deleteLater()
