"""Жизненный цикл локальной финансовой книги."""

import os
import secrets
import tempfile
from contextlib import closing
from pathlib import Path
from threading import RLock
from uuid import uuid4

from filelock import FileLock, Timeout
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from sqlcipher3 import dbapi2 as sqlcipher

from personal_finance.application.catalog import Catalog, utc_now
from personal_finance.application.classification import Classification
from personal_finance.application.exporting import Exporting
from personal_finance.application.importing import Importing
from personal_finance.application.ledger import Ledger
from personal_finance.application.ocr_importing import OcrImporting
from personal_finance.application.organization import Organization
from personal_finance.application.periods import Periods
from personal_finance.domain.errors import VaultError
from personal_finance.domain.models import validate_name
from personal_finance.infrastructure.persistence.catalog_repository import SqlCatalogRepository
from personal_finance.infrastructure.persistence.classification_repository import (
    SqlClassificationRepository,
)
from personal_finance.infrastructure.persistence.database import (
    check_database,
    connect_database,
    encrypted_snapshot,
    make_engine,
)
from personal_finance.infrastructure.persistence.export_repository import SqlExportRepository
from personal_finance.infrastructure.persistence.import_repository import SqlImportRepository
from personal_finance.infrastructure.persistence.ledger_repository import SqlLedgerRepository
from personal_finance.infrastructure.persistence.migration_runner import (
    initialize_schema,
    supported_schema,
)
from personal_finance.infrastructure.persistence.ocr_repository import SqlOcrRepository
from personal_finance.infrastructure.persistence.ocr_settings_repository import SqlOcrSettings
from personal_finance.infrastructure.persistence.organization_repository import (
    SqlOrganizationRepository,
)
from personal_finance.infrastructure.persistence.period_repository import SqlPeriodRepository
from personal_finance.infrastructure.security.keys import (
    MAX_MANIFEST_BYTES,
    make_manifest,
    unlock_manifest,
)


