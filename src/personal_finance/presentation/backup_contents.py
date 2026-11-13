"""Состав проверенной резервной копии перед восстановлением."""

from PySide6.QtWidgets import QDialog, QFormLayout, QVBoxLayout, QWidget

from personal_finance.infrastructure.backup.container import BackupContents
from personal_finance.presentation.analytics import _label


class BackupContentsDialog(QDialog):
    def __init__(self, contents: BackupContents, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Состав резервной копии")
        layout = QVBoxLayout(self)
        layout.addWidget(_label("Контрольная сумма архива и целостность книги подтверждены."))
        form = QFormLayout()
        form.setHorizontalSpacing(24)
        for name, value in (
            ("Создана (UTC)", contents.created_at),
            ("Схема в копии", contents.source_schema),
            ("Схема после восстановления", contents.restore_schema),
            ("Размер базы", f"{contents.database_size:,} байт".replace(",", " ")),
            ("Счета", str(contents.accounts)),
            ("Действующие операции", str(contents.operations)),
            ("Инвестиционные позиции", str(contents.investment_positions)),
        ):
            form.addRow(name, _label(value))
        layout.addLayout(form)
        layout.addStretch()
