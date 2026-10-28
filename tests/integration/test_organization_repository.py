import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from personal_finance.application import vault as vault_module
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.domain.periods import AccountingMonth
from personal_finance.domain.query import OperationQuery
from personal_finance.infrastructure.backup.container import (
    create_backup,
    restore_backup,
    verify_backup,
)
from personal_finance.infrastructure.persistence import migration_runner
from personal_finance.infrastructure.persistence.ledger_repository import SqlLedgerRepository
from personal_finance.infrastructure.persistence.organization_repository import (
    SqlOrganizationRepository,
)
from tests.conftest import BACKUP_PASSWORD, PASSWORD

DAY = date(2026, 1, 15)


@pytest.fixture
def organized(session):
    tag = session.organization.add_tag("Отпуск")
    account = session.catalog.add_account("Наличные", AccountKind.CASH)
    category = next(c.id for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE)
    operation = session.ledger.add(
        OperationDraft(
            OperationKind.EXPENSE, DAY, account.id, Money(1000), category, tag_ids=(tag.id,)
        )
    )
    return tag, account, operation


def counts(session):
    with session.engine.connect() as c:
        return [
            c.execute(text("SELECT count(*) FROM " + name)).scalar_one()
            for name in (
                "entities",
                "entity_revisions",
                "revision_parents",
                "revision_heads",
                "change_groups",
                "change_log",
                "command_receipts",
                "postings",
                "operation_tags",
                "tag_revisions",
                "saved_filter_revisions",
            )
        ] + list(c.execute(text("SELECT sequence FROM replicas ORDER BY id")).scalars())


def test_tag_rename_archive_restore_preserves_operation_and_balance(session, organized):
    tag, _, operation = organized
    before = session.ledger.operations(), session.ledger.balances()
    renamed = session.organization.rename_tag(tag.id, tag.revision_id, "Поездка")
    archived = session.organization.archive_tag(tag.id, renamed.revision_id)
    assert session.organization.tags() == []
    assert session.organization.tags(include_archived=True) == [archived]
    assert session.ledger.search(OperationQuery(tag_ids=(tag.id,))) == [operation]
    restored = session.organization.archive_tag(tag.id, archived.revision_id, archived=False)
    assert session.organization.history("tag", tag.id) == [tag, renamed, archived, restored]
    assert (session.ledger.operations(), session.ledger.balances()) == before
    session.check_integrity()


def test_name_uniqueness_uses_russian_casefold_and_archive_allows_reuse(session):
    first = session.organization.add_tag("ОТПУСК")
    with pytest.raises(ValidationError, match="названием"):
        session.organization.add_tag("отпуск")
    archived = session.organization.archive_tag(first.id, first.revision_id)
    other = session.organization.add_tag("Отпуск")
    with pytest.raises(ValidationError):
        session.organization.archive_tag(first.id, archived.revision_id, archived=False)
    assert session.organization.tags() == [other]
    session.check_integrity()


def test_filter_save_replace_archive_restore_and_query_preserve_versions(session, organized):
    tag, account, operation = organized
    query = OperationQuery(
        start=DAY, end=DAY, account_ids=(account.id,), tag_ids=(tag.id,), text=""
    )
    saved = session.organization.save_filter("Поездки", query)
    corrected = session.organization.save_filter(
        "Поездки",
        replace(query, text="другое"),
        identifier=saved.id,
        expected_revision_id=saved.revision_id,
    )
    assert session.ledger.search(saved.query) == [operation]
    assert session.ledger.search(corrected.query) == []
    archived = session.organization.archive_filter(saved.id, corrected.revision_id)
    assert session.organization.filters() == []
    restored = session.organization.archive_filter(saved.id, archived.revision_id, archived=False)
    assert session.organization.filters() == [restored]
    assert session.organization.history("saved_filter", saved.id) == [
        saved,
        corrected,
        archived,
        restored,
    ]
    session.check_integrity()


def test_archived_tag_can_be_kept_or_removed_but_not_assigned_anew(session, organized):
    tag, _, operation = organized
    session.organization.archive_tag(tag.id, tag.revision_id)
    corrected = session.ledger.replace(
        operation.id, operation.revision_id, replace(operation.draft, note="Правка")
    )
    assert corrected.draft.tag_ids == (tag.id,)
    with pytest.raises(ValidationError, match="архиве"):
        session.ledger.add(operation.draft)
    removed = session.ledger.replace(
        corrected.id, corrected.revision_id, replace(corrected.draft, tag_ids=())
    )
    with pytest.raises(ValidationError):
        session.ledger.replace(removed.id, removed.revision_id, corrected.draft)
    assert len(session.ledger.history(operation.id)) == 3
    session.check_integrity()


