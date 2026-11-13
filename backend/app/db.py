"""Portable transactional store. PostgreSQL in Compose, SQLite for local demos/tests."""
import contextlib
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, event, text

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
        service TEXT NOT NULL, owner TEXT NOT NULL, environment TEXT NOT NULL,
        current_version INTEGER NOT NULL, status TEXT NOT NULL,
        confidence REAL NOT NULL, issues TEXT NOT NULL, duplicate_of TEXT,
        updated_at TEXT NOT NULL, reviewed_at TEXT, origin TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS versions (
        document_id TEXT NOT NULL, version INTEGER NOT NULL, content TEXT NOT NULL,
        content_hash TEXT NOT NULL, sections TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(document_id, version))""",
    """CREATE TABLE IF NOT EXISTS sources (
        source_key TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
        revision INTEGER NOT NULL, document_ids TEXT NOT NULL, removed INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS aliases (
        alias TEXT PRIMARY KEY, document_id TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS document_owners (
        document_id TEXT PRIMARY KEY, source_key TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS messages (
        session_id TEXT NOT NULL, sequence INTEGER NOT NULL, role TEXT NOT NULL,
        content TEXT NOT NULL, kind TEXT, document_id TEXT, created_at TEXT NOT NULL,
        PRIMARY KEY(session_id, sequence))""",
    """CREATE TABLE IF NOT EXISTS request_metrics (
        id TEXT PRIMARY KEY, operation TEXT NOT NULL, latency_ms REAL NOT NULL,
        model_calls INTEGER NOT NULL, input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL, cost_usd REAL NOT NULL,
        outcome TEXT NOT NULL, created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_documents_status_kind ON documents(status, kind)",
    "CREATE INDEX IF NOT EXISTS idx_versions_hash ON versions(content_hash)",
]


class Store:
    def __init__(self, url=None):
        self.url = url or os.getenv("DATABASE_URL", "sqlite:///data/knowledge.db")
        if self.url.startswith("sqlite:///"):
            path = self.url.removeprefix("sqlite:///")
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(self.url, pool_pre_ping=True,
            connect_args={"check_same_thread": False, "timeout": 30} if self.url.startswith("sqlite") else {})
        if self.url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def configure(connection, _):
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA busy_timeout=30000")
        with self.engine.begin() as conn:
            if not self.url.startswith("sqlite"):
                conn.execute(text("SELECT pg_advisory_xact_lock(7401941)"))
            for statement in SCHEMA:
                conn.execute(text(statement))
            # Backfill ownership for an existing local prototype database.
            owned = {r[0] for r in conn.execute(text("SELECT document_id FROM document_owners"))}
            for source in conn.execute(text("SELECT source_key, document_ids FROM sources WHERE removed=0 ORDER BY source_key")).mappings():
                for doc_id in json.loads(source["document_ids"]):
                    if doc_id not in owned:
                        conn.execute(text("INSERT INTO document_owners VALUES (:id,:source)"), {"id":doc_id,"source":source["source_key"]})
                        owned.add(doc_id)

    @contextlib.contextmanager
    def transaction(self, write=False):
        with self.engine.connect() as conn:
            if write and self.url.startswith("sqlite"):
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                conn.begin()
                if write:
                    # Serialize lifecycle mutations across API/worker processes.
                    conn.execute(text("SELECT pg_advisory_xact_lock(7401941)"))
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise


def rows(conn, sql, **params):
    return [dict(row) for row in conn.execute(text(sql), params).mappings()]


def one(conn, sql, **params):
    result = rows(conn, sql, **params)
    return result[0] if result else None


def execute(conn, sql, **params):
    return conn.execute(text(sql), params)
