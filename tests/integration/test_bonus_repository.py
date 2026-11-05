from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from personal_finance.application.bonuses import Bonuses
from personal_finance.application.vault import create_vault
from personal_finance.domain.bonuses import BonusDraft, BonusKind
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import CategoryKind
from personal_finance.infrastructure.persistence.bonus_repository import SqlBonusRepository
from personal_finance.infrastructure.persistence.migrations.versions import v0014_bonuses
from tests.conftest import PASSWORD
from tests.integration.test_generic_ocr_migration import staging_connection


@pytest.fixture
def storage(tmp_path, monkeypatch):
    from personal_finance.application import vault
    from personal_finance.infrastructure.persistence.migration_runner import initialize_schema

    with monkeypatch.context() as patch:
        patch.setattr(vault, "initialize_schema", lambda e: initialize_schema(e, target="0013"))
        session, _ = create_vault(tmp_path / "synthetic.pfvault", "Синтетические бонусы", PASSWORD)
    try:
        with staging_connection(session.engine):
            v0014_bonuses.upgrade()
        repo = SqlBonusRepository(
            session.engine, session.book_id, session.replica_id, session.mutex
        )
        yield session, Bonuses(repo), repo
    finally:
        session.close()


def test_separate_balances_categories_history_and_unchanged_money(storage):
    session, service, repo = storage
    money = session.ledger.operations(), session.ledger.balances()
    key = (session.root / "data/key.json").read_bytes()
    program = service.save_program("Вымышленная программа")
    category = next(v for v in session.catalog.categories() if v.kind is CategoryKind.EXPENSE)
    first = service.save_entry(
        BonusDraft(program.id, date(2026, 10, 1), BonusKind.ACCRUAL, 125, category.id)
    )
    service.save_entry(
        BonusDraft(program.id, date(2026, 10, 2), BonusKind.SPENDING, -25, category.id)
    )
    assert service.balances()[program.id] == 100
    report = service.category_report(date(2026, 10, 1), date(2026, 10, 31))
    assert len(report) == 1 and report[0].accrued_minor == 125 and report[0].spent_minor == 25
    changed = service.save_entry(
        replace(first.draft, units_minor=225),
        identifier=first.id,
        expected_revision_id=first.revision_id,
    )
    assert len(service.history(first.id)) == 2 and service.balances()[program.id] == 200
    service.save_entry(
        changed.draft,
        identifier=changed.id,
        expected_revision_id=changed.revision_id,
        is_deleted=True,
    )
    assert service.balances()[program.id] == -25
    assert (session.ledger.operations(), session.ledger.balances()) == money
    assert (session.root / "data/key.json").read_bytes() == key
    repo.check_integrity()


def test_repeat_commands_sources_and_stale_updates(storage):
    _, service, _ = storage
    command = str(uuid4())
    program = service.save_program("Программа", command_id=command)
    assert service.save_program("Программа", command_id=command) == program
    with pytest.raises(ValidationError):
        service.save_program("Другая", command_id=command)
    draft = BonusDraft(program.id, date(2026, 10, 1), BonusKind.ACCRUAL, 100, source_key="a" * 64)
    c = str(uuid4())
    entry = service.save_entry(draft, command_id=c)
    assert service.save_entry(draft, command_id=c) == entry
    with pytest.raises(ValidationError):
        service.save_entry(draft)
    new = service.save_entry(
        replace(draft, units_minor=200), identifier=entry.id, expected_revision_id=entry.revision_id
    )
    with pytest.raises(ValidationError):
        service.save_entry(draft, identifier=entry.id, expected_revision_id=entry.revision_id)
    assert service.balances()[program.id] == 200
    assert len(service.history(new.id)) == 2
    service.check_integrity()


