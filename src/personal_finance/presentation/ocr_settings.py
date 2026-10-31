"""Форма параметров OCR; сохранение выполняет вызывающий сценарий."""

from datetime import date

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.errors import FinanceError
from personal_finance.domain.models import Account
from personal_finance.domain.ocr_settings import OcrPreferences, TransferParty
from personal_finance.presentation.ocr_preview import plain


class OcrSettingsDialog(QDialog):
    def __init__(
        self,
        preferences: OcrPreferences,
        accounts: list[Account],
        parent: QWidget | None = None,
        *,
        allow_parties: bool,
    ) -> None:
        super().__init__(parent)
        self.result_preferences: OcrPreferences | None = None
        self._preferences = preferences
        self._accounts = [a for a in accounts if a.currency == "RUB"]
        self._allow_parties = allow_parties
        self.setWindowTitle("Настройки распознавания")
        self.resize(800, 700)
        self.setMinimumSize(640, 460)
        layout = QVBoxLayout(self)
        body = QWidget()
        column = QVBoxLayout(body)
        column.addWidget(
            plain("Эти настройки предлагают значения и определяют порядок проверки OCR.")
        )
        form = QFormLayout()
        self.currency = QComboBox()
        self.currency.addItem("RUB — российский рубль", "RUB")
        self.currency.setToolTip(
            "Для финансового подтверждения OCR пока поддерживается только RUB."
        )
        form.addRow("Валюта по умолчанию", self.currency)
        year = QWidget()
        year_row = QHBoxLayout(year)
        year_row.setContentsMargins(0, 0, 0, 0)
        self.current_year = QCheckBox(f"Текущий год ({date.today().year})")
        self.current_year.setChecked(preferences.reference_year is None)
        self.reference_year = QSpinBox()
        self.reference_year.setRange(1, 9999)
        self.reference_year.setValue(preferences.year)
        self.reference_year.setEnabled(not self.current_year.isChecked())
        self.current_year.toggled.connect(self._year_changed)
        year_row.addWidget(self.current_year)
        year_row.addWidget(self.reference_year)
        form.addRow("Год для неполной даты", year)
        column.addLayout(form)
        self.confirm_fields = QCheckBox("Требовать флажки проверки четырёх полей")
        self.confirm_fields.setChecked(preferences.confirm_fields)
        column.addWidget(self.confirm_fields)
        self.review_before_save = QCheckBox("Показывать финансовое подтверждение перед записью")
        self.review_before_save.setChecked(preferences.review_before_save)
        column.addWidget(self.review_before_save)
        column.addWidget(
            plain(
                "Оба требования можно отключить. Проверка корректности суммы, даты, счетов "
                "и защита от повторов действуют при любом выборе. "
                "Неизвестные или противоречивые данные требуют исправления."
            )
        )
        column.addWidget(plain("Свои имена в описании операции"))
        column.addWidget(
            plain(
                "Укажите имя или фразу, по которой узнаёте собственный перевод. "
                "Для действия «Перевод между своими» выберите другой свой счёт или оставьте "
                "его для выбора при подготовке. «Исключить из импорта» полностью пропускает "
                "строку: операция не создаётся и остаток не меняется."
            )
        )
        self.parties = QTableWidget(0, 3)
        self.parties.setHorizontalHeaderLabels(
            ["Имя на изображении", "Действие", "Другой свой счёт"]
        )
        self.parties.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.parties.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.parties.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.parties.verticalHeader().setDefaultSectionSize(54)
        self.parties.setMinimumHeight(200)
        self.parties.setEnabled(allow_parties)
        column.addWidget(self.parties, 1)
        row = QHBoxLayout()
        self.add_party = QPushButton("Добавить имя")
        self.remove_party = QPushButton("Удалить выбранное имя")
        self.add_party.clicked.connect(lambda: self._add_party())
        self.remove_party.clicked.connect(self._remove_party)
        self.parties.itemSelectionChanged.connect(self._update_actions)
        row.addWidget(self.add_party)
        row.addWidget(self.remove_party)
        row.addStretch()
        column.addLayout(row)
        self.parties_notice = plain(
            "Свои имена хранятся в зашифрованной книге."
            if allow_parties
            else "Изменение своих имён доступно в открытой книге текущей схемы. "
            "Основные настройки можно сохранить без книги."
        )
        column.addWidget(self.parties_notice)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить настройки")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for party in preferences.transfer_parties:
            self._add_party(party)
        self._update_actions()

    def _year_changed(self, current: bool) -> None:
        if current:
            self.reference_year.setValue(date.today().year)
        self.reference_year.setEnabled(not current)

    def _add_party(self, party: TransferParty | None = None) -> None:
        if party is None and not self._allow_parties:
            return
        row = self.parties.rowCount()
        if row >= 100:
            return
        self.parties.insertRow(row)
        name = QLineEdit(party.name if party else "")
        name.setMaxLength(120)
        name.setPlaceholderText("Имя или фраза")
        action = QComboBox()
        action.addItem("Перевод между своими", False)
        action.addItem("Исключить из импорта", True)
        account = QComboBox()
        account.addItem("Выбрать при подготовке", None)
        for value in self._accounts:
            account.addItem(value.name, value.id)
        if party is not None:
            action.setCurrentIndex(action.findData(party.exclude))
            if party.account_id is not None and account.findData(party.account_id) < 0:
                account.addItem("Счёт недоступен в справочнике", party.account_id)
            account.setCurrentIndex(max(0, account.findData(party.account_id)))
        account.setEnabled(not bool(action.currentData()))
        action.currentIndexChanged.connect(
            lambda *args, action=action, account=account: self._party_action(action, account)
        )
        for column, widget in enumerate((name, action, account)):
            self.parties.setCellWidget(row, column, widget)
        self._update_actions()

    @staticmethod
    def _party_action(action: QComboBox, account: QComboBox) -> None:
        excluded = bool(action.currentData())
        if excluded:
            account.setCurrentIndex(0)
        account.setEnabled(not excluded)

    def _remove_party(self) -> None:
        if not self._allow_parties:
            return
        selected = self.parties.selectionModel().selectedRows()
        if len(selected) == 1:
            self.parties.removeRow(selected[0].row())
        self._update_actions()

    def _update_actions(self) -> None:
        self.add_party.setEnabled(self._allow_parties and self.parties.rowCount() < 100)
        self.remove_party.setEnabled(
            self._allow_parties and bool(self.parties.selectionModel().selectedRows())
        )

    def _validate(self) -> None:
        self.result_preferences = None
        try:
            parties = self._preferences.transfer_parties
            if self._allow_parties:
                rows = []
                for row in range(self.parties.rowCount()):
                    name = self.parties.cellWidget(row, 0)
                    action = self.parties.cellWidget(row, 1)
                    account = self.parties.cellWidget(row, 2)
                    if (
                        not isinstance(name, QLineEdit)
                        or not isinstance(action, QComboBox)
                        or not isinstance(account, QComboBox)
                    ):
                        raise FinanceError("Проверьте таблицу своих имён.")
                    rows.append(
                        TransferParty(
                            name.text().strip(), account.currentData(), bool(action.currentData())
                        )
                    )
                parties = tuple(rows)
            self.result_preferences = OcrPreferences(
                default_currency=self.currency.currentData(),
                reference_year=None
                if self.current_year.isChecked()
                else self.reference_year.value(),
                confirm_fields=self.confirm_fields.isChecked(),
                review_before_save=self.review_before_save.isChecked(),
                transfer_parties=parties,
            )
        except FinanceError as error:
            QMessageBox.warning(self, "Проверьте настройки распознавания", str(error))
            return
        self.accept()

    def reject(self) -> None:
        self.result_preferences = None
        super().reject()
