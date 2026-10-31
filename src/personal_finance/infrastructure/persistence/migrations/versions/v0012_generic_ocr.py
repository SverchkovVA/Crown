"""Расширение профиля OCR с точным сохранением старых строк и SQL-защит."""

import re

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def upgrade() -> None:
    connection = op.get_bind()
    count: int = connection.exec_driver_sql("SELECT count(*) FROM ocr_records").scalar_one()
    foreign_keys: int = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
    if count and foreign_keys:
        raise RuntimeError(
            "Заполненная книга обновляется только в промежуточной копии восстановления."
        )
    ddl: str = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ocr_records'"
    ).scalar_one()
    if not isinstance(ddl, str):
        raise RuntimeError("Не найдено ожидаемое определение таблицы OCR.")
    widened, changes = re.subn(
        r"\bbank\s+IN\s*\(\s*'Т-Банк'\s*,\s*'ВТБ'\s*\)",
        "bank IN ('Т-Банк','ВТБ','Любой банк')",
        ddl,
        flags=re.I,
    )
    if changes != 1:
        raise RuntimeError("Ограничение профилей OCR отличается от ожидаемого.")
    staged, changes = re.subn(
        r'\ACREATE TABLE (?:"ocr_records"|ocr_records)(?=\s*\()',
        'CREATE TABLE "ocr_records_0012"',
        widened,
        count=1,
        flags=re.I,
    )
    if changes != 1:
        raise RuntimeError("Имя таблицы OCR отличается от ожидаемого.")
    reference = re.compile(r"\bocr_records\b", re.I)
    object_rows = connection.exec_driver_sql(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE type IN ('trigger','index','view') AND sql IS NOT NULL ORDER BY rowid"
    ).fetchall()
    objects = [
        (str(kind), str(name), str(table), str(sql)) for kind, name, table, sql in object_rows
    ]
    if any(kind == "view" and reference.search(sql) for kind, _, _, sql in objects):
        raise RuntimeError("Неизвестное представление ссылается на OCR; переход остановлен.")
    triggers = [
        (name, sql) for kind, name, _, sql in objects if kind == "trigger" and reference.search(sql)
    ]
    indexes = [
        (name, sql)
        for kind, name, table, sql in objects
        if kind == "index" and table == "ocr_records"
    ]
    columns = ",".join(
        _quoted(str(row[1]))
        for row in connection.exec_driver_sql("PRAGMA table_info(ocr_records)").fetchall()
    )
    op.execute(staged)
    op.execute(f"INSERT INTO ocr_records_0012 ({columns}) SELECT {columns} FROM ocr_records")
    different: int = connection.exec_driver_sql(
        "SELECT EXISTS(SELECT * FROM ocr_records EXCEPT SELECT * FROM ocr_records_0012)"
        "+ EXISTS(SELECT * FROM ocr_records_0012 EXCEPT SELECT * FROM ocr_records)"
    ).scalar_one()
    if (
        different
        or connection.exec_driver_sql("SELECT count(*) FROM ocr_records_0012").scalar_one() != count
    ):
        raise RuntimeError("Перенос строк OCR не подтверждён; переход отменён.")
    # Внешний ocr_batch_book тоже ссылается на таблицу: снимается лишь в этой транзакции.
    for name, _ in triggers:
        op.execute("DROP TRIGGER " + _quoted(name))
    op.execute("DROP TABLE ocr_records")
    op.execute("ALTER TABLE ocr_records_0012 RENAME TO ocr_records")
    for _, sql in indexes:
        op.execute(sql)
    for _, sql in triggers:
        op.execute(sql)


def downgrade() -> None:
    raise RuntimeError("Понижение схемы OCR запрещено; используйте исходную книгу.")