def test_archive_and_invalid_links(storage):
    _, service, _ = storage
    p = service.save_program("Программа")
    with pytest.raises(ValidationError):
        service.save_program("программа")
    with pytest.raises(ValidationError):
        service.save_entry(BonusDraft(str(uuid4()), date(2026, 10, 1), BonusKind.ACCRUAL, 100))
    with pytest.raises(ValidationError):
        service.save_entry(
            BonusDraft(p.id, date(2026, 10, 1), BonusKind.ACCRUAL, 100, category_id=str(uuid4()))
        )
    p = service.save_program(
        p.name, identifier=p.id, expected_revision_id=p.revision_id, is_deleted=True
    )
    assert not service.programs() and service.balances()[p.id] == 0
    with pytest.raises(ValidationError):
        service.save_entry(BonusDraft(p.id, date(2026, 10, 1), BonusKind.ACCRUAL, 100))
    service.check_integrity()


def test_payload_immutable_and_direct_wrong_kind_rejected(storage):
    session, service, _ = storage
    p = service.save_program("Программа")
    with pytest.raises(IntegrityError), session.engine.begin() as c:
        c.execute(
            text("UPDATE bonus_program_revisions SET name='Подмена' WHERE revision_id=:r"),
            {"r": p.revision_id},
        )
    with pytest.raises(IntegrityError), session.engine.begin() as c:
        c.execute(
            text("DELETE FROM bonus_program_revisions WHERE revision_id=:r"), {"r": p.revision_id}
        )
    service.check_integrity()


def test_filled_ordinary_migration_rejected(storage):
    session, _, _ = storage
    # Новая схема уже есть; повторный вызов при включённых FK обязан остановиться до DDL.
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with (
        session.engine.begin() as c,
        Operations.context(MigrationContext.configure(c)),
        pytest.raises(RuntimeError, match="промежуточной"),
    ):
        v0014_bonuses.upgrade()


def test_bonus_batch_atomic_repeat_and_group(storage):
    session, service, _ = storage
    p = service.save_program("Пакет")
    values = (
        BonusDraft(p.id, date(2026, 10, 1), BonusKind.ACCRUAL, 125, source_key="1" * 64),
        BonusDraft(p.id, date(2026, 10, 2), BonusKind.ACCRUAL, 275, source_key="2" * 64),
    )
    command = str(uuid4())
    first = service.save_entries(values, command_id=command)
    assert service.save_entries(values, command_id=command) == first
    assert service.balances()[p.id] == 400 and len(service.entries()) == 2
    with session.engine.connect() as c:
        groups = (
            c.execute(
                text("SELECT group_id FROM entity_revisions WHERE id IN (:a,:b)"),
                {"a": first[0].revision_id, "b": first[1].revision_id},
            )
            .scalars()
            .all()
        )
        assert len(set(groups)) == 1
    with pytest.raises(ValidationError):
        service.save_entries(values[:1], command_id=command)
    service.check_integrity()


def test_bonus_batch_failure_rolls_back_every_row(storage):
    session, service, _ = storage
    p = service.save_program("Пакет")
    first = BonusDraft(p.id, date(2026, 10, 1), BonusKind.ACCRUAL, 100, source_key="3" * 64)
    second = replace(first, program_id=str(uuid4()), source_key="4" * 64)
    with session.engine.connect() as c:
        before = c.execute(text("SELECT count(*) FROM change_log")).scalar_one()
    with pytest.raises(ValidationError):
        service.save_entries((first, second))
    assert service.entries() == [] and service.balances()[p.id] == 0
    with session.engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM change_log")).scalar_one() == before
    service.check_integrity()


def test_batch_rejects_previously_used_child_command_without_partial_write(storage):
    from uuid import UUID, uuid5

    _, service, _ = storage
    p = service.save_program("Программа")
    draft = BonusDraft(p.id, date(2026, 10, 1), BonusKind.ACCRUAL, 100)
    command = str(uuid4())
    child = str(uuid5(UUID(command), "0"))
    service.save_entry(draft, command_id=child)
    before = service.entries()
    with pytest.raises(ValidationError, match="строки пакета"):
        service.save_entries((draft,), command_id=command)
    assert service.entries() == before and service.balances()[p.id] == 100
    service.check_integrity()
