from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from kalshi_agent.config import Settings
from kalshi_agent.data.models import Base


def make_engine(settings: Settings) -> Engine:
    if settings.database_url.startswith("sqlite"):
        db_path = urlparse(settings.database_url).path.lstrip("/")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(settings.database_url, future=True)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
