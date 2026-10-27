import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from personal_finance.application import vault as vault_module
from personal_finance.application.classification import Classification
from personal_finance.domain.classification import (
    CategoryRule,
    ClassificationInput,
    ClassificationStatus,
)
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.domain.periods import AccountingMonth
from personal_finance.infrastructure.backup.container import (
    create_backup,
    restore_backup,
    verify_backup,
)
from personal_finance.infrastructure.persistence.classification_repository import (
    SqlClassificationRepository,
)
from personal_finance.infrastructure.persistence.migration_runner import initialize_schema
from tests.conftest import BACKUP_PASSWORD, PASSWORD
from tests.integration.test_category_rule_migration import upgrade_rules


@pytest.fixture
def rules(session):
    upgrade_rules(session.engine)
    repository = SqlClassificationRepository(
        session.engine, session.book_id, session.replica_id, session.mutex
    )
    service = Classification(repository, session.mutex, session.ensure_open)
    account = session.catalog.add_account("Счёт правил", AccountKind.CASH)
    categories = [c for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE]
    category, other = categories[0].id, categories[1].id
    session.ledger.add(
        OperationDraft(OperationKind.EXPENSE, date(2026, 1, 15), account.id, Money(100), category)
    )
    rule = CategoryRule(
        str(uuid4()),
        "Продукты по тексту",
        OperationKind.EXPENSE,
        "магазин",
        category,
        10,
        account.id,
    )
    return service, repository, rule, other


def state(session):
    with session.engine.connect() as connection:
        return [
            connection.execute(text("SELECT count(*) FROM " + table)).scalar_one()
            for table in (
                "entities",
                "entity_revisions",
                "revision_heads",
                "revision_parents",
                "change_groups",
                "change_log",
                "command_receipts",
                "category_rule_revisions",
                "postings",
            )
        ] + list(connection.execute(text("SELECT sequence FROM replicas ORDER BY id")).scalars())


def test_rule_save_edit_disable_enable_keeps_history_and_financial_facts(session, rules):
    service, _, rule, other = rules
    financial = session.ledger.operations(), session.ledger.balances()
    first = service.save(rule)
    edited = service.save(
        replace(rule, priority=20, category_id=other), expected_revision_id=first.revision_id
    )
    disabled = service.save(
        replace(edited.rule, enabled=False), expected_revision_id=edited.revision_id
    )
    assert service.rules() == [] and service.rules(include_disabled=True) == [disabled]
    enabled = service.save(
        replace(disabled.rule, enabled=True), expected_revision_id=disabled.revision_id
    )
    assert service.history(rule.id) == [first, edited, disabled, enabled]
    assert service.rules() == [enabled]
    assert (session.ledger.operations(), session.ledger.balances()) == financial
    service.check_integrity()
    session.check_integrity()


def test_repeat_command_returns_original_version_after_later_edit_and_conflict_is_rejected(
    session, rules
):
    service, _, rule, _ = rules
    command_id = str(uuid4())
    first = service.save(rule, command_id=command_id)
    service.save(replace(rule, priority=20), expected_revision_id=first.revision_id)
    before = state(session)
    assert service.save(rule, command_id=command_id) == first
    with pytest.raises(ValidationError, match="других данных"):
        service.save(replace(rule, priority=11), command_id=command_id)
    assert state(session) == before


def test_stale_edit_and_reusing_entity_id_do_not_write_any_rows(session, rules):
    service, _, rule, _ = rules
    first = service.save(rule)
    service.save(replace(rule, priority=20), expected_revision_id=first.revision_id)
    before = state(session)
    with pytest.raises(ValidationError, match="изменено"):
        service.save(replace(rule, priority=30), expected_revision_id=first.revision_id)
    with pytest.raises(ValidationError, match="уже существует"):
        service.save(rule)
    with pytest.raises(ValidationError, match="уже существует"):
        service.save(replace(rule, id=rule.account_id))
    assert state(session) == before


