from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from kalshi_agent.config import Settings
from kalshi_agent.data.models import Base


def make_engine(settings: Settings) -> Engine:
    is_sqlite = settings.database_url.startswith("sqlite")
    if is_sqlite:
        db_path = urlparse(settings.database_url).path.lstrip("/")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(settings.database_url, future=True)

    if is_sqlite:
        # WAL mode lets readers (the dashboard) read while a writer (the
        # agent's continuous loop) is mid-transaction — SQLite's default
        # rollback-journal mode blocks one against the other with a 0ms
        # busy_timeout, i.e. an immediate "database is locked" error. Both
        # processes now run concurrently against the same file, so this
        # went from a latent config detail to a real correctness issue.
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ARG001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


def _sync_missing_columns(engine: Engine) -> None:
    """create_all only creates missing TABLES, never adds columns to a table
    that already exists — if a model gains a field, an existing local DB
    silently keeps the old schema and later crashes with "no such column"
    the first time that field is actually used. Hit this for real 2026-07-09
    (Order.fair_value_at_entry/time_horizon, added mid-session, weren't in
    the already-created orders table — the running agent crashed on its
    first order attempt). Additive only (no rename/drop/type-change) — a
    proportionate safety net for a solo local SQLite project, not a
    replacement for a real migration tool if this ever needs one."""
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table.name})")}
            if not existing:
                continue  # table didn't exist before create_all — already correct
            for column in table.columns:
                if column.name not in existing:
                    col_type = column.type.compile(dialect=engine.dialect)
                    conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}")
        conn.commit()


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        _sync_missing_columns(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
