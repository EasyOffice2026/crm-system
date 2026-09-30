import os
from collections.abc import Generator
from sqlite3 import Connection

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


DATABASE_URL = os.environ.get("CRM_DATABASE_URL", "sqlite:///./crm.db")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {},
)


@event.listens_for(engine, "connect")
def sqlite_pragmas(connection: object, record: object) -> None:
    if DATABASE_URL.startswith("sqlite"):
        assert isinstance(connection, Connection)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def db_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
