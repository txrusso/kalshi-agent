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


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
