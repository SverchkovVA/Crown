"""Проверка собранного приложения на одноразовых синтетических данных."""

import json
import sys
import tempfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QCoreApplication, QDate, QEvent, Qt
from PySide6.QtWidgets import QApplication, QDateEdit, QLineEdit

from personal_finance.application.analytics import Analytics
from personal_finance.application.importing import Importing
from personal_finance.application.ocr_cards import CardRecognition
from personal_finance.application.vault import VaultSession, create_vault, open_vault, write_private
from personal_finance.bootstrap.diagnostics import runtime_info
from personal_finance.bootstrap.ocr_audit_self_test import (
    check_audited_ocr_link,
    check_ocr_workflow,
)
from personal_finance.bootstrap.ocr_runtime import load_runtime_manifest, recognitions_for_directory
from personal_finance.domain.analytics import ReportPeriod, ReportScope
from personal_finance.domain.analytics_cashflow import CashSourceKind, cash_flow_sources
from personal_finance.domain.analytics_cashflow_months import cash_month_sources
from personal_finance.domain.classification import (
    CategoryRule,
    ClassificationInput,
    ClassificationStatus,
)
from personal_finance.domain.deposit_scenarios import scenario_from_json, scenario_json
from personal_finance.domain.deposits import (
    AccrualBoundary,
    BalanceMoment,
    DayCount,
    DepositRate,
    DepositScenario,
    InterestRounding,
    RoundingStage,
    forecast_deposit,
)
from personal_finance.domain.errors import InvalidCredentials, ValidationError
from personal_finance.domain.exporting import ExportFormat, ExportSettings
from personal_finance.domain.imports import ImportSelection, ImportStatus
from personal_finance.domain.ledger import OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.domain.ocr_imports import OcrImportStatus
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.domain.periods import AccountingMonth
from personal_finance.domain.query import OperationQuery
from personal_finance.infrastructure.backup.container import create_backup, restore_backup
from personal_finance.infrastructure.importing.statements import CsvMapping
from personal_finance.presentation.analytics import IncomeExpenseDialog, format_report_amount
from personal_finance.presentation.analytics_cashflow import CashFlowDialog
from personal_finance.presentation.analytics_comparison import IncomeExpenseComparisonDialog
from personal_finance.presentation.deposits import DepositForecastDialog, DepositScenarioDialog
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


def _check_packaged_ofx(importing: Importing, day: date) -> None:
    stamp = day.strftime("%Y%m%d")
    body = (
        "<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>RUB</CURDEF>"
        "<BANKACCTFROM><BANKID>self-test</BANKID><ACCTID>synthetic-only</ACCTID>"
        "<ACCTTYPE>CHECKING</ACCTTYPE></BANKACCTFROM><BANKTRANLIST>"
        f"<DTSTART>{stamp}</DTSTART><DTEND>{stamp}</DTEND><STMTTRN>"
        f"<TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>{stamp}</DTPOSTED>"
        "<TRNAMT>-0.01</TRNAMT><FITID>self-test-ofx</FITID>"
        "<NAME>Проверка кодировки</NAME></STMTTRN></BANKTRANLIST>"
        "</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"
    )
    xml = ('<?xml version="1.0" encoding="windows-1251"?>' + body).encode("cp1251")
    sgml = body
    for name in (
        "CURDEF",
        "BANKID",
        "ACCTID",
        "ACCTTYPE",
        "DTSTART",
        "DTEND",
        "TRNTYPE",
        "DTPOSTED",
        "TRNAMT",
        "FITID",
        "NAME",
    ):
        sgml = sgml.replace("</" + name + ">", "\n")
    header = (
        "OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\n"
        "ENCODING:USASCII\nCHARSET:1251\n\n"
    )
    for contents in (xml, (header + sgml).encode("cp1251")):
        parsed = importing.parse_ofx(contents)
        if (
            parsed.issues
            or len(parsed.rows) != 1
            or parsed.rows[0].amount != Money(-1)
            or parsed.rows[0].occurred_on != day
            or parsed.rows[0].note != "Проверка кодировки"
        ):
            raise RuntimeError("Парсеры OFX или кодировка сборки не прошли проверку.")


