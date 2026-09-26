"""Engine/session factory. SQLite by default; any SQLAlchemy URL (e.g. PostgreSQL) works."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite:///") and not url.endswith(":memory:"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    if url.endswith(":memory:"):
        from sqlalchemy.pool import StaticPool

        kwargs["poolclass"] = StaticPool
    eng = create_engine(url, future=True, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _pragma(dbapi_conn, _rec):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return eng


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def migrate(url: str) -> None:
    """Apply Alembic migrations (head)."""
    from alembic import command
    from alembic.config import Config

    if url.startswith("sqlite:///") and not url.endswith(":memory:"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    from marketlens.config import ALEMBIC_DIR

    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


def _sqlite_path(url: str) -> Path | None:
    if not url.startswith("sqlite:///") or url.endswith(":memory:"):
        return None
    return Path(url.removeprefix("sqlite:///"))


def backup_sqlite(url: str, dest: Path) -> Path:
    """A consistent copy of a live SQLite database (the -wal content included) via SQLite's online backup
    API. Copying only ``marketlens.db`` while the app writes can produce a corrupt file ("database disk
    image is malformed"); this does not."""
    import sqlite3

    src = _sqlite_path(url)
    if src is None:
        raise ValueError("백업은 파일 SQLite 데이터베이스에서만 지원합니다")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError(f"{dest} 이미 있음 — 덮어쓰지 않습니다")
    with sqlite3.connect(src) as a, sqlite3.connect(dest) as b:
        a.backup(b)
    with sqlite3.connect(dest) as b:
        ok = b.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        raise RuntimeError(f"백업 무결성 검사 실패: {ok}")
    return dest


def checkpoint_sqlite(url: str) -> None:
    """Fold the -wal file back into the database (on shutdown), so the .db file alone is complete."""
    import sqlite3

    src = _sqlite_path(url)
    if src is None or not src.exists():
        return
    with sqlite3.connect(src) as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
