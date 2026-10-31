"""Программы, отдельный журнал и отчёт бонусов; денежные операции не меняются."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from PySide6.QtCore import QDate, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from personal_finance.application.vault import VaultSession
from personal_finance.domain.bonuses import (
    KIND_LABELS,
    BonusCategoryTotal,
    BonusDraft,
    BonusEntry,
    BonusKind,
    BonusProgram,
    format_units,
    parse_units,
    program_name,
)
from personal_finance.domain.errors import FinanceError, ValidationError
from personal_finance.domain.models import Category, CategoryKind
from personal_finance.presentation.ocr_preview import OptionalMaskedEdit, plain
from personal_finance.presentation.tables import replace_table

if TYPE_CHECKING:
    from personal_finance.presentation.main_window import MainWindow


def day_text(day: date) -> str:
    return f"{day.day:02d}.{day.month:02d}.{day.year:04d}"


@dataclass(frozen=True, slots=True)
class BonusSeed:
    units_minor: int
    occurred_on: str
    source_key: str
    bank_category: str = ""
    category_id: str | None = None
    note: str = ""


class BonusNameForm(QDialog):
    def __init__(self, name: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._last_value: object | None = None
        self._command: str = ""
        self.setWindowTitle("Программа лояльности")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain("Название программы. Её бонусы учитываются отдельно от денег и других программ.")
        )
        self.name = QLineEdit(name)
        self.name.setMaxLength(120)
        layout.addWidget(self.name)
        self.feedback = plain("")
        layout.addWidget(self.feedback)
        self.value: str | None = None
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        try:
            self.value = program_name(self.name.text())
        except ValidationError as error:
            self.feedback.setText(str(error))
            return
        self.accept()


class BonusEntryForm(QDialog):
    def __init__(
        self,
        programs: list[BonusProgram],
        categories: list[Category],
        parent: QWidget,
        current: BonusEntry | None = None,
        seed: BonusSeed | None = None,
    ) -> None:
        super().__init__(parent)
        self._last_value: object | None = None
        self._command: str = ""
        self.setWindowTitle("Бонусная запись")
        self.setMinimumWidth(520)
        self.current = current
        self.seed = seed
        self.value: BonusDraft | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain(
                "Бонусы не изменяют рублёвые остатки. "
                "Записывайте фактическое начисление или списание."
            )
        )
        form = QFormLayout()
        layout.addLayout(form)
        self.program = QComboBox()
        self.program.addItem("Выберите программу", None)
        for p in programs:
            if not p.is_deleted or (current and p.id == current.draft.program_id):
                self.program.addItem(p.name + (" (архив)" if p.is_deleted else ""), p.id)
        if self.program.count() == 2:
            self.program.setCurrentIndex(1)
        self.kind = QComboBox()
        for kind, label in KIND_LABELS.items():
            self.kind.addItem(label, kind)
        self.kind.setCurrentIndex(self.kind.findData(BonusKind.ACCRUAL))
        self.occurred = OptionalMaskedEdit("99.99.9999;_")
        self.occurred.setText(day_text(date.today()))
        self.units = QLineEdit()
        self.units.setPlaceholderText("Например, 1,25")
        self.category = QComboBox()
        self.category.addItem("Без категории", None)
        for c in categories:
            if c.kind is CategoryKind.EXPENSE:
                self.category.addItem(c.name, c.id)
        self.note = QLineEdit()
        self.note.setMaxLength(500)
        self.bank_category = plain("")
        for title, widget in [
            ("Программа", self.program),
            ("Вид", self.kind),
            ("Дата", self.occurred),
            ("Бонусы (знак — только для корректировки)", self.units),
            ("Категория расходов", self.category),
            ("Примечание", self.note),
            ("Подсказка банка", self.bank_category),
        ]:
            form.addRow(title, widget)
        if current is not None:
            d = current.draft
            self.program.setCurrentIndex(self.program.findData(d.program_id))
            self.kind.setCurrentIndex(self.kind.findData(d.kind))
            self.occurred.setText(day_text(d.occurred_on))
            self.units.setText(
                format_units(
                    d.units_minor if d.kind is BonusKind.ADJUSTMENT else abs(d.units_minor)
                )
            )
            self.category.setCurrentIndex(self.category.findData(d.category_id))
            self.note.setText(d.note)
            self.bank_category.setText(d.bank_category)
        elif seed is not None:
            self.kind.setCurrentIndex(
                self.kind.findData(
                    BonusKind.ACCRUAL if seed.units_minor > 0 else BonusKind.ADJUSTMENT
                )
            )
            self.occurred.setText(seed.occurred_on)
            self.units.setText(format_units(seed.units_minor))
            self.category.setCurrentIndex(self.category.findData(seed.category_id))
            self.note.setText(seed.note)
            self.bank_category.setText(seed.bank_category)
        self.feedback = plain("")
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить бонусы")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        try:
            amount = parse_units(self.units.text())
            kind = BonusKind(self.kind.currentData())
            if kind is not BonusKind.ADJUSTMENT and amount < 0:
                raise ValidationError("Укажите количество без знака; движение задаёт вид записи.")
            if kind in (BonusKind.SPENDING, BonusKind.EXPIRY):
                amount = -amount
            occurred = datetime.strptime(self.occurred.text(), "%d.%m.%Y").date()
            source = (
                self.current.draft.source_key
                if self.current
                else self.seed.source_key
                if self.seed
                else None
            )
            operation = self.current.draft.operation_id if self.current else None
            self.value = BonusDraft(
                self.program.currentData(),
                occurred,
                kind,
                amount,
                self.category.currentData(),
                operation,
                self.note.text(),
                source,
                self.bank_category.text(),
            )
        except (ValidationError, ValueError) as error:
            self.feedback.setText(
                str(error)
                if isinstance(error, ValidationError)
                else "Укажите полную допустимую дату."
            )
            return
        self.accept()


class BonusesDialog(QDialog):
    def __init__(
        self,
        host: MainWindow,
        session: VaultSession,
        parent: QWidget | None = None,
        seeds: tuple[BonusSeed, ...] = (),
    ) -> None:
        super().__init__(parent or host)
        self._last_seeds: tuple[BonusDraft, ...] | None = None
        self._seed_command: str = ""
        self.host = host
        self.session = session
        if session.bonuses is None:
            raise ValidationError("В этой книге учёт бонусов недоступен.")
        self.service = session.bonuses
        self.seeds = seeds
        self._busy = False
        self._closing = False
        self._seed_ready = False
        self._programs: list[BonusProgram] = []
        self._entries: list[BonusEntry] = []
        self._categories: list[Category] = []
        self._balances: dict[str, int] = {}
        self._report: tuple[BonusCategoryTotal, ...] = ()
        self.visible_programs: list[BonusProgram] = []
        self.visible_entries: list[BonusEntry] = []
        self._seed_widgets: list[
            tuple[QCheckBox, OptionalMaskedEdit, QLineEdit, QComboBox, BonusSeed]
        ] = []
        self.setWindowTitle("Бонусы: программы, журнал и категории")
        self.resize(1080, 720)
        self.setMinimumSize(800, 560)
        outer = QVBoxLayout(self)
        self.contents = QWidget()
        outer.addWidget(self.contents)
        layout = QVBoxLayout(self.contents)
        layout.addWidget(
            plain("Бонусы учитываются отдельно от денег. Остатки разных программ не суммируются.")
        )
        filters = QHBoxLayout()
        self.program_filter = QComboBox()
        self.program_filter.setMinimumWidth(260)
        self.program_filter.addItem("Все программы", None)
        filters.addWidget(self.program_filter)
        self.start = QDateEdit(QDate.currentDate().addMonths(-1))
        self.end = QDateEdit(QDate.currentDate())
        for widget in (self.start, self.end):
            widget.setMinimumDate(QDate(1, 1, 1))
            widget.setMaximumDate(QDate(9999, 12, 31))
            widget.setDisplayFormat("dd.MM.yyyy")
            widget.setCalendarPopup(True)
        filters.addWidget(QLabel("Период отчёта"))
        filters.addWidget(self.start)
        filters.addWidget(self.end)
        layout.addLayout(filters)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.program_page = QWidget()
        p_layout = QVBoxLayout(self.program_page)
        self.show_archived = QCheckBox("Показать архивные программы")
        p_layout.addWidget(self.show_archived)
        self.program_table = QTableView()
        p_layout.addWidget(self.program_table)
        self.tabs.addTab(self.program_page, "Программы")
        self.entry_page = QWidget()
        e_layout = QVBoxLayout(self.entry_page)
        self.show_deleted = QCheckBox("Показать отменённые записи")
        e_layout.addWidget(self.show_deleted)
        self.entry_table = QTableView()
        e_layout.addWidget(self.entry_table)
        self.tabs.addTab(self.entry_page, "Журнал")
        self.report_page = QWidget()
        r_layout = QVBoxLayout(self.report_page)
        r_layout.addWidget(
            plain(
                "Начисления и списания по категориям за выбранный период, отдельно для каждой"
                " программы. Начальные остатки и корректировки сюда не входят."
            )
        )
        self.report_table = QTableView()
        r_layout.addWidget(self.report_table)
        self.tabs.addTab(self.report_page, "По категориям")
        for table in (self.program_table, self.entry_table, self.report_table):
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setSortingEnabled(False)
            table.setAlternatingRowColors(True)
            table.verticalHeader().hide()
        buttons = QHBoxLayout()
        self.add_program = QPushButton("Новая программа")
        self.add_entry = QPushButton("Добавить бонусы")
        self.edit = QPushButton("Изменить")
        self.archive = QPushButton("В архив / отменить")
        self.history_button = QPushButton("История")
        for button in (
            self.add_program,
            self.add_entry,
            self.edit,
            self.archive,
            self.history_button,
        ):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.status = plain("")
        outer.addWidget(self.status)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.close_button = close.button(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        close.rejected.connect(self.reject)
        outer.addWidget(close)
        self.add_program.clicked.connect(self.create_program)
        self.add_entry.clicked.connect(self.create_entry)
        self.edit.clicked.connect(self.edit_selected)
        self.archive.clicked.connect(self.archive_selected)
        self.history_button.clicked.connect(self.show_history)
        self.program_filter.currentIndexChanged.connect(self._render)
        self.show_archived.toggled.connect(self._render)
        self.show_deleted.toggled.connect(self._render)
        self.tabs.currentChanged.connect(self._selection)
        self.start.dateChanged.connect(self.reload)
        self.end.dateChanged.connect(self.reload)
        self.seed_page: QWidget | None = None
        if seeds:
            self._prepare_seeds()
        self._render()
        QTimer.singleShot(0, self.reload)

    def _current(self) -> bool:
        return self.host.session is self.session

    def _job(
        self,
        action: Callable[[], Any],
        done: Callable[[Any], None],
        message: str,
        failed: Callable[[str], None] | None = None,
    ) -> None:
        if self._busy or self.host._busy or (not self._current()):
            return
        self._busy = True
        self.contents.setEnabled(False)
        self.close_button.setEnabled(False)
        self.status.setText(message)

        def run() -> tuple[bool, Any]:
            try:
                with self.session.mutex:
                    self.session.ensure_open()
                    return (True, action())
            except FinanceError as error:
                return (False, str(error))
            except Exception:
                return (
                    False,
                    "Не удалось завершить действие. Проверьте данные и повторите попытку.",
                )

        def applied(result: Any) -> None:
            self._busy = False
            self.contents.setEnabled(True)
            self.close_button.setEnabled(True)
            if not self._current():
                self.reject()
                return
            if result[0]:
                done(result[1])
            elif failed is not None:
                failed(result[1])
            else:
                self.status.setText(result[1])
                QMessageBox.warning(self, "Действие не выполнено", result[1])

        self.host._job(run, applied, message, defer_success=True)

    def reload(self, *unused: object) -> None:
        if self._closing or not self._current():
            return
        start, end = (date(*self.start.date().getDate()), date(*self.end.date().getDate()))
        self._job(
            lambda: (
                self.service.programs(include_archived=True),
                self.service.entries(include_deleted=True),
                self.session.catalog.categories(),
                self.service.balances(),
                self.service.category_report(start, end),
            ),
            self._loaded,
            "Загрузка бонусного журнала…",
        )

    def _loaded(self, value: Any) -> None:
        self._programs, self._entries, self._categories, self._balances, self._report = value
        chosen = self.program_filter.currentData()
        with QSignalBlocker(self.program_filter):
            self.program_filter.clear()
            self.program_filter.addItem("Все программы", None)
            for p in self._programs:
                self.program_filter.addItem(p.name + (" (архив)" if p.is_deleted else ""), p.id)
            self.program_filter.setCurrentIndex(max(0, self.program_filter.findData(chosen)))
        self._render()
        self.status.setText("Бонусный журнал загружен · рублёвые остатки не изменяются")
        if self.seeds:
            self._fill_seeds()

    def _render(self, *unused: object) -> None:
        chosen = self.program_filter.currentData()
        names = {p.id: p.name for p in self._programs}
        categories = {c.id: c.name for c in self._categories}
        self.visible_programs = [
            p
            for p in self._programs
            if (self.show_archived.isChecked() or not p.is_deleted)
            and (chosen is None or p.id == chosen)
        ]
        replace_table(
            self.program_table,
            ["Программа", "Остаток бонусов", "Состояние"],
            [
                [
                    p.name,
                    format_units(self._balances.get(p.id, 0)),
                    "В архиве" if p.is_deleted else "Активна",
                ]
                for p in self.visible_programs
            ],
        )
        self.visible_entries = [
            e
            for e in self._entries
            if (self.show_deleted.isChecked() or not e.is_deleted)
            and (chosen is None or e.draft.program_id == chosen)
        ]
        replace_table(
            self.entry_table,
            ["Дата", "Программа", "Вид", "Бонусы", "Категория", "Примечание", "Состояние"],
            [
                [
                    day_text(e.draft.occurred_on),
                    names.get(e.draft.program_id, ""),
                    KIND_LABELS[e.draft.kind],
                    format_units(e.draft.units_minor),
                    categories.get(e.draft.category_id or "", "Без категории"),
                    e.draft.note,
                    "Отменена" if e.is_deleted else "Учтена",
                ]
                for e in self.visible_entries
            ],
        )
        replace_table(
            self.report_table,
            ["Программа", "Категория", "Начислено бонусов", "Списано бонусов"],
            [
                [
                    names.get(r.program_id, ""),
                    categories.get(r.category_id or "", "Без категории"),
                    format_units(r.accrued_minor),
                    format_units(r.spent_minor),
                ]
                for r in self._report
                if chosen is None or r.program_id == chosen
            ],
        )
        for table in (self.program_table, self.entry_table):
            table.selectionModel().selectionChanged.connect(self._selection)
        self._selection()

    def _selected(self) -> BonusProgram | BonusEntry | None:
        records: Sequence[BonusProgram | BonusEntry]
        if self.tabs.currentWidget() is self.program_page:
            table, records = (self.program_table, self.visible_programs)
        elif self.tabs.currentWidget() is self.entry_page:
            table, records = (self.entry_table, self.visible_entries)
        else:
            return None
        rows = table.selectionModel().selectedRows()
        return (
            records[rows[0].row()] if len(rows) == 1 and 0 <= rows[0].row() < len(records) else None
        )

    def _selection(self, *unused: object) -> None:
        record = self._selected()
        for button in (self.edit, self.archive, self.history_button):
            button.setEnabled(record is not None)
        self.archive.setText(
            "Вернуть"
            if record is not None and record.is_deleted
            else "В архив"
            if isinstance(record, BonusProgram)
            else "Отменить запись"
        )
        self.add_entry.setEnabled(any(not p.is_deleted for p in self._programs))

    def _save_form(
        self, form: BonusNameForm | BonusEntryForm, save: Callable[[Any, str], object]
    ) -> None:
        if form.exec() != QDialog.DialogCode.Accepted or form.value is None:
            form.deleteLater()
            return
        value = form.value
        if getattr(form, "_last_value", None) != value:
            form._last_value = value
            form._command = str(uuid4())

        def saved(_: Any) -> None:
            form.deleteLater()
            self.reload()

        def failed(message: str) -> None:
            form.feedback.setText(message)
            self._save_form(form, save)

        self._job(lambda: save(value, form._command), saved, "Сохранение бонусной записи…", failed)

    def create_program(self) -> None:
        form = BonusNameForm("", self)
        self._save_form(
            form, lambda value, command: self.service.save_program(value, command_id=command)
        )

    def create_entry(self) -> None:
        form = BonusEntryForm(self._programs, self._categories, self)
        selected = self.program_filter.currentData()
        if selected is not None:
            form.program.setCurrentIndex(form.program.findData(selected))
        self._save_form(
            form, lambda value, command: self.service.save_entry(value, command_id=command)
        )

    def edit_selected(self) -> None:
        record = self._selected()
        if isinstance(record, BonusProgram):
            self._save_form(
                BonusNameForm(record.name, self),
                lambda value, command: self.service.save_program(
                    value,
                    identifier=record.id,
                    expected_revision_id=record.revision_id,
                    is_deleted=record.is_deleted,
                    command_id=command,
                ),
            )
        elif isinstance(record, BonusEntry):
            self._save_form(
                BonusEntryForm(self._programs, self._categories, self, current=record),
                lambda value, command: self.service.save_entry(
                    value,
                    identifier=record.id,
                    expected_revision_id=record.revision_id,
                    is_deleted=record.is_deleted,
                    command_id=command,
                ),
            )

    def archive_selected(self) -> None:
        record = self._selected()
        if record is None:
            return
        command = str(uuid4())

        def run_archive() -> object:
            if isinstance(record, BonusProgram):
                return self.service.save_program(
                    record.name,
                    identifier=record.id,
                    expected_revision_id=record.revision_id,
                    is_deleted=not record.is_deleted,
                    command_id=command,
                )
            return self.service.save_entry(
                record.draft,
                identifier=record.id,
                expected_revision_id=record.revision_id,
                is_deleted=not record.is_deleted,
                command_id=command,
            )

        self._job(run_archive, lambda _: self.reload(), "Сохранение состояния бонусной записи…")

    def show_history(self) -> None:
        record = self._selected()
        if record is None:
            return
        self._job(
            lambda: self.service.history(record.id), self._history, "Загрузка неизменяемой истории…"
        )

    def _history(self, records: Any) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("История бонусной записи")
        dialog.resize(850, 520)
        layout = QVBoxLayout(dialog)
        table = QTableView()
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        rows = []
        for record in records:
            if isinstance(record, BonusProgram):
                rows.append(
                    [
                        record.created_at,
                        record.name,
                        "",
                        "",
                        "В архиве" if record.is_deleted else "Активна",
                    ]
                )
            else:
                rows.append(
                    [
                        record.created_at,
                        day_text(record.draft.occurred_on),
                        KIND_LABELS[record.draft.kind],
                        format_units(record.draft.units_minor),
                        "Отменена" if record.is_deleted else "Учтена",
                    ]
                )
        replace_table(
            table, ["Сохранено · UTC", "Название / дата", "Вид", "Бонусы", "Состояние"], rows
        )
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _prepare_seeds(self) -> None:
        self.seed_page = QWidget()
        layout = QVBoxLayout(self.seed_page)
        layout.addWidget(
            plain(
                "Подписи бонусов на снимке не являются денежными доходами. Отметьте только "
                "фактические начисления/списания. Этот пакет сохраняется отдельно от денежных"
                " операций."
            )
        )
        self.seed_program = QComboBox()
        self.seed_program.addItem("Выберите программу для выбранных бонусов", None)
        layout.addWidget(self.seed_program)
        self.seed_table = QTableWidget(0, 5)
        self.seed_table.setHorizontalHeaderLabels(
            ["Учесть", "Дата", "Бонусы", "Категория расходов", "Категория банка"]
        )
        self.seed_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(self.seed_table, 1)
        tools = QHBoxLayout()
        select = QPushButton("Выбрать все доступные")
        select.clicked.connect(self._select_all_seeds)
        save = QPushButton("Записать выбранные бонусы")
        save.clicked.connect(self.save_seeds)
        tools.addWidget(select)
        tools.addWidget(save)
        layout.addLayout(tools)
        self.seed_feedback = plain("")
        layout.addWidget(self.seed_feedback)
        self.tabs.addTab(self.seed_page, "Со снимка")
        self.tabs.setCurrentWidget(self.seed_page)

    def _fill_seeds(self) -> None:
        selected = self.seed_program.currentData()
        with QSignalBlocker(self.seed_program):
            self.seed_program.clear()
            self.seed_program.addItem("Выберите программу", None)
            for p in self._programs:
                if not p.is_deleted:
                    self.seed_program.addItem(p.name, p.id)
            index = self.seed_program.findData(selected)
            self.seed_program.setCurrentIndex(
                index if index >= 0 else 1 if self.seed_program.count() == 2 else 0
            )
        recorded = {e.draft.source_key for e in self._entries if e.draft.source_key is not None}
        if not self._seed_ready:
            self.seed_table.setRowCount(len(self.seeds))
            for index, seed in enumerate(self.seeds):
                include = QCheckBox()
                occurred = OptionalMaskedEdit("99.99.9999;_")
                occurred.setText(seed.occurred_on)
                units = QLineEdit(format_units(seed.units_minor))
                category = QComboBox()
                category.addItem("Без категории", None)
                for c in self._categories:
                    if c.kind is CategoryKind.EXPENSE:
                        category.addItem(c.name, c.id)
                category.setCurrentIndex(max(0, category.findData(seed.category_id)))
                for column, widget in enumerate((include, occurred, units, category)):
                    self.seed_table.setCellWidget(index, column, widget)
                bank_hint = QTableWidgetItem(seed.bank_category)
                bank_hint.setFlags(bank_hint.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.seed_table.setItem(index, 4, bank_hint)
                self._seed_widgets.append((include, occurred, units, category, seed))
            self._seed_ready = True
        for include, _occurred, _units, _category, seed in self._seed_widgets:
            if seed.source_key in recorded:
                include.setChecked(False)
                include.setEnabled(False)
                include.setToolTip("Уже учтено, в том числе в отменённой записи")

    def save_seeds(self) -> None:
        try:
            drafts = []
            for include, occurred, units, category, seed in self._seed_widgets:
                if not include.isChecked() or not include.isEnabled():
                    continue
                amount = parse_units(units.text())
                drafts.append(
                    BonusDraft(
                        self.seed_program.currentData(),
                        datetime.strptime(occurred.text(), "%d.%m.%Y").date(),
                        BonusKind.ACCRUAL if amount > 0 else BonusKind.ADJUSTMENT,
                        amount,
                        category.currentData(),
                        None,
                        seed.note,
                        seed.source_key,
                        seed.bank_category,
                    )
                )
            if not drafts:
                raise ValidationError("Выберите хотя бы одну ещё не учтённую бонусную подпись.")
            values = tuple(drafts)
        except (ValueError, ValidationError) as error:
            self.seed_feedback.setText(
                str(error)
                if isinstance(error, ValidationError)
                else "Укажите полные допустимые даты."
            )
            return
        if getattr(self, "_last_seeds", None) != values:
            self._last_seeds = values
            self._seed_command = str(uuid4())
        self._job(
            lambda: self.service.save_entries(values, command_id=self._seed_command),
            lambda _: self.reload(),
            "Сохранение выбранных бонусов одним пакетом…",
            self.seed_feedback.setText,
        )

    def reject(self) -> None:
        if not self._busy:
            self._closing = True
            super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._busy:
            event.ignore()
        else:
            self._closing = True
            super().closeEvent(event)

    def _select_all_seeds(self) -> None:
        for include, _, _, _, _ in self._seed_widgets:
            if include.isEnabled():
                include.setChecked(True)