def test_bad_references_and_initially_disabled_rule_are_rejected(session, rules):
    service, _, rule, _ = rules
    income = next(c.id for c in session.catalog.categories() if c.kind == CategoryKind.INCOME)
    before = state(session)
    for bad in (
        replace(rule, category_id=str(uuid4())),
        replace(rule, category_id=income),
        replace(rule, account_id=str(uuid4())),
        replace(rule, enabled=False),
    ):
        with pytest.raises(ValidationError):
            service.save(bad)
    assert state(session) == before


def test_active_name_casefold_and_disabled_name_reuse_are_controlled(session, rules):
    service, _, rule, _ = rules
    first = service.save(rule)
    other = replace(rule, id=str(uuid4()), name=rule.name.upper())
    with pytest.raises(ValidationError, match="названием"):
        service.save(other)
    disabled = service.save(replace(rule, enabled=False), expected_revision_id=first.revision_id)
    active = service.save(other)
    before = state(session)
    with pytest.raises(ValidationError, match="названием"):
        service.save(rule, expected_revision_id=disabled.revision_id)
    assert service.rules() == [active] and state(session) == before


def test_failure_mid_command_rolls_back_rule_history_and_replica_sequence(
    session, rules, monkeypatch
):
    service, repository, rule, _ = rules
    before = state(session)
    insert = repository._insert_payload

    def fail(*args):
        insert(*args)
        raise OSError("Синтетический отказ после правила")

    monkeypatch.setattr(repository, "_insert_payload", fail)
    with pytest.raises(OSError, match="Синтетический отказ"):
        service.save(rule)
    assert state(session) == before and service.rules(include_disabled=True) == []


def test_sql_guards_reject_wrong_category_and_keep_sealed_payload(session, rules, monkeypatch):
    service, repository, rule, _ = rules
    first = service.save(rule)
    before = state(session)
    with pytest.raises(DatabaseError, match="sealed"), session.engine.begin() as connection:
        connection.execute(
            text("UPDATE category_rule_revisions SET priority=999 WHERE revision_id=:id"),
            {"id": first.revision_id},
        )
    income = next(c.id for c in session.catalog.categories() if c.kind == CategoryKind.INCOME)
    monkeypatch.setattr(repository, "_require_references", lambda *args: None)
    with pytest.raises(DatabaseError, match="invalid category rule references"):
        service.save(replace(rule, category_id=income), expected_revision_id=first.revision_id)
    assert state(session) == before
    service.check_integrity()


def test_concurrent_replacements_accept_one_expected_revision(session, rules):
    service, _, rule, _ = rules
    first = service.save(rule)

    def edit(priority):
        try:
            return service.save(
                replace(rule, priority=priority), expected_revision_id=first.revision_id
            )
        except ValidationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, (20, 30)))
    assert sum(r is not None for r in results) == 1 and len(service.history(rule.id)) == 2
    service.check_integrity()


def test_suggestion_snapshot_is_read_only_and_manual_choice_wins(session, rules):
    service, _, rule, other = rules
    first = service.save(rule)
    value = ClassificationInput(OperationKind.EXPENSE, rule.account_id, "МАГАЗИН тест")
    before = state(session), session.ledger.operations(), session.ledger.balances()
    result = service.suggest(value)
    assert result.category_id == rule.category_id and result.rule_ids == (rule.id,)
    assert (
        service.suggest(replace(value, manual_category_id=other)).status
        == ClassificationStatus.MANUAL
    )
    assert (state(session), session.ledger.operations(), session.ledger.balances()) == before
    disabled = service.save(replace(rule, enabled=False), expected_revision_id=first.revision_id)
    assert (
        not disabled.rule.enabled and service.suggest(value).status == ClassificationStatus.NO_MATCH
    )


def test_closed_service_refuses_reads_and_writes_and_foreign_history_is_hidden(session, rules):
    service, _, rule, _ = rules
    with pytest.raises(ValidationError, match="История"):
        service.history(str(uuid4()))
    session.close()
    for action in (
        lambda: service.rules(),
        lambda: service.save(rule),
        lambda: service.history(rule.id),
        lambda: service.check_integrity(),
    ):
        with pytest.raises(VaultError, match="закрыта"):
            action()


