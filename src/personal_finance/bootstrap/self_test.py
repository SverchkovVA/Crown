"""Проверка собранного приложения на одноразовых синтетических данных."""

import json
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import monotonic
from typing import Any

from PySide6.QtWidgets import QApplication

from personal_finance.application.vault import create_vault, open_vault, write_private
from personal_finance.bootstrap.diagnostics import runtime_info
from personal_finance.domain.errors import InvalidCredentials
from personal_finance.domain.models import AccountKind
from personal_finance.infrastructure.backup.container import create_backup, restore_backup
from personal_finance.presentation.main_window import MainWindow


@contextmanager
def _step(report: dict[str, Any], identifier: str) -> Iterator[None]:
    result: dict[str, object] = {"id": identifier, "status": "running"}
    report["steps"].append(result)
    started = monotonic()
    try:
        yield
    except Exception:
        result["status"] = "failed"
        raise
    else:
        result["status"] = "passed"
    finally:
        result["duration_ms"] = round((monotonic() - started) * 1000)


def self_test(app: QApplication, report_path: Path | None = None) -> int:
    report: dict[str, Any] = {
        "format": 1,
        "status": "running",
        "steps": [],
        "uses_real_financial_data": False,
        "windows11_manual_acceptance": "pending",
    }
    exit_code = 0
    try:
        report["runtime"] = {**runtime_info(), "qt_platform": app.platformName()}
        with tempfile.TemporaryDirectory(prefix="pf-self-test-") as directory:
            root = Path(directory) / "Проверка пути с пробелами"
            root.mkdir()
            with _step(report, "create_encrypted_book"):
                source, recovery = create_vault(
                    root / "Исходная книга.pfvault", "Проверка сборки", "тестовая фраза сборки"
                )
            try:
                with _step(report, "persist_reference_data"):
                    account = source.catalog.add_account("Наличные", AccountKind.CASH)
                    original_replica = source.replica_id
                    source.check_integrity()
                with _step(report, "reopen_with_recovery_code"):
                    source.close()
                    source = open_vault(root / "Исходная книга.pfvault", recovery, recovery=True)
                    if source.catalog.accounts()[0].id != account.id:
                        raise RuntimeError("Не сохранилась идентичность счёта.")
                with _step(report, "reject_wrong_password"):
                    source.close()
                    try:
                        unexpected = open_vault(
                            root / "Исходная книга.pfvault", "заведомо неверная тестовая фраза"
                        )
                    except InvalidCredentials:
                        pass
                    else:
                        unexpected.close()
                        raise RuntimeError("Неверный пароль был принят.")
                    source = open_vault(root / "Исходная книга.pfvault", "тестовая фраза сборки")
                with _step(report, "create_verified_backup"):
                    backup = create_backup(
                        source, root / "Копия книги.pfbackup", "пароль проверочной копии"
                    )
                with _step(report, "restore_to_new_book"):
                    restored, _ = restore_backup(
                        backup,
                        root / "Восстановленная книга.pfvault",
                        "пароль проверочной копии",
                        "новая тестовая фраза",
                    )
                try:
                    with _step(report, "check_restored_data"):
                        if (
                            len(restored.catalog.accounts()) != 1
                            or restored.catalog.accounts()[0].id != account.id
                            or restored.replica_id == original_replica
                        ):
                            raise RuntimeError(
                                "Данные или идентичность реплики не прошли проверку."
                            )
                        restored.check_integrity()
                    with _step(report, "open_and_lock_window"):
                        window = MainWindow()
                        try:
                            window.activate_session(restored)
                            window.show()
                            app.processEvents()
                            if window.account_table.rowCount() != 1:
                                raise RuntimeError("Счёт не отображается в окне.")
                            window.lock_book()
                            if window.session is not None or window.account_table.rowCount() != 0:
                                raise RuntimeError("Книга не закрылась.")
                        finally:
                            window.close()
                finally:
                    restored.close()
            finally:
                source.close()
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        exit_code = 1
        if sys.stderr is not None:
            print(f"Самопроверка не пройдена: {type(exc).__name__}", file=sys.stderr)
    if report_path is not None:
        try:
            write_private(report_path, json.dumps(report, ensure_ascii=False, indent=2).encode())
        except OSError as exc:
            if sys.stderr is not None:
                print(f"Не удалось сохранить отчёт: {type(exc).__name__}", file=sys.stderr)
            return 2
    return exit_code