def write_private(path: Path, contents: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(contents)
        output.flush()
        os.fsync(output.fileno())
    sync_directory(path.parent)


def sync_directory(path: Path) -> None:
    """Закрепляет изменение каталога там, где ОС поддерживает fsync каталогов."""
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def publish_data(staged_data: Path, destination: Path) -> None:
    """Резервирование имени исключает перезапись существующей книги."""
    try:
        destination.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise VaultError("Папка назначения уже существует. Выберите новое имя.") from exc
    try:
        staged_data.rename(destination / "data")
    except Exception:
        # Здесь удаляется только только что созданная пустая папка, а не данные пользователя.
        destination.rmdir()
        raise
    sync_directory(destination)
    sync_directory(destination.parent)


class VaultSession:
    def __init__(self, root: Path, book_id: str, key: bytes, lock: FileLock | None = None) -> None:
        self.root = root
        self.book_id = book_id
        self.database_path = root / "data" / "ledger.sqlite3"
        self._key = key
        self._lock = lock
        self._closed = False
        self.mutex = RLock()
        self.engine = make_engine(self.database_path, self.backup_key)
        try:
            self.schema_revision = supported_schema(self.engine)
            with self.engine.connect() as connection:
                book = connection.execute(text("SELECT id,name FROM books")).one()
                if book.id != book_id:
                    raise VaultError("Ключевой файл относится к другой книге.")
                self.name = book.name
                self.replica_id: str = connection.execute(
                    text("SELECT replica_id FROM local_state WHERE id=1")
                ).scalar_one()
            self.catalog = Catalog(
                SqlCatalogRepository(self.engine, book_id, self.replica_id, self.mutex)
            )
            ledger_repository = (
                SqlLedgerRepository(
                    self.engine,
                    book_id,
                    self.replica_id,
                    self.mutex,
                    supports_splits=self.schema_revision
                    in ("0004", "0005", "0006", "0007", "0008", "0009", "0010", "0011"),
                    supports_refunds=self.schema_revision
                    in ("0005", "0006", "0007", "0008", "0009", "0010", "0011"),
                    supports_periods=self.schema_revision
                    in ("0006", "0007", "0008", "0009", "0010", "0011"),
                    supports_tags=self.schema_revision in ("0007", "0008", "0009", "0010", "0011"),
                )
                if self.schema_revision
                in ("0003", "0004", "0005", "0006", "0007", "0008", "0009", "0010", "0011")
                else None
            )
            self.ledger = Ledger(ledger_repository) if ledger_repository is not None else None
            self.exporting = (
                Exporting(
                    SqlExportRepository(self.engine, book_id, self.mutex, ledger_repository),
                    book_id,
                    root,
                    self.mutex,
                    self.ensure_open,
                )
                if ledger_repository is not None
                else None
            )
            self.importing = (
                Importing(SqlImportRepository(self.engine, book_id, self.mutex, ledger_repository))
                if self.schema_revision in ("0008", "0009", "0010", "0011")
                and ledger_repository is not None
                else None
            )
            self.periods = (
                Periods(SqlPeriodRepository(self.engine, book_id, self.replica_id, self.mutex))
                if self.schema_revision in ("0006", "0007", "0008", "0009", "0010", "0011")
                else None
            )
            self.organization = (
                Organization(
                    SqlOrganizationRepository(self.engine, book_id, self.replica_id, self.mutex)
                )
                if self.schema_revision in ("0007", "0008", "0009", "0010", "0011")
                else None
            )
            self.classification = (
                Classification(
                    SqlClassificationRepository(self.engine, book_id, self.replica_id, self.mutex),
                    self.mutex,
                    self.ensure_open,
                )
                if self.schema_revision in ("0009", "0010", "0011")
                else None
            )
            ocr_repository = (
                SqlOcrRepository(
                    self.engine,
                    book_id,
                    self.mutex,
                    ledger_repository,
                    supports_batches=self.schema_revision == "0011",
                )
                if self.schema_revision in ("0010", "0011") and ledger_repository is not None
                else None
            )
            self.ocr_importing = (
                OcrImporting(ocr_repository) if ocr_repository is not None else None
            )
            self.ocr_settings = (
                SqlOcrSettings(self.engine, book_id, self.mutex, ocr_repository)
                if self.schema_revision == "0011" and ocr_repository is not None
                else None
            )
        except Exception:
            self.engine.dispose()
            raise

    def ensure_open(self) -> None:
        if self._closed:
            raise VaultError("Книга закрыта.")

    def check_integrity(self) -> None:
        with self.mutex:
            self.ensure_open()
            check_database(self.database_path, self._key)
            with self.engine.connect() as connection:
                bad: int = connection.execute(
                    text("""SELECT count(*) FROM entities e
                    LEFT JOIN entity_revisions r ON r.id=e.accepted_revision_id
                    WHERE r.id IS NULL OR r.entity_id<>e.id OR r.sealed<>1""")
                ).scalar_one()
                unfinished: int = connection.execute(
                    text("SELECT count(*) FROM entity_revisions WHERE sealed<>1")
                ).scalar_one()
                missing_changes: int = connection.execute(
                    text("""SELECT count(*)
                    FROM entity_revisions r LEFT JOIN change_log c ON c.revision_id=r.id
                    WHERE c.id IS NULL OR c.replica_id<>r.replica_id OR c.sequence<>r.sequence
                    OR c.group_id<>r.group_id""")
                ).scalar_one()
            if bad or unfinished or missing_changes:
                raise VaultError("Проверка истории изменений не пройдена.")
            self.catalog.check_integrity()
            if self.classification is not None:
                self.classification.check_integrity()
            if self.organization is not None:
                self.organization.check_integrity()
            if self.periods is not None:
                self.periods.check_integrity()
            if self.ledger is not None:
                self.ledger.check_integrity()
            if self.importing is not None:
                self.importing.check_integrity()
            if self.ocr_importing is not None:
                self.ocr_importing.check_integrity()
            if self.ocr_settings is not None:
                self.ocr_settings.check_integrity()

    def snapshot(self, target: Path) -> None:
        with self.mutex:
            self.ensure_open()
            self.check_integrity()
            encrypted_snapshot(self.database_path, target, self._key)

    def backup_key(self) -> bytes:
        """Используется только локальным зашифрованным контейнером копии."""
        self.ensure_open()
        return self._key

    def close(self) -> None:
        with self.mutex:
            if self._closed:
                return
            self.engine.dispose()
            self._key = b""
            self._closed = True
            if self._lock:
                self._lock.release()


def open_vault(root: Path, credential: str, *, recovery: bool = False) -> VaultSession:
    root = root.absolute()
    data = root / "data"
    manifest_path = data / "key.json"
    if (
        not data.is_dir()
        or data.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.is_symlink()
        or manifest_path.stat().st_size > MAX_MANIFEST_BYTES
    ):
        raise VaultError("В выбранной папке нет поддерживаемой финансовой книги.")
    lock_path = root / ".session.lock"
    if lock_path.is_symlink():
        raise VaultError("Файл блокировки книги не должен быть ссылкой.")
    # После аварийного завершения Windows может освобождать файловую блокировку
    # с задержкой. Ограниченное ожидание не снимает блокировку живого процесса.
    # Книгу открывает рабочий поток, а закрыть её может поток интерфейса.
    # Состояние системной блокировки должно быть общим для всей сессии.
    lock = FileLock(lock_path, timeout=2, thread_local=False)
    try:
        lock.acquire()
    except Timeout as exc:
        raise VaultError("Книга уже открыта в другом окне или процессе.") from exc
    try:
        book_id, key = unlock_manifest(manifest_path.read_bytes(), credential, recovery=recovery)
        session = VaultSession(root, book_id, key, lock)
        try:
            session.check_integrity()
        except Exception:
            session.close()
            raise
        return session
    except (sqlcipher.DatabaseError, DatabaseError) as exc:
        lock.release()
        raise VaultError(
            "Не удалось прочитать зашифрованную базу. Исходные данные сохранены."
        ) from exc
    except Exception:
        lock.release()
        raise


def create_vault(destination: Path, name: str, password: str) -> tuple[VaultSession, str]:
    destination = destination.absolute()
    name = validate_name(name)
    if destination.exists():
        raise VaultError("Папка назначения уже существует. Выберите новое имя.")
    if not destination.parent.is_dir():
        raise VaultError("Родительская папка не существует.")
    book_id, replica_id = str(uuid4()), str(uuid4())
    key = secrets.token_bytes(32)
    manifest, recovery = make_manifest(book_id, key, password)
    with tempfile.TemporaryDirectory(prefix=".pf-create-", dir=destination.parent) as temporary:
        staged_root = Path(temporary)
        staged_data = staged_root / "data"
        staged_data.mkdir(mode=0o700)
        database = staged_data / "ledger.sqlite3"
        with closing(connect_database(database, key, create=True)) as connection:
            if connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise VaultError("Не удалось включить журнал восстановления WAL.")
        engine = make_engine(database, key)
        try:
            initialize_schema(engine)
            with engine.begin() as connection:
                connection.execute(
                    text("""INSERT INTO books (id,name,currency,created_at)
                    VALUES (:id,:name,'RUB',:created)"""),
                    {"id": book_id, "name": name, "created": utc_now()},
                )
                connection.execute(
                    text("INSERT INTO replicas (id,book_id) VALUES (:id,:book)"),
                    {"id": replica_id, "book": book_id},
                )
                connection.execute(
                    text("INSERT INTO local_state (id,replica_id) VALUES (1,:id)"),
                    {"id": replica_id},
                )
        finally:
            engine.dispose()
        staged_session = VaultSession(staged_root, book_id, key)
        try:
            staged_session.catalog.seed()
            staged_session.check_integrity()
        finally:
            staged_session.close()
        write_private(staged_data / "key.json", manifest)
        publish_data(staged_data, destination)
    return open_vault(destination, password), recovery
