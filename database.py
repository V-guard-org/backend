"""Conexões SQLite compartilhadas pela API e pela importação do PostgreSQL."""

import contextlib
import os
from pathlib import Path
import sqlite3

HERE = Path(__file__).resolve().parent
BUSY_TIMEOUT_SECONDS = 5


def resolve_path(value):
    if not str(value).strip() or str(value) == ":memory:" or "://" in str(value):
        raise ValueError("DATABASE_PATH deve ser um caminho de arquivo SQLite persistente")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else HERE / path).resolve()


@contextlib.contextmanager
def connection(path, *, create=False):
    # mode=rw impede recriar silenciosamente um banco removido durante a execução.
    uri = Path(path).resolve().as_uri() + ("?mode=rwc" if create else "?mode=rw")
    conn = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        yield conn
    finally:
        conn.close()


def initialize(path, *, wal=True):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)
    with connection(path, create=True) as conn:
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("BEGIN;\n" + (HERE / "schema.sql").read_text(encoding="utf-8") + "\nCOMMIT;")


@contextlib.contextmanager
def transaction(path):
    with connection(path) as conn:
        conn.execute("BEGIN")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
