import socket
from datetime import date

from personal_finance.application.vault import create_vault
from personal_finance.domain.imports import ImportSelection, ImportStatus
from personal_finance.domain.ledger import OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.domain.query import OperationQuery
from personal_finance.infrastructure.backup.container import create_backup, restore_backup
from personal_finance.infrastructure.importing.statements import CsvMapping


def test_book_catalog_journal_and_backup_do_not_use_network(tmp_path, monkeypatch) -> None:
    def blocked(*args, **kwargs):
        raise AssertionError("Сетевая операция запрещена в автономном сценарии")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    source, _ = create_vault(tmp_path / "offline.pfvault", "Офлайн", "пароль автономной книги")
    try:
        account = source.catalog.add_account("Наличные", AccountKind.CASH)
        categories = [c for c in source.catalog.categories() if c.kind == CategoryKind.INCOME]
        assert source.organization is not None
        tag = source.organization.add_tag("Автономный тег")
        assert source.ledger is not None
        operation = source.ledger.add(
            OperationDraft(
                OperationKind.INCOME,
                date(2026, 10, 26),
                account.id,
                Money(12345),
                tag_ids=(tag.id,),
                parts=(
                    OperationPart(categories[0].id, Money(10000)),
                    OperationPart(categories[1].id, Money(2345)),
                ),
            )
        )
        saved = source.organization.save_filter(
            "Автономный фильтр", OperationQuery(tag_ids=(tag.id,))
        )
        assert source.ledger.search(saved.query) == [operation]
        assert source.importing is not None
        document = source.importing.parse_csv(
            b"date;amount;id\n2026-10-26;1.00;offline-id\n",
            CsvMapping(
                "date", "amount", id_column="id", date_format="%Y-%m-%d", decimal_separator="."
            ),
        )
        imported = source.importing.confirm(
            document,
            account.id,
            (
                ImportSelection(
                    1,
                    OperationDraft(
                        OperationKind.INCOME,
                        date(2026, 10, 26),
                        account.id,
                        Money(100),
                        categories[0].id,
                    ),
                ),
            ),
        )
        backup = create_backup(source, tmp_path / "offline.pfbackup", "пароль автономной копии")
        restored, _ = restore_backup(
            backup,
            tmp_path / "restored.pfvault",
            "пароль автономной копии",
            "новый автономный пароль",
        )
        try:
            assert restored.ledger is not None
            assert {op.id for op in restored.ledger.operations()} == {
                operation.id,
                imported.operations[0].id,
            }
            assert restored.ledger.balances(date(2026, 10, 26))[0].amount == Money(12445)
            assert restored.organization is not None
            assert restored.organization.filters() == [saved]
            assert restored.ledger.search(saved.query) == [operation]
            assert restored.importing is not None
            assert (
                restored.importing.preview(document, account.id).lines[0].status
                == ImportStatus.DUPLICATE
            )
            restored.check_integrity()
        finally:
            restored.close()
    finally:
        source.close()
