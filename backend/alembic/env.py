from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401  (importing registers every table on Base.metadata)
from app.config import get_settings
from app.db import Base

config = context.config
if not config.get_main_option("sqlalchemy.url"):
    settings = get_settings()
    settings.ensure_dirs()
    config.set_main_option("sqlalchemy.url", f"sqlite:///{settings.db_path.as_posix()}".replace("%", "%%"))

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001
    # The full-text index and its shadow tables are created by hand in a migration, not from a model.
    if type_ == "table" and name.startswith("transcript_fts"):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
                      literal_binds=True, render_as_batch=True, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.",
                                     poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True,
                          compare_type=True, include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
