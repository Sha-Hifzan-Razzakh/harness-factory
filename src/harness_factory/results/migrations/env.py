"""Alembic environment for the results store.

ResultsStore passes its own connection in `config.attributes["connection"]` (one transaction for the whole
upgrade). The alembic CLI passes nothing, and the database is HF_RESULTS_URL, as for the factory.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from harness_factory import settings
from harness_factory.results.store import metadata

config = context.config


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or settings.RESULTS_URL


def _configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=metadata,                                       # autogenerate compares against this
        render_as_batch=connection.dialect.name == "sqlite",            # SQLite cannot ALTER most things
        compare_type=True,
    )


def run_offline() -> None:
    context.configure(url=_url(), target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
        with context.begin_transaction():                               # joins the caller's transaction
            context.run_migrations()
        return
    engine = create_engine(_url())
    try:
        with engine.begin() as connection:
            _configure(connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
