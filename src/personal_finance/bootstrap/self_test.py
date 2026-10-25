"""Проверка собранного приложения на одноразовых синтетических данных."""

import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

from personal_finance.application.vault import create_vault
from personal_finance.domain.models import AccountKind
from personal_finance.infrastructure.backup.container import create_backup, restore_backup
from personal_finance.presentation.main_window import MainWindow


def self_test(app: QApplication) -> int:
    try:
        with tempfile.TemporaryDirectory(prefix="pf-self-test-") as directory:
            root = Path(directory)
            source, _ = create_vault(
                root / "source.pfvault", "Проверка сборки", "тестовая фраза сборки"
            )
            try:
                source.catalog.add_account("Наличные", AccountKind.CASH)
                backup = create_backup(source, root / "copy.pfbackup", "пароль проверочной копии")
                restored, _ = restore_backup(
                    backup,
                    root / "restored.pfvault",
                    "пароль проверочной копии",
                    "новая тестовая фраза",
                )
                try:
                    if len(restored.catalog.accounts()) != 1:
                        raise RuntimeError("Не совпало количество счетов.")
                    window = MainWindow()
                    window.activate_session(restored)
                    window.show()
                    app.processEvents()
                    window.close()
                finally:
                    restored.close()
            finally:
                source.close()
        return 0
    except Exception as exc:
        # Сборка с gui-subsystem может не иметь stdout. Код возврата используется CI.
        if sys.stderr is not None:
            print(f"Самопроверка не пройдена: {type(exc).__name__}", file=sys.stderr)
        return 1
