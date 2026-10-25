import pytest
from sqlalchemy import text

from personal_finance.domain.errors import UnsupportedFormat
from personal_finance.infrastructure.persistence.migration_runner import initialize_schema


def test_initialization_refuses_existing_book(session) -> None:
    original = session.name
    with pytest.raises(UnsupportedFormat):
        initialize_schema(session.engine)
    with session.engine.connect() as connection:
        assert connection.execute(text("SELECT name FROM books")).scalar_one() == original
    session.check_integrity()


def test_seed_failure_does_not_publish_partial_book(tmp_path, monkeypatch) -> None:
    from personal_finance.application.catalog import Catalog
    from personal_finance.application.vault import create_vault

    original_seed = Catalog.seed

    def fail_after_seed(self):
        original_seed(self)
        raise OSError("проверочный отказ записи")

    monkeypatch.setattr(Catalog, "seed", fail_after_seed)
    marker = tmp_path / "existing.txt"
    marker.write_text("сохранить", encoding="utf-8")
    target = tmp_path / "failed.pfvault"
    with pytest.raises(OSError):
        create_vault(target, "Книга", "длинная тестовая фраза")
    assert not target.exists()
    assert marker.read_text(encoding="utf-8") == "сохранить"
