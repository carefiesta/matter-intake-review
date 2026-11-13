"""SQLite schema for documents, facts, flags, and the append-only log."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matters (
    matter_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    checklist_version TEXT NOT NULL,
    template_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    ready_by TEXT,
    ready_at TEXT,
    attorney_ack_by TEXT,
    attorney_ack_at TEXT,
    return_reason TEXT,
    processing_stage TEXT,
    sample_key TEXT
);

CREATE TABLE IF NOT EXISTS matter_access (
    matter_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY (matter_id, user_id)
);

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    matter_id TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    document_type TEXT NOT NULL,
    channel TEXT NOT NULL,
    received_at TEXT NOT NULL,
    extracted_at TEXT,
    length_bytes INTEGER NOT NULL,
    version INTEGER NOT NULL,
    locator TEXT NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    content BLOB NOT NULL,
    text_content TEXT,
    is_current INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS facts (
    fact_id TEXT PRIMARY KEY,
    matter_id TEXT NOT NULL,
    fact_type TEXT NOT NULL,
    value TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    document_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    locator TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL NOT NULL,
    label TEXT NOT NULL,
    sensitive INTEGER NOT NULL DEFAULT 0,
    prior_value TEXT,
    actor_id TEXT,
    acted_at TEXT,
    reason TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flags (
    flag_id TEXT PRIMARY KEY,
    matter_id TEXT NOT NULL,
    flag_type TEXT NOT NULL,
    fact_type TEXT NOT NULL,
    checklist_key TEXT,
    disposition TEXT,
    disposition_actor TEXT,
    disposition_at TEXT,
    disposition_reason TEXT,
    blocks_ready INTEGER NOT NULL,
    gap_reason TEXT,
    display_fact_id TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    signature TEXT NOT NULL,
    document_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flag_members (
    flag_id TEXT NOT NULL,
    fact_id TEXT,
    value TEXT NOT NULL,
    normalized_value TEXT,
    document_id TEXT,
    locator TEXT,
    excerpt TEXT
);

CREATE TABLE IF NOT EXISTS quotations (
    quote_id TEXT PRIMARY KEY,
    matter_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    locator TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    label TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_decisions (
    decision_id TEXT PRIMARY KEY,
    matter_id TEXT NOT NULL,
    object_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    acted_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    role TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    object_id TEXT,
    detail TEXT NOT NULL,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reveals (
    reveal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    role TEXT NOT NULL,
    reason TEXT NOT NULL,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_log (
    tool_log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);

CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS decisions_no_delete
BEFORE DELETE ON review_decisions
BEGIN
    SELECT RAISE(ABORT, 'review decisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS decisions_no_update
BEFORE UPDATE ON review_decisions
BEGIN
    SELECT RAISE(ABORT, 'review decisions are append-only');
END;
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if path.parent and str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
