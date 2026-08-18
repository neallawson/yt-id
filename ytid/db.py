"""SQLite state layer for ytid.

Four durable tables form the backbone of the pipeline:

- videos:    one row per discovered file, plus raw yt-dlp metadata & fetch status
- artists:   resolved genre per artist, with provenance
- decisions: the classifier's chosen artist/title/genre/target/action per video
- moves:     an audit log of applied moves, enabling --undo

Every stage reads/writes these tables so that nothing is ever re-fetched or
re-guessed unnecessarily, and runs are safe to interrupt and resume.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = "ytid.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    src_path       TEXT NOT NULL UNIQUE,
    filename       TEXT NOT NULL,
    ext            TEXT NOT NULL,
    detected_id    TEXT,                              -- raw parse result (may repeat)
    id_source      TEXT NOT NULL DEFAULT 'none',      -- bracket|dash|manual|none
    youtube_id     TEXT UNIQUE,                       -- authoritative id used downstream
    resolve_status TEXT NOT NULL DEFAULT 'unresolved',-- resolved|unresolved|duplicate|ignored
    raw_json       TEXT,
    fetch_status   TEXT NOT NULL DEFAULT 'pending',  -- pending|ok|error|unavailable
    fetch_error    TEXT,
    ytdlp_version  TEXT,
    fetched_at     TEXT,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artists (
    name        TEXT PRIMARY KEY COLLATE NOCASE,
    genre       TEXT NOT NULL,
    source      TEXT NOT NULL,                        -- override|musicbrainz|manual
    mbid        TEXT,
    confidence  REAL NOT NULL DEFAULT 1.0,
    resolved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    youtube_id  TEXT PRIMARY KEY REFERENCES videos(youtube_id),
    artist      TEXT,
    title       TEXT,
    genre       TEXT,
    target_path TEXT,
    action      TEXT NOT NULL,                        -- move|review|skip
    confidence  REAL NOT NULL DEFAULT 0.0,
    reason      TEXT,
    decided_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS moves (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_id  TEXT NOT NULL REFERENCES videos(youtube_id),
    run_id      TEXT,                                 -- groups moves from one apply run
    from_path   TEXT NOT NULL,
    to_path     TEXT NOT NULL,
    size        INTEGER,                              -- source byte size, for verification
    status      TEXT NOT NULL DEFAULT 'done',         -- pending|done|rolled_back
    applied_at  TEXT,
    undone_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_videos_fetch_status   ON videos(fetch_status);
CREATE INDEX IF NOT EXISTS idx_videos_resolve_status ON videos(resolve_status);
CREATE INDEX IF NOT EXISTS idx_videos_id_source      ON videos(id_source);
CREATE INDEX IF NOT EXISTS idx_decisions_action      ON decisions(action);
CREATE INDEX IF NOT EXISTS idx_moves_run             ON moves(run_id);
CREATE INDEX IF NOT EXISTS idx_moves_status          ON moves(status);
"""

# Columns added after the initial schema; applied to pre-existing databases so
# `CREATE TABLE IF NOT EXISTS` (which never alters) does not leave them stale.
_MIGRATIONS = {
    "moves": {
        "run_id": "ALTER TABLE moves ADD COLUMN run_id TEXT",
        "size": "ALTER TABLE moves ADD COLUMN size INTEGER",
        "status": "ALTER TABLE moves ADD COLUMN status TEXT NOT NULL DEFAULT 'done'",
    },
}


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, columns in _MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(ddl)


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) the database and ensure the schema exists."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.executescript(SCHEMA)
    _apply_migrations(conn)
    conn.commit()
    return conn


@contextmanager
def session(db_path: str | Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    """Context manager that commits on success and closes on exit."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
