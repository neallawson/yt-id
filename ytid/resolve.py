"""Query and manually resolve the tracking status of scanned video files.

Because every file is a row with a `resolve_status`, the "worklist" of files
needing attention is just a query -- no CSV round-trips. Manual resolution
updates the row in place:

- assign a YouTube ID to an unresolved/duplicate file (id_source becomes 'manual')
- mark a file as 'ignored' so it is tracked but never fetched/moved
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import db

# Statuses that indicate a file still needs human attention.
NEEDS_ATTENTION = ("unresolved", "duplicate")


def list_videos(
    db_path: str | Path = db.DEFAULT_DB_PATH,
    statuses: tuple[str, ...] = NEEDS_ATTENTION,
    include_dash: bool = False,
) -> list[dict]:
    """Return tracked files matching the given resolve_status values.

    If include_dash is True, also return 'resolved' files whose id came from the
    lower-confidence dash-suffix convention, for auditing.
    """
    clauses = []
    params: list[str] = []
    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        clauses.append(f"resolve_status IN ({placeholders})")
        params.extend(statuses)
    if include_dash:
        clauses.append("(resolve_status = 'resolved' AND id_source = 'dash')")

    where = " OR ".join(f"({c})" for c in clauses) if clauses else "1"
    sql = (
        "SELECT id, resolve_status, id_source, detected_id, youtube_id, "
        "       src_path, filename "
        f"  FROM videos WHERE {where} "
        " ORDER BY resolve_status, filename"
    )
    with db.session(db_path) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def counts_by_status(db_path: str | Path = db.DEFAULT_DB_PATH) -> dict[str, int]:
    with db.session(db_path) as conn:
        rows = conn.execute(
            "SELECT resolve_status, COUNT(*) AS n FROM videos GROUP BY resolve_status"
        ).fetchall()
    return {r["resolve_status"]: r["n"] for r in rows}


def _find_row(conn, *, row_id: int | None, path: str | None):
    if row_id is not None:
        return conn.execute("SELECT * FROM videos WHERE id = ?", (row_id,)).fetchone()
    return conn.execute("SELECT * FROM videos WHERE src_path = ?", (path,)).fetchone()


def assign_id(
    youtube_id: str,
    row_id: int | None = None,
    path: str | None = None,
    db_path: str | Path = db.DEFAULT_DB_PATH,
) -> dict:
    """Manually assign a YouTube ID to a tracked file (by row id or src_path)."""
    if row_id is None and path is None:
        raise ValueError("provide either row_id or path")
    now = datetime.now(timezone.utc).isoformat()
    with db.session(db_path) as conn:
        row = _find_row(conn, row_id=row_id, path=path)
        if row is None:
            raise LookupError("no tracked file matches the given id/path")

        clash = conn.execute(
            "SELECT id FROM videos WHERE youtube_id = ? AND id <> ?",
            (youtube_id, row["id"]),
        ).fetchone()
        if clash is not None:
            raise ValueError(
                f"youtube_id {youtube_id} is already assigned to row {clash['id']}"
            )

        conn.execute(
            """
            UPDATE videos
               SET youtube_id = ?, detected_id = ?, id_source = 'manual',
                   resolve_status = 'resolved', last_seen_at = ?
             WHERE id = ?
            """,
            (youtube_id, youtube_id, now, row["id"]),
        )
        return dict(_find_row(conn, row_id=row["id"], path=None))


def set_ignored(
    row_id: int | None = None,
    path: str | None = None,
    db_path: str | Path = db.DEFAULT_DB_PATH,
) -> dict:
    """Mark a tracked file as ignored (tracked but never fetched/moved)."""
    if row_id is None and path is None:
        raise ValueError("provide either row_id or path")
    now = datetime.now(timezone.utc).isoformat()
    with db.session(db_path) as conn:
        row = _find_row(conn, row_id=row_id, path=path)
        if row is None:
            raise LookupError("no tracked file matches the given id/path")
        conn.execute(
            "UPDATE videos SET resolve_status = 'ignored', last_seen_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        return dict(_find_row(conn, row_id=row["id"], path=None))