def test_backup_restores_rules_history_and_decisions_with_new_replica(session, rules, tmp_path):
    service, _, rule, other = rules
    first = service.save(rule)
    changed = service.save(replace(rule, category_id=other), expected_revision_id=first.revision_id)
    backup = create_backup(session, tmp_path / "rules.pfbackup", BACKUP_PASSWORD)
    restored, _ = restore_backup(backup, tmp_path / "restored.pfvault", BACKUP_PASSWORD, PASSWORD)
    try:
        assert restored.replica_id != session.replica_id
        assert restored.classification is not None
        assert restored.classification.history(rule.id) == [first, changed]
        assert (
            restored.classification.suggest(
                ClassificationInput(OperationKind.EXPENSE, rule.account_id, "МАГАЗИН")
            ).category_id
            == other
        )
        assert restored.ledger.operations() == session.ledger.operations()
        restored.check_integrity()
    finally:
        restored.close()


def test_old8_is_unchanged_and_only_restored_copy_gets_rules(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module, "initialize_schema", lambda e: initialize_schema(e, target="0008")
        )
        old, _ = vault_module.create_vault(tmp_path / "old8.pfvault", "Старая книга 0008", PASSWORD)
    restored = None
    try:
        account = old.catalog.add_account("Счёт старой книги", AccountKind.CASH)
        category = next(c.id for c in old.catalog.categories() if c.kind == CategoryKind.EXPENSE)
        operation = old.ledger.add(
            OperationDraft(
                OperationKind.EXPENSE, date(2026, 1, 15), account.id, Money(100), category
            )
        )
        root = old.root
        old.close()
        files = [root / "data" / name for name in ("ledger.sqlite3", "key.json")]
        original = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
        old = vault_module.open_vault(root, PASSWORD)
        assert old.classification is None and old.schema_revision == "0008"
        backup = create_backup(old, tmp_path / "old8.pfbackup", BACKUP_PASSWORD)
        assert verify_backup(backup, BACKUP_PASSWORD)["schema"] == "0008"
        restored, _ = restore_backup(backup, tmp_path / "new9.pfvault", BACKUP_PASSWORD, PASSWORD)
        assert restored.classification is not None and restored.schema_revision == "0009"
        assert restored.ledger.operations() == [operation]
        restored.classification.save(
            CategoryRule(str(uuid4()), "Новое правило", OperationKind.EXPENSE, "тест", category)
        )
        assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in files] == original
        restored.check_integrity()
    finally:
        old.close()
        if restored:
            restored.close()


def test_snapshot_blocks_concurrent_rule_edit_and_keeps_reviewed_definition(
    session, rules, monkeypatch
):
    service, repository, rule, other = rules
    first = service.save(rule)
    ready, release, attempted = Event(), Event(), Event()
    read = repository._rules

    def pause(*args):
        result = read(*args)
        ready.set()
        assert release.wait(10)
        return result

    def edit():
        attempted.set()
        return service.save(
            replace(rule, category_id=other), expected_revision_id=first.revision_id
        )

    monkeypatch.setattr(repository, "_rules", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(service.snapshot)
        try:
            assert ready.wait(10)
            writing = pool.submit(edit)
            assert attempted.wait(10) and not writing.done()
        finally:
            release.set()
        snapshot = reading.result(timeout=15)
        writing.result(timeout=15)
    assert (
        snapshot.suggest(
            ClassificationInput(OperationKind.EXPENSE, rule.account_id, "магазин")
        ).category_id
        == rule.category_id
    )
    assert service.rules()[0].rule.category_id == other


def test_closed_month_does_not_block_nonfinancial_rule_edit(session, rules):
    service, _, rule, _ = rules
    first = service.save(rule)
    session.periods.close(AccountingMonth(2026, 1))
    before = session.ledger.operations(), session.ledger.balances()
    changed = service.save(replace(rule, priority=35), expected_revision_id=first.revision_id)
    assert (
        changed.rule.priority == 35
        and (session.ledger.operations(), session.ledger.balances()) == before
    )
    session.check_integrity()