def self_test(
    app: QApplication, report_path: Path | None = None, *, ocr_directory: Path | None = None
) -> int:
    report: dict[str, Any] = {
        "format": 1,
        "status": "running",
        "steps": [],
        "uses_real_financial_data": False,
        "windows11_manual_acceptance": "pending",
        "stage4_complete": False,
        "local_ocr_runtime_requested": ocr_directory is not None,
    }
    exit_code = 0
    try:
        report["runtime"] = {**runtime_info(), "qt_platform": app.platformName()}
        ocr_recognitions: tuple[CardRecognition, ...] = ()
        if ocr_directory is not None:
            with _step(report, "verify_local_ocr_runtime"):
                ocr_recognitions = recognitions_for_directory(ocr_directory)
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
                        source.catalog.add_institution("Вымышленный банк самопроверки").id,
                    )
                    organization = source.organization
                    if organization is None:
                        raise RuntimeError("Теги и фильтры недоступны.")
                    tag = organization.add_tag("Проверочный тег")
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
                            tag_ids=(tag.id,),
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
                            tag_ids=(tag.id,),
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
                with _step(report, "persist_tags_and_saved_filter"):
                    saved_filter = organization.save_filter(
                        "Проверочный фильтр",
                        OperationQuery(
                            account_ids=(account.id,),
                            tag_ids=(tag.id,),
                            kinds=(OperationKind.EXPENSE,),
                        ),
                    )
                    organization.rename_tag(tag.id, tag.revision_id, "Переименованный тег")
                    expected_tags = organization.tags(include_archived=True)
                    expected_filters = organization.filters(include_archived=True)
                    if ledger.search(saved_filter.query) != [corrected_spending]:
                        raise RuntimeError("Фильтр не нашёл проверочный расход.")
                    source.check_integrity()
                with _step(report, "persist_category_rules"):
                    classification = source.classification
                    if classification is None:
                        raise RuntimeError("Правила категорий недоступны.")
                    operations_before_rules = ledger.operations(include_deleted=True)
                    rule = CategoryRule(
                        str(uuid4()),
                        "Проверочное правило",
                        OperationKind.EXPENSE,
                        "проверка",
                        expense,
                        10,
                        account.id,
                    )
                    rule_command = str(uuid4())
                    initial_rule = classification.save(rule, command_id=rule_command)
                    changed_rule = classification.save(
                        replace(rule, priority=20), expected_revision_id=initial_rule.revision_id
                    )
                    expected_rule_history = [initial_rule, changed_rule]
                    rule_input = ClassificationInput(
                        OperationKind.EXPENSE, account.id, "ПРОВЕРКА импорта"
                    )
                    suggestion = classification.snapshot().suggest(rule_input)
                    manual = classification.suggest(
                        replace(rule_input, manual_category_id=expenses[1])
                    )
                    if (
                        suggestion.category_id != expense
                        or suggestion.rule_ids != (rule.id,)
                        or manual.status != ClassificationStatus.MANUAL
                        or manual.category_id != expenses[1]
                        or classification.save(rule, command_id=rule_command) != initial_rule
                        or ledger.operations(include_deleted=True) != operations_before_rules
                        or _balances(source) != expected_balances
                    ):
                        raise RuntimeError(
                            "Подсказки правил изменили ручной выбор или финансовые факты."
                        )
                    source.check_integrity()
                with _step(report, "confirm_statement_import"):
                    importing = source.importing
                    if importing is None:
                        raise RuntimeError("Импорт недоступен.")
                    document = importing.parse_csv(
                        f"date;amount;id\n{today.isoformat()};-1.23;self-test-id\n".encode(),
                        CsvMapping(
                            "date",
                            "amount",
                            id_column="id",
                            date_format="%Y-%m-%d",
                            decimal_separator=".",
                        ),
                    )
                    selected_import = (
                        ImportSelection(
                            1,
                            OperationDraft(
                                OperationKind.EXPENSE,
                                today,
                                account.id,
                                Money(123),
                                expense,
                            ),
                        ),
                    )
                    _check_packaged_ofx(importing, today)
                    import_command = str(uuid4())
                    imported = importing.confirm(
                        document,
                        account.id,
                        selected_import,
                        command_id=import_command,
                    )
                    if (
                        importing.preview(document, account.id).lines[0].status
                        != ImportStatus.DUPLICATE
                        or importing.confirm(
                            document, account.id, selected_import, command_id=import_command
                        )
                        != imported
                    ):
                        raise RuntimeError("Повтор импорта создал лишнее движение.")
                    expected_balances[account.id] -= 123
                    if _balances(source) != expected_balances:
                        raise RuntimeError("Не совпал остаток после импорта.")
                    source.check_integrity()
                with _step(report, "export_reviewed_journal"):
                    exporting = source.exporting
                    if exporting is None:
                        raise RuntimeError("Экспорт недоступен.")
                    operations_before_export = ledger.operations(include_deleted=True)
                    export_preview = exporting.preview(
                        ExportSettings(
                            OperationQuery(tag_ids=(tag.id,)),
                        )
                    )
                    export_path = exporting.save(
                        root / "Журнал.json",
                        export_preview,
                        ExportFormat.JSON,
                    )
                    exported_bytes = export_path.read_bytes()
                    rows = json.loads(exported_bytes)["operations"]
                    if (
                        len(rows) != 1
                        or rows[0]["amount_minor"] != "3000"
                        or "note" in rows[0]
                        or "tag_ids" in rows[0]
                        or source.book_id.encode() in exported_bytes
                    ):
                        raise RuntimeError("Состав экспортного файла не соответствует выбору.")
                    csv_path = exporting.save(root / "Журнал.csv", export_preview, ExportFormat.CSV)
                    try:
                        importing.parse_csv(
                            csv_path.read_bytes(),
                            CsvMapping("Дата", "Сумма", date_format="%Y-%m-%d"),
                        )
                    except ValidationError:
                        pass
                    else:
                        raise RuntimeError("Экспорт журнала принят как банковская выписка.")
                    try:
                        exporting.save(export_path, export_preview, ExportFormat.JSON)
                    except ValidationError:
                        pass
                    else:
                        raise RuntimeError("Экспорт заменил существующий файл.")
                    if (
                        export_path.read_bytes() != exported_bytes
                        or ledger.operations(include_deleted=True) != operations_before_export
                        or _balances(source) != expected_balances
                    ):
                        raise RuntimeError("Экспорт изменил книгу или прежний файл.")
                    source.check_integrity()
                with _step(report, "confirm_ocr_with_audit"):
                    ocr_command = str(uuid4())
                    ocr_selection, ocr_result = check_audited_ocr_link(
                        source, corrected_spending, account.id, ocr_command
                    )
                with _step(report, "persist_ocr_workflow"):
                    batch_command = str(uuid4())
                    batch_selection, batch_result = check_ocr_workflow(
                        source, ocr_selection, batch_command
                    )
                    expected_ocr_settings = (
                        source.ocr_settings.load()[0] if source.ocr_settings is not None else None
                    )
                with _step(report, "persist_generic_ocr_audit"):
                    if (
                        source.schema_revision != "0012"
                        or source.ocr_importing is None
                        or not source.ocr_importing.supports_universal
                    ):
                        raise RuntimeError("Общий OCR недоступен в новой схеме.")
                    generic_command = str(uuid4())
                    generic_selection, generic_result = check_audited_ocr_link(
                        source,
                        corrected_spending,
                        account.id,
                        generic_command,
                        profile=UNIVERSAL_PROFILE,
                    )
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
                with _step(report, "report_income_expense_read_only"):
                    before_report = ledger.operations(include_deleted=True)
                    expected_report = Analytics(ledger, source.mutex, source.ensure_open).report(
                        ReportPeriod(today, today)
                    )
                    if (
                        expected_report.operation_count != 4
                        or len(expected_report.totals) != 1
                        or expected_report.totals[0].income_minor != 0
                        or expected_report.totals[0].expense_minor != 2223
                    ):
                        raise RuntimeError(
                            "Отчёт неверно учёл возврат, комиссию или начальный остаток."
                        )
                    report_dialog = IncomeExpenseDialog(
                        expected_report, source.catalog.categories()
                    )
                    try:
                        if (
                            report_dialog.summary_table.model().data(
                                report_dialog.summary_table.model().index(0, 2)
                            )
                            != "22,23"
                        ):
                            raise RuntimeError("Точная сумма отчёта недоступна в интерфейсе.")
                        report_dialog._select_sources("RUB", expenses[1])
                        selected_sources = report_dialog._source_rows
                        if (
                            len(selected_sources) != 1
                            or selected_sources[0].operation.id != spending.id
                            or selected_sources[0].operation.draft.amount.minor != 3000
                            or selected_sources[0].totals.expense_minor != 2000
                        ):
                            raise RuntimeError("Не совпал вклад части исходной операции в отчёт.")
                    finally:
                        report_dialog.reject()
                        report_dialog.deleteLater()
                        QCoreApplication.sendPostedEvents(report_dialog, QEvent.Type.DeferredDelete)
                    filtered_report = Analytics(ledger, source.mutex, source.ensure_open).report(
                        ReportPeriod(today, today),
                        ReportScope(frozenset({account.id}), frozenset({expenses[1]})),
                    )
                    if (
                        filtered_report.operation_count != 1
                        or len(filtered_report.totals) != 1
                        or filtered_report.totals[0].expense_minor != 2000
                    ):
                        raise RuntimeError("Глобальный фильтр отчёта потерял выбранную часть.")
                    filtered_dialog = IncomeExpenseDialog(
                        filtered_report,
                        source.catalog.categories(),
                        accounts=source.catalog.accounts(),
                    )
                    try:
                        filtered_dialog._select_sources("RUB", expenses[1])
                        filtered_dialog.source_reset.click()
                        if (
                            len(filtered_dialog._source_rows) != 1
                            or filtered_dialog._source_rows[0].totals.expense_minor != 2000
                            or filtered_dialog._source_rows[0].operation.draft.amount.minor != 3000
                        ):
                            raise RuntimeError("Сброс строки расширил глобальный отбор отчёта.")
                    finally:
                        filtered_dialog.reject()
                        filtered_dialog.deleteLater()
                        QCoreApplication.sendPostedEvents(
                            filtered_dialog, QEvent.Type.DeferredDelete
                        )
                    if (
                        ledger.operations(include_deleted=True) != before_report
                        or _balances(source) != expected_balances
                    ):
                        raise RuntimeError("Отчёт изменил финансовые данные.")
                with _step(report, "compare_income_expense_read_only"):
                    before_comparison = ledger.operations(include_deleted=True)
                    earlier = today - timedelta(days=1)
                    comparison = Analytics(ledger, source.mutex, source.ensure_open).compare(
                        ReportPeriod(earlier, earlier),
                        ReportPeriod(today, today),
                        ReportScope(frozenset({account.id}), frozenset({expenses[1]})),
                    )
                    if (
                        comparison.before.operation_count != 0
                        or comparison.after.operation_count != 1
                        or len(comparison.totals) != 1
                        or comparison.totals[0].expense_delta != 2000
                        or comparison.totals[0].difference_delta != -2000
                    ):
                        raise RuntimeError(
                            "Сравнение периодов потеряло общий отбор или точную разницу."
                        )
                    comparison_dialog = IncomeExpenseComparisonDialog(
                        comparison, source.catalog.categories(), accounts=source.catalog.accounts()
                    )
                    try:
                        model = comparison_dialog.total_table.model()
                        if (
                            model.data(model.index(1, 4)) != "+20,00"
                            or comparison_dialog.before_button.isEnabled()
                            or not comparison_dialog.after_button.isEnabled()
                        ):
                            raise RuntimeError(
                                "В сравнении недоступна сумма или неверен переход к источникам."
                            )
                    finally:
                        comparison_dialog.reject()
                        comparison_dialog.deleteLater()
                        QCoreApplication.sendPostedEvents(
                            comparison_dialog, QEvent.Type.DeferredDelete
                        )
                    if (
                        ledger.operations(include_deleted=True) != before_comparison
                        or _balances(source) != expected_balances
                    ):
                        raise RuntimeError("Сравнение периодов изменило финансовые данные.")
                with _step(report, "report_cash_flow_read_only"):
                    before_cash_flow = ledger.operations(include_deleted=True)
                    cash_report = Analytics(ledger, source.mutex, source.ensure_open).cash_flow(
                        ReportPeriod(date.min, date.max)
                    )
                    if (
                        {v.account_id: v.totals.closing_minor for v in cash_report.accounts}
                        != expected_balances
                        or len(cash_report.totals) != 1
                        or cash_report.totals[0].closing_minor != sum(expected_balances.values())
                    ):
                        raise RuntimeError("Движение средств не совпало с остатками журнала.")
                    total = cash_report.totals[0]
                    cash_lines = cash_flow_sources(
                        cash_report, total.currency, CashSourceKind.THROUGH_END
                    )
                    if sum(v.minor for v in cash_lines) != total.closing_minor:
                        raise RuntimeError("Источники движения средств не совпали с итогом.")
                    cash_dialog = CashFlowDialog(
                        cash_report,
                        source.catalog.accounts(),
                        categories=source.catalog.categories(),
                    )
                    try:
                        model = cash_dialog.total_table.model()
                        if (
                            model.data(model.index(0, 6))
                            != format_report_amount(total.closing_minor, total.currency)
                            or not cash_dialog.source_buttons[
                                CashSourceKind.THROUGH_END
                            ].isEnabled()
                        ):
                            raise RuntimeError("Остаток или источники недоступны в интерфейсе.")
                        last_day = max(v.operation.draft.occurred_on for v in cash_report.movements)
                        cash_dialog.months.month.setDate(QDate(last_day.year, last_day.month, 1))
                        monthly_page = cash_dialog.months.page
                        if (
                            monthly_page is None
                            or monthly_page.rows[0].totals.closing_minor != total.closing_minor
                            or sum(
                                v.minor
                                for v in cash_month_sources(
                                    monthly_page, 0, CashSourceKind.THROUGH_END
                                )
                            )
                            != total.closing_minor
                        ):
                            raise RuntimeError("Месячный остаток не совпал с источниками.")
                        cash_dialog.months.canvas.draw()
                        if (
                            format_report_amount(total.closing_minor, total.currency)
                            not in {v.get_text() for v in cash_dialog.months.axes.texts}
                            or not cash_dialog.months._targets
                        ):
                            raise RuntimeError("Месячный график не совпал с точным остатком.")
                    finally:
                        cash_dialog.reject()
                        cash_dialog.deleteLater()
                        QCoreApplication.sendPostedEvents(cash_dialog, QEvent.Type.DeferredDelete)
                    if (
                        ledger.operations(include_deleted=True) != before_cash_flow
                        or _balances(source) != expected_balances
                    ):
                        raise RuntimeError("Отчёт движения средств изменил финансовые данные.")
                with _step(report, "forecast_deposit_read_only"):
                    before_deposit = ledger.operations(include_deleted=True)
                    deposit = forecast_deposit(
                        DepositScenario(
                            opened_on=date(2026, 1, 1),
                            matures_on=date(2027, 1, 1),
                            initial=Money(1),
                            rates=(DepositRate(str(uuid4()), date(2026, 1, 1), Decimal(50)),),
                            movements=(),
                            payout_dates=(date(2027, 1, 1),),
                            boundary=AccrualBoundary.OPENING_INCLUDED,
                            balance_moment=BalanceMoment.AFTER_MOVEMENTS,
                            day_count=DayCount.FIXED_365,
                            rounding=InterestRounding.HALF_UP,
                            rounding_stage=RoundingStage.PAYOUT,
                            capitalize=False,
                            allow_topups=False,
                            allow_withdrawals=False,
                            minimum=Money(0),
                        )
                    )
                    if deposit.interest_minor != 1 or deposit.closing_minor != 1:
                        raise RuntimeError("Не подтверждено точное округление процентов.")
                    deposit_dialog = DepositForecastDialog(deposit, allow_edit=True)
                    try:
                        model = deposit_dialog.model
                        if model.rowCount() != 1 or model.data(model.index(0, 1)) != "0,01":
                            raise RuntimeError("График процентов не совпал с точной выплатой.")
                        if deposit_dialog.edit_button is None:
                            raise RuntimeError("Возврат к условиям прогноза недоступен.")
                        deposit_dialog.edit_button.click()
                        if not deposit_dialog.edit_requested:
                            raise RuntimeError("Не выбран возврат к условиям прогноза.")
                    finally:
                        deposit_dialog.reject()
                        deposit_dialog.deleteLater()
                        QCoreApplication.sendPostedEvents(
                            deposit_dialog, QEvent.Type.DeferredDelete
                        )
                    scenario_form = DepositScenarioDialog()
                    try:
                        scenario_form.initial.setText("100,00")
                        scenario_form.rate.setText("365")
                        scenario_form.start.setDate(QDate(2026, 1, 1))
                        scenario_form.end.setDate(QDate(2026, 1, 4))
                        scenario_form.day_count.setCurrentIndex(1)
                        scenario_form.allow_topups.setChecked(True)
                        scenario_form.allow_withdrawals.setChecked(True)
                        scenario_form._add_event(scenario_form.rate_table)
                        rate_date_editor = scenario_form.rate_table.cellWidget(0, 0)
                        rate_value_field = scenario_form.rate_table.cellWidget(0, 1)
                        if not isinstance(rate_date_editor, QDateEdit) or not isinstance(
                            rate_value_field, QLineEdit
                        ):
                            raise RuntimeError("Поля изменения ставки недоступны.")
                        rate_date_editor.setDate(QDate(2026, 1, 3))
                        rate_value_field.setText("730")
                        for row, day, amount_text in [(0, 2, "+100,00"), (1, 4, "−25,00")]:
                            scenario_form._add_event(scenario_form.movement_table)
                            movement_date_editor = scenario_form.movement_table.cellWidget(row, 0)
                            movement_value_field = scenario_form.movement_table.cellWidget(row, 1)
                            if not isinstance(movement_date_editor, QDateEdit) or not isinstance(
                                movement_value_field, QLineEdit
                            ):
                                raise RuntimeError("Поля сценарного движения недоступны.")
                            movement_date_editor.setDate(QDate(2026, 1, day))
                            movement_value_field.setText(amount_text)
                        scenario_form._accept()
                        if scenario_form.result_scenario is None:
                            raise RuntimeError("Не подготовлены сценарные ставки и движения.")
                        first_scenario = scenario_form.result_scenario
                        scenario_form._accept()
                        if scenario_form.result_scenario != first_scenario:
                            raise RuntimeError("Повторный расчёт изменил идентичность условий.")
                        restored_scenario = scenario_from_json(
                            scenario_json(scenario_form.result_scenario)
                        )
                        if restored_scenario != scenario_form.result_scenario:
                            raise RuntimeError("Контракт сценария изменил условия расчёта.")
                        changed_deposit = forecast_deposit(restored_scenario)
                        if (
                            changed_deposit.interest_minor != 950
                            or changed_deposit.closing_minor != 17500
                        ):
                            raise RuntimeError("Сценарные изменения дали неверный прогноз.")
                        changed_dialog = DepositForecastDialog(changed_deposit)
                        try:
                            if (
                                changed_dialog.rate_table.model().rowCount() != 2
                                or changed_dialog.movement_table.model().rowCount() != 2
                                or changed_dialog.model.data(changed_dialog.model.index(0, 1))
                                != "9,50"
                            ):
                                raise RuntimeError("Условия сценария недоступны в результате.")
                        finally:
                            changed_dialog.reject()
                            changed_dialog.deleteLater()
                            QCoreApplication.sendPostedEvents(
                                changed_dialog, QEvent.Type.DeferredDelete
                            )
                    finally:
                        scenario_form.reject()
                        scenario_form.deleteLater()
                        QCoreApplication.sendPostedEvents(scenario_form, QEvent.Type.DeferredDelete)
                    if (
                        ledger.operations(include_deleted=True) != before_deposit
                        or _balances(source) != expected_balances
                    ):
                        raise RuntimeError("Прогноз процентов изменил финансовые данные.")
                with _step(report, "render_income_expense_charts"):
                    chart_dialog = IncomeExpenseDialog(expected_report, source.catalog.categories())
                    try:
                        chart_dialog.charts.kind.setCurrentIndex(1)
                        chart_dialog.charts.canvas.draw()
                        if (
                            chart_dialog.charts.report != expected_report
                            or "20,00"
                            not in {text.get_text() for text in chart_dialog.charts.axes.texts}
                            or not chart_dialog.charts._targets
                        ):
                            raise RuntimeError("График не совпал с точным снимком отчёта.")
                    finally:
                        chart_dialog.reject()
                        chart_dialog.deleteLater()
                        QCoreApplication.sendPostedEvents(chart_dialog, QEvent.Type.DeferredDelete)
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
                        if (
                            restored.organization is None
                            or restored.organization.tags(include_archived=True) != expected_tags
                            or restored.organization.filters(include_archived=True)
                            != expected_filters
                            or restored.ledger.search(saved_filter.query) != [corrected_spending]
                        ):
                            raise RuntimeError("Теги или сохранённый фильтр не восстановлены.")
                        if (
                            restored.importing is None
                            or restored.importing.preview(document, account.id).lines[0].status
                            != ImportStatus.DUPLICATE
                            or restored.importing.confirm(
                                document,
                                account.id,
                                selected_import,
                                command_id=import_command,
                            )
                            != imported
                        ):
                            raise RuntimeError("История импорта не восстановлена.")
                        if (
                            restored.classification is None
                            or restored.classification.history(rule.id) != expected_rule_history
                            or restored.classification.suggest(rule_input) != suggestion
                        ):
                            raise RuntimeError("Правила категорий не восстановлены.")
                        if (
                            restored.ocr_importing is None
                            or restored.ocr_importing.preview(ocr_selection).status
                            != OcrImportStatus.DUPLICATE
                            or restored.ocr_importing.confirm(ocr_selection, command_id=ocr_command)
                            != ocr_result
                        ):
                            raise RuntimeError("Аудит OCR или защита повторов не восстановлены.")
                        if (
                            restored.ocr_importing is None
                            or not restored.ocr_importing.supports_universal
                            or restored.ocr_importing.preview(generic_selection).status
                            != OcrImportStatus.DUPLICATE
                            or restored.ocr_importing.confirm(
                                generic_selection, command_id=generic_command
                            )
                            != generic_result
                        ):
                            raise RuntimeError(
                                "Общий аудит OCR или его квитанция не восстановлены."
                            )
                        if (
                            restored.ocr_settings is None
                            or restored.ocr_settings.load()[0] != expected_ocr_settings
                            or restored.ocr_importing is None
                            or restored.ocr_importing.confirm_many(
                                (batch_selection,), command_id=batch_command
                            )
                            != batch_result
                        ):
                            raise RuntimeError("Настройки или пакет OCR не восстановлены.")
                        restored.check_integrity()
                    if (
                        restored.ledger is None
                        or Analytics(restored.ledger, restored.mutex, restored.ensure_open).report(
                            ReportPeriod(today, today)
                        )
                        != expected_report
                    ):
                        raise RuntimeError("Отчёт после восстановления отличается от исходного.")
                    with _step(report, "check_financial_history"):
                        if (
                            restored.ledger is None
                            or len(restored.ledger.operations()) != 5
                            or len(restored.ledger.history(spending.id)) != 2
                            or restored.ledger.history(spending.id)[-1].draft.parts
                            != (
                                OperationPart(expenses[0], Money(1000)),
                                OperationPart(expenses[1], Money(2000)),
                            )
                            or not restored.ledger.history(incoming.id)[-1].is_deleted
                            or restored.ledger.history(returned.id)[-1].draft != returned.draft
                            or next(
                                candidate.amount_remaining
                                for candidate in restored.ledger.refund_candidates()
                                if candidate.expense.id == spending.id
                            )
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
                            window = MainWindow(ocr_recognitions=ocr_recognitions)
                            window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
                            try:
                                window.activate_session(background)
                                if ocr_recognitions and not any(
                                    action.text() == "Распознать изображение…"
                                    and action.isEnabled()
                                    for action in window._ocr_actions
                                ):
                                    raise RuntimeError("Команда автоматического OCR недоступна.")
                                window.show()
                                app.processEvents()
                                if (
                                    window.account_model.rowCount() != 2
                                    or window.operation_model.rowCount() != 5
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
        with _step(report, "preview_ocr_without_engine"):
            from personal_finance.bootstrap.ocr_self_test import check_packaged_ocr_preview

            load_runtime_manifest()
            check_packaged_ocr_preview()
        if ocr_recognitions:
            with _step(report, "recognize_local_synthetic_image"):
                from personal_finance.bootstrap.ocr_self_test import check_local_ocr_runtime

                check_local_ocr_runtime(ocr_recognitions)
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
