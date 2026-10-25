"""Формы создания, открытия и восстановления книги."""

from pathlib import Path
from uuid import uuid4

from platformdirs import user_data_dir
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import validate_name
from personal_finance.infrastructure.security.keys import validate_password


def library_directory() -> Path:
    return Path(user_data_dir("PersonalFinance", appauthor=False)) / "books"


class NameDialog(QInputDialog):
    def accept(self) -> None:
        try:
            validate_name(self.textValue())
        except ValidationError as exc:
            QMessageBox.warning(self, "Проверьте название", str(exc))
            return
        super().accept()


class CreateDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, *, restoring: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Восстановить книгу" if restoring else "Создать финансовую книгу")
        self._restoring = restoring
        self.setMinimumWidth(580)
        layout = QVBoxLayout(self)
        introduction = QLabel(
            "Копия будет восстановлена в новую папку. Исходные книги сохранятся."
            if restoring
            else "Название и справочники хранятся в зашифрованной базе на этом устройстве."
        )
        introduction.setWordWrap(True)
        layout.addWidget(introduction)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.name = QLineEdit("Личные финансы")
        if not restoring:
            form.addRow("Название", self.name)
        self.path = QLineEdit(str(library_directory() / f"book-{uuid4().hex[:8]}.pfvault"))
        browse = QPushButton("Выбрать…")
        browse.clicked.connect(self._choose_parent)
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        form.addRow("Новая папка книги", row)
        self.backup_password = QLineEdit()
        self.backup_password.setEchoMode(QLineEdit.EchoMode.Password)
        if restoring:
            form.addRow("Пароль резервной копии", self.backup_password)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Парольная фраза от 12 символов")
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Новая парольная фраза" if restoring else "Парольная фраза", self.password)
        form.addRow("Повторите фразу", self.confirm)
        layout.addLayout(form)
        note = QLabel("Сохраните пароль отдельно. После создания будет показан код восстановления.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Восстановить" if restoring else "Создать книгу"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_parent(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Папка для новой книги")
        if folder:
            self.path.setText(str(Path(folder) / f"book-{uuid4().hex[:8]}.pfvault"))

    def _validate(self) -> None:
        try:
            if not self._restoring:
                validate_name(self.name.text())
            validate_password(self.password.text())
            if self.password.text() != self.confirm.text():
                raise ValidationError("Парольные фразы не совпадают.")
            if not self.path.text().strip() or Path(self.path.text()).exists():
                raise ValidationError("Укажите новое имя папки книги.")
        except ValidationError as exc:
            QMessageBox.warning(self, "Проверьте ввод", str(exc))
            return
        self.accept()


class OpenDialog(QDialog):
    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Открыть финансовую книгу")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        label = QLabel(str(path))
        label.setWordWrap(True)
        layout.addWidget(label)
        self.credential = QLineEdit()
        self.credential.setEchoMode(QLineEdit.EchoMode.Password)
        self.credential.setPlaceholderText("Парольная фраза")
        layout.addWidget(self.credential)
        self.recovery = QCheckBox("Использовать код восстановления")
        self.recovery.toggled.connect(
            lambda checked: self.credential.setPlaceholderText(
                "Код PF1-…" if checked else "Парольная фраза"
            )
        )
        layout.addWidget(self.recovery)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Open).setText("Открыть")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class PasswordDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, *, confirm: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle("Пароль резервной копии")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Пароль резервной копии")
        layout.addWidget(self.password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm.setPlaceholderText("Повторите пароль")
        self.confirm.setVisible(confirm)
        layout.addWidget(self.confirm)
        self._confirm_required = confirm
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Продолжить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate(self) -> None:
        if self._confirm_required:
            try:
                validate_password(self.password.text())
                if self.password.text() != self.confirm.text():
                    raise ValidationError("Пароли не совпадают.")
            except ValidationError as exc:
                QMessageBox.warning(self, "Проверьте ввод", str(exc))
                return
        self.accept()


class RecoveryDialog(QDialog):
    def __init__(self, code: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Сохраните код восстановления")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        text = QLabel(
            "Этот код открывает книгу при потере пароля. "
            "Храните его отдельно от компьютера и не передавайте другим людям."
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        field = QPlainTextEdit(code)
        field.setReadOnly(True)
        field.setMaximumHeight(100)
        field.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        layout.addWidget(field)
        saved = QCheckBox("Я сохранил код отдельно от книги")
        layout.addWidget(saved)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Готово")
        ok.setEnabled(False)
        saved.toggled.connect(ok.setEnabled)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