def test_tagging_closed_operation_requires_explicit_period_reopen(session, organized):
    tag, _, operation = organized
    session.periods.close(AccountingMonth.from_date(DAY))
    with pytest.raises(ValidationError, match="Период"):
        session.ledger.replace(
            operation.id, operation.revision_id, replace(operation.draft, tag_ids=())
        )
    session.organization.rename_tag(tag.id, tag.revision_id, "Название справочника")
    session.check_integrity()


def test_command_replays_do_not_follow_later_names_or_states(session, organized):
    tag, _, _ = organized
    command = str(uuid4())
    archived = session.organization.archive_tag(tag.id, tag.revision_id, command_id=command)
    renamed = session.organization.rename_tag(tag.id, archived.revision_id, "Новое название")
    session.organization.archive_tag(tag.id, renamed.revision_id, archived=False)
    before = counts(session)
    assert session.organization.archive_tag(tag.id, tag.revision_id, command_id=command) == archived
    assert counts(session) == before
    with pytest.raises(ValidationError):
        session.organization.archive_tag(
            tag.id, tag.revision_id, archived=False, command_id=command
        )


def test_filter_command_replay_uses_original_query_after_later_edit(session, organized):
    tag, _, _ = organized
    saved = session.organization.save_filter("Фильтр", OperationQuery(tag_ids=(tag.id,)))
    command = str(uuid4())
    archived = session.organization.archive_filter(saved.id, saved.revision_id, command_id=command)
    renamed = session.organization.save_filter(
        "Другой фильтр",
        OperationQuery(),
        identifier=saved.id,
        expected_revision_id=archived.revision_id,
    )
    before = counts(session)
    assert (
        session.organization.archive_filter(saved.id, saved.revision_id, command_id=command)
        == archived
    )
    assert counts(session) == before and session.organization.filters() == [renamed]


def test_create_replay_and_stale_revision(session):
    command = str(uuid4())
    tag = session.organization.add_tag("Тест", command_id=command)
    assert session.organization.add_tag("Тест", command_id=command) == tag
    renamed = session.organization.rename_tag(tag.id, tag.revision_id, "Другое")
    with pytest.raises(ValidationError):
        session.organization.rename_tag(tag.id, tag.revision_id, "Старый запрос")
    assert session.organization.tags() == [renamed]


@pytest.mark.parametrize("field", ["account_ids", "category_ids", "institution_ids", "tag_ids"])
def test_saved_filter_rejects_unknown_references_atomically(session, field):
    before = counts(session)
    with pytest.raises(ValidationError):
        session.organization.save_filter("Неверный", OperationQuery(**{field: (str(uuid4()),)}))
    assert counts(session) == before


def test_operation_rejects_foreign_or_non_tag_entity(session, organized, tmp_path):
    tag, account, operation = organized
    other, _ = vault_module.create_vault(
        tmp_path / "foreign.pfvault", "Чужая тестовая книга", PASSWORD
    )
    try:
        foreign = other.organization.add_tag("Чужой тег")
        for identifier in (account.id, foreign.id):
            with pytest.raises(ValidationError):
                session.ledger.add(replace(operation.draft, tag_ids=(identifier,)))
        session.check_integrity()
    finally:
        other.close()


def test_definitions_and_operation_membership_are_immutable(session, organized):
    tag, _, operation = organized
    saved = session.organization.save_filter("Фильтр", OperationQuery(tag_ids=(tag.id,)))
    statements = [
        ("UPDATE tag_revisions SET name='x' WHERE revision_id=:r", tag.revision_id),
        ("DELETE FROM saved_filter_revisions WHERE revision_id=:r", saved.revision_id),
        ("DELETE FROM operation_tags WHERE revision_id=:r", operation.revision_id),
        ("INSERT INTO operation_tags(revision_id,tag_id) VALUES (:r,:tag)", operation.revision_id),
    ]
    for sql, revision in statements:
        with pytest.raises(DatabaseError), session.engine.begin() as c:
            c.execute(text(sql), {"r": revision, "tag": tag.id})
    session.check_integrity()


@pytest.mark.parametrize("definition", ["tag", "filter"])
def test_definition_failure_rolls_back_whole_group(session, monkeypatch, definition):
    insert = SqlOrganizationRepository._insert_payload

    def fail(self, *args):
        insert(self, *args)
        raise OSError("Синтетический отказ")

    before = counts(session)
    monkeypatch.setattr(SqlOrganizationRepository, "_insert_payload", fail)
    with pytest.raises(OSError):
        if definition == "tag":
            session.organization.add_tag("Тест")
        else:
            session.organization.save_filter("Тест", OperationQuery())
    assert counts(session) == before
    session.check_integrity()


def test_operation_tag_failure_rolls_back_postings_and_group(session, organized, monkeypatch):
    _, _, operation = organized
    insert = SqlLedgerRepository._insert_tags

    def fail(self, *args):
        insert(self, *args)
        raise OSError("Синтетический отказ тегов операции")

    before = counts(session)
    monkeypatch.setattr(SqlLedgerRepository, "_insert_tags", fail)
    with pytest.raises(OSError):
        session.ledger.add(operation.draft)
    assert counts(session) == before
    session.check_integrity()


