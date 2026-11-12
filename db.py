"""Подключение к БД: PostgreSQL — основной режим, SQLite — для быстрых автотестов."""
import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

DEFAULT_URL = "postgresql+psycopg2://checkstock:checkstock@localhost:5432/checkstock"


def database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_URL)


def make_engine(url: str):
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or url == "sqlite://":
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)

        # pysqlite не открывает транзакции явно, из-за чего SAVEPOINT (begin_nested)
        # работает некорректно. Явные BEGIN дают те же транзакционные условия, что и PostgreSQL.
        @event.listens_for(engine, "connect")
        def _sqlite_no_isolation(dbapi_conn, _record):
            dbapi_conn.isolation_level = None

        @event.listens_for(engine, "begin")
        def _sqlite_begin(conn):
            conn.exec_driver_sql("BEGIN")

        return engine
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)