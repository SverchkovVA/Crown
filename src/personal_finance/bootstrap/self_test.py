"""Проверка собранного приложения на одноразовых синтетических данных."""

import json
import sys
import tempfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from time import monotonic
from typing import Any

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication

from personal_finance.application.vault import VaultSession, create_vault, open_vault, write_private
from personal_finance.bootstrap.diagnostics import runtime_info
from personal_finance.domain.errors import InvalidCredentials, ValidationError
from personal_finance.domain.ledger import OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.domain.periods import AccountingMonth
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


def _balances(session: VaultSession) -> dict[str, int]:
    if session.ledger is None:
        raise RuntimeError("Финансовый журнал не создан.")
    return {item.account_id: item.amount.minor for item in session.ledger.balances()}


def self_test(app: QApplication, report_path: Path | None = None) -> int:
    report: dict[str, Any] = {
        "format": 1,
        "status": "running",
        "steps": [],
        "uses_real_financial_data": False,
        "windows11_manual_acceptance": "pending",
        "stage4_complete": False,
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
                    reserve = source.catalog.add_account(
                        "Резерв",
                        AccountKind.SAVINGS,
                        source.catalog.institutions()[0].id,
                    )
                    original_replica = source.replica_id
                    source.check_integrity()
                with _step(report, "persist_financial_journal"):
                    ledger = source.ledger
                    if ledger is None:
                        raise RuntimeError("Финансовый журнал не создан.")
                    categories = source.catalog.categories()
                    income = next(c.id for c in categories if c.kind == CategoryKind.INCOME)
                    expenses = [c.id for c in categories if c.kind == CategoryKind.EXPENSE]
                    expense = expenses[0]
                    today = date.today()
                    ledger.add(
                        OperationDraft(
                            OperationKind.OPENING,
                            today,
                            account.id,
                            Money(100000),
                        )
                    )
                    incoming = ledger.add(
                        OperationDraft(
                            OperationKind.INCOME,
                            today,
                            account.id,
                            Money(10000),
                            income,
                        )
                    )
                    spending = ledger.add(
                        OperationDraft(
                            OperationKind.EXPENSE,
                            today,
                            account.id,
                            Money(2500),
                            parts=(
                                OperationPart(expenses[0], Money(1000)),
                                OperationPart(expenses[1], Money(1500)),
                            ),
                        )
                    )
                    ledger.add(
                        OperationDraft(
                            OperationKind.TRANSFER,
                            today,
                            account.id,
                            Money(20000),
                            destination_account_id=reserve.id,
                            fee=Money(100),
                            fee_category_id=expense,
                        )
                    )
                    corrected_spending = ledger.replace(
                        spending.id,
                        spending.revision_id,
                        OperationDraft(
                            OperationKind.EXPENSE,
                            today,
                            account.id,
                            Money(3000),
                            parts=(
                                OperationPart(expenses[0], Money(1000)),
                                OperationPart(expenses[1], Money(2000)),
                            ),
                        ),
                    )
                    ledger.delete(incoming.id, incoming.revision_id)
                    expected_balances = {account.id: 76900, reserve.id: 20000}
                    if _balances(source) != expected_balances:
                        raise RuntimeError("Не совпали проверочные остатки.")
                    source.check_integrity()
                with _step(report, "persist_linked_refund"):
                    returned = ledger.add(
                        OperationDraft(
                            OperationKind.REFUND,
                            today,
                            account.id,
                            Money(1000),
                            expenses[0],
                            refund_of=spending.id,
                            refund_of_revision_id=corrected_spending.revision_id,
                        )
                    )
                    expected_balances[account.id] += 1000
                    if _balances(source) != expected_balances:
                        raise RuntimeError("Не совпал остаток после возврата.")
                    if ledger.refund_candidates()[0].amount_remaining != Money(2000):
                        raise RuntimeError("Не совпал остаток к возврату.")
                    source.check_integrity()
                with _step(report, "protect_closed_period"):
                    periods = source.periods
                    if periods is None:
                        raise RuntimeError("Закрытие периодов недоступно.")
                    month = AccountingMonth.from_date(today)
                    closed = periods.close(month)
                    try:
                        ledger.delete(returned.id, returned.revision_id)
                    except ValidationError:
                        pass
                    else:
                        raise RuntimeError("Закрытый период допустил удаление операции.")
                    opened = periods.reopen(month, closed.revision_id, reason="Проверка открытия")
                    periods.close(month, expected_revision_id=opened.revision_id)
                    expected_period_history = periods.history(month)
                    source.check_integrity()
                with _step(report, "reconcile_read_only"):
                    before_operations = ledger.operations(include_deleted=True)
                    expected_reconciliation = ledger.reconcile(
                        account.id, today, Money(expected_balances[account.id])
                    )
                    mismatch = ledger.reconcile(
                        account.id, today, Money(expected_balances[account.id] + 1)
                    )
                    if (
                        not expected_reconciliation.matches
                        or mismatch.difference_minor != 1
                        or ledger.operations(include_deleted=True) != before_operations
                        or _balances(source) != expected_balances
                    ):
                        raise RuntimeError("Сверка изменила данные или дала неверную разницу.")
                with _step(report, "reopen_with_recovery_code"):
                    source.close()
                    source = open_vault(root / "Исходная книга.pfvault", recovery, recovery=True)
                    if {a.id for a in source.catalog.accounts()} != {account.id, reserve.id}:
                        raise RuntimeError("Не сохранилась идентичность счетов.")
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
                    restored, restored_recovery = restore_backup(
                        backup,
                        root / "Восстановленная книга.pfvault",
                        "пароль проверочной копии",
                        "новая тестовая фраза",
                    )
                try:
                    with _step(report, "check_restored_data"):
                        if (
                            {a.id for a in restored.catalog.accounts()} != {account.id, reserve.id}
                            or restored.replica_id == original_replica
                            or _balances(restored) != expected_balances
                        ):
                            raise RuntimeError("Данные или реплика не прошли проверку.")
                        if (
                            restored.periods is None
                            or restored.periods.history(month) != expected_period_history
                        ):
                            raise RuntimeError("Не сохранилась история закрытия периода.")
                        try:
                            if restored.ledger is None:
                                raise RuntimeError("Финансовый журнал отсутствует.")
                            restored.ledger.delete(returned.id, returned.revision_id)
                        except ValidationError:
                            pass
                        else:
                            raise RuntimeError("Восстановленный период не защищён.")
                        if (
                            restored.ledger is None
                            or restored.ledger.reconcile(
                                account.id, today, Money(expected_balances[account.id])
                            )
                            != expected_reconciliation
                        ):
                            raise RuntimeError("Сверка после восстановления изменилась.")
                        restored.check_integrity()
                    with _step(report, "check_financial_history"):
                        if (
                            restored.ledger is None
                            or len(restored.ledger.operations()) != 4
                            or len(restored.ledger.history(spending.id)) != 2
                            or restored.ledger.history(spending.id)[-1].draft.parts
                            != (
                                OperationPart(expenses[0], Money(1000)),
                                OperationPart(expenses[1], Money(2000)),
                            )
                            or not restored.ledger.history(incoming.id)[-1].is_deleted
                            or restored.ledger.history(returned.id)[-1].draft != returned.draft
                            or restored.ledger.refund_candidates()[0].amount_remaining
                            != Money(2000)
                        ):
                            raise RuntimeError("Не сохранилась финансовая история.")
                    with _step(report, "open_and_lock_window"):
                        restored.close()
                        with ThreadPoolExecutor(max_workers=1) as worker:
                            background = worker.submit(
                                open_vault,
                                root / "Восстановленная книга.pfvault",
                                restored_recovery,
                                recovery=True,
                            ).result(timeout=15)
                            window = MainWindow()
                            window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
                            try:
                                window.activate_session(background)
                                window.show()
                                app.processEvents()
                                if (
                                    window.account_model.rowCount() != 2
                                    or window.operation_model.rowCount() != 4
                                ):
                                    raise RuntimeError("Счета или операции не отображаются.")
                                window.lock_book()
                                if (
                                    window.session is not None
                                    or window.account_model.rowCount() != 0
                                    or window.operation_model.rowCount() != 0
                                ):
                                    raise RuntimeError("Книга не закрылась.")
                                reopened = worker.submit(
                                    open_vault,
                                    root / "Восстановленная книга.pfvault",
                                    "новая тестовая фраза",
                                ).result(timeout=15)
                                try:
                                    reopened.check_integrity()
                                    if _balances(reopened) != expected_balances:
                                        raise RuntimeError("Остатки после закрытия изменились.")
                                finally:
                                    reopened.close()
                            finally:
                                window.close()
                                window.deleteLater()
                                QCoreApplication.sendPostedEvents(
                                    window, QEvent.Type.DeferredDelete
                                )
                                background.close()
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
