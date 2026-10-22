"""SQLite storage. One short-lived connection per operation keeps it thread-safe."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from saathi import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS person (
    person_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    speaker_key TEXT,
    checkin_times TEXT NOT NULL DEFAULT '[]',
    language TEXT NOT NULL DEFAULT 'en-IN',
    speech_rate REAL NOT NULL DEFAULT 0.95,
    -- the six flags below are unused (sharing switches were removed); kept so older databases still load
    share_reminders INTEGER NOT NULL DEFAULT 1,
    share_selfreported_mood INTEGER NOT NULL DEFAULT 1,
    share_topics INTEGER NOT NULL DEFAULT 0,
    accept_family_notes INTEGER NOT NULL DEFAULT 1,
    auto_memory INTEGER NOT NULL DEFAULT 1,
    notice_impressions INTEGER NOT NULL DEFAULT 1,
    share_impressions INTEGER NOT NULL DEFAULT 1,
    emergency_contact_id TEXT
);
CREATE TABLE IF NOT EXISTS contact (
    contact_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    relationship TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL,
    consent_granted INTEGER NOT NULL DEFAULT 0,
    can_leave_notes INTEGER NOT NULL DEFAULT 0,
    notify_email INTEGER NOT NULL DEFAULT 1,
    contact_token TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS reminder (
    reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    time TEXT NOT NULL,
    date TEXT,
    recurrence TEXT NOT NULL DEFAULT 'none',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    last_fired_at TEXT,
    last_done_at TEXT
);
CREATE TABLE IF NOT EXISTS activity (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    mood_word TEXT
);
CREATE TABLE IF NOT EXISTS memory (
    memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    fact TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'explicit'
);
CREATE TABLE IF NOT EXISTS note (
    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    contact_id TEXT NOT NULL REFERENCES contact(contact_id) ON DELETE CASCADE,
    direction TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    reply_to INTEGER
);
CREATE TABLE IF NOT EXISTS preview (
    token TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    contact_id TEXT NOT NULL REFERENCES contact(contact_id) ON DELETE CASCADE,
    facts_json TEXT NOT NULL,
    preview_text TEXT NOT NULL,
    extra_note TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS family_update (
    update_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    contact_id TEXT NOT NULL REFERENCES contact(contact_id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkin (
    person_id TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    slot TEXT NOT NULL,
    surfaced_at TEXT NOT NULL,
    PRIMARY KEY (person_id, date, slot)
);
CREATE TABLE IF NOT EXISTS impression (
    impression_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    text TEXT NOT NULL,
    aspect TEXT NOT NULL DEFAULT 'mood',
    confidence REAL NOT NULL DEFAULT 0.5,
    method TEXT NOT NULL DEFAULT 'rules'
);
CREATE TABLE IF NOT EXISTS day_impression (
    person_id TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    text TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (person_id, day)
);
CREATE TABLE IF NOT EXISTS turn_stat (
    stat_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    content_words INTEGER NOT NULL,
    replied_to_open_q INTEGER NOT NULL,
    initiated INTEGER NOT NULL,
    disclosed INTEGER NOT NULL,
    asked_q INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS alert (
    alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS digest (
    person_id TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (person_id, date)
);
CREATE TABLE IF NOT EXISTS clock (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    offset_seconds REAL NOT NULL DEFAULT 0
);
"""


# Columns added after the first release; applied to existing databases on start-up.
MIGRATIONS = (
    ("person", "auto_memory", "INTEGER NOT NULL DEFAULT 1"),
    ("memory", "source", "TEXT NOT NULL DEFAULT 'explicit'"),
    ("person", "notice_impressions", "INTEGER NOT NULL DEFAULT 1"),
    ("person", "share_impressions", "INTEGER NOT NULL DEFAULT 1"),
    ("person", "address", "TEXT NOT NULL DEFAULT ''"),
    ("contact", "phone", "TEXT NOT NULL DEFAULT ''"),
    ("contact", "notify_sms", "INTEGER NOT NULL DEFAULT 1"),
)


def db_path() -> Path:
    return config.get().db_path


def init(path: Path | None = None) -> None:
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        for table, column, declaration in MIGRATIONS:
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        conn.execute("INSERT OR IGNORE INTO clock (id, offset_seconds) VALUES (1, 0)")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    if not path.exists():
        init(path)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def loads(raw: str | None, default):
    if not raw:
        return default
    return json.loads(raw)
