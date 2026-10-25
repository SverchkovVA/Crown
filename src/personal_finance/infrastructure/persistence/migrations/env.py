"""Alembic принимает только уже открытое защищённое соединение."""

from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Миграции требуют защищённого соединения приложения.")
context.configure(connection=connection, transactional_ddl=True, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