def test_two_renames_accept_one_expected_version(session, organized):
    tag, _, _ = organized

    def rename(name):
        try:
            return session.organization.rename_tag(tag.id, tag.revision_id, name)
        except ValidationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        values = list(workers.map(rename, ("Первое", "Второе")))
    assert sum(v is not None for v in values) == 1
    assert len(session.organization.history("tag", tag.id)) == 2
    session.check_integrity()


def test_backup_restores_tag_filter_history_and_search(session, organized, tmp_path):
    tag, _, operation = organized
    renamed = session.organization.rename_tag(tag.id, tag.revision_id, "Поездка")
    saved = session.organization.save_filter("Поездки", OperationQuery(tag_ids=(tag.id,)))
    backup = create_backup(session, tmp_path / "copy.pfbackup", BACKUP_PASSWORD)
    restored, _ = restore_backup(backup, tmp_path / "restored.pfvault", BACKUP_PASSWORD, PASSWORD)
    try:
        assert restored.organization.history("tag", tag.id) == [tag, renamed]
        assert restored.organization.filters() == [saved]
        assert restored.ledger.search(saved.query) == [operation]
        assert restored.replica_id != session.replica_id
        restored.check_integrity()
    finally:
        restored.close()


def test_source6_is_unchanged_and_only_restored_copy_supports_tags(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module,
            "initialize_schema",
            lambda e: migration_runner.initialize_schema(e, target="0006"),
        )
        source, _ = vault_module.create_vault(
            tmp_path / "old6.pfvault", "Старая синтетическая книга", PASSWORD
        )
    restored = None
    try:
        account = source.catalog.add_account("Счёт", AccountKind.CASH)
        command = str(uuid4())
        value = OperationDraft(OperationKind.OPENING, DAY, account.id, Money(123))
        opening = source.ledger.add(value, command_id=command)
        source.periods.close(AccountingMonth.from_date(DAY))
        root = source.root
        source.close()
        files = [root / "data" / name for name in ("ledger.sqlite3", "key.json")]
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
        source = vault_module.open_vault(root, PASSWORD)
        assert source.organization is None and not source.ledger.supports_tags
        assert source.ledger.search(OperationQuery(account_ids=(account.id,))) == [opening]
        backup = create_backup(source, tmp_path / "old6.pfbackup", BACKUP_PASSWORD)
        assert verify_backup(backup, BACKUP_PASSWORD)["schema"] == "0006"
        restored, _ = restore_backup(backup, tmp_path / "new8.pfvault", BACKUP_PASSWORD, PASSWORD)
        assert restored.schema_revision == "0011" and restored.organization is not None
        assert restored.ledger.add(value, command_id=command) == opening
        restored.organization.add_tag("Новый тег")
        assert restored.periods.states()[0].is_closed
        assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in files] == hashes
        restored.check_integrity()
    finally:
        source.close()
        if restored:
            restored.close()


def test_closed_session_rejects_organization_and_search(session, organized):
    _, _, _ = organized
    organization, ledger = session.organization, session.ledger
    session.close()
    for action in (
        organization.tags,
        organization.filters,
        lambda: organization.add_tag("Тест"),
        lambda: ledger.search(OperationQuery()),
    ):
        with pytest.raises(VaultError):
            action()


def test_sql_guard_rejects_non_tag_even_if_application_validation_is_bypassed(
    session, organized, monkeypatch
):
    _, account, operation = organized
    before = counts(session)
    monkeypatch.setattr(SqlLedgerRepository, "_require_tags", lambda *args, **kwargs: None)
    with pytest.raises(DatabaseError):
        session.ledger.add(replace(operation.draft, tag_ids=(account.id,)))
    assert counts(session) == before


def test_tag_order_is_canonical_for_command_receipt_and_does_not_duplicate_postings(
    session, organized
):
    tag, account, operation = organized
    other = session.organization.add_tag("Второй")
    command = str(uuid4())
    value = replace(operation.draft, tag_ids=(tag.id, other.id))
    created = session.ledger.add(value, command_id=command)
    before = counts(session)
    assert (
        session.ledger.add(replace(value, tag_ids=(other.id, tag.id)), command_id=command)
        == created
    )
    assert counts(session) == before
    assert {b.account_id: b.amount for b in session.ledger.balances()}[account.id] == Money(-2000)
    session.check_integrity()


def test_saved_filter_may_reference_archived_tag_to_find_existing_operations(session, organized):
    tag, _, operation = organized
    session.organization.archive_tag(tag.id, tag.revision_id)
    saved = session.organization.save_filter("Архивный тег", OperationQuery(tag_ids=(tag.id,)))
    assert session.ledger.search(saved.query) == [operation]
    session.check_integrity()
