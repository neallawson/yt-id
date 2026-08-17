"""Resolve YouTube metadata for pending videos and cache it in SQLite.

This is the slow, network-bound stage. It is resumable (only rows whose
fetch_status is 'pending' or 'error' are processed) and polite (configurable
sleep + jitter between requests to avoid throttling at 20k+ scale).

'unavailable' videos (deleted/private/geo-blocked) are recorded as terminal and
are NOT retried unless explicitly forced.
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .ytdlp_client import DEFAULT_BINARY, fetch_metadata


def _select_targets(conn, retry_errors: bool, retry_unavailable: bool, limit: int | None):
    statuses = ["pending"]
    if retry_errors:
        statuses.append("error")
    if retry_unavailable:
        statuses.append("unavailable")
    placeholders = ",".join("?" for _ in statuses)
    sql = (
        f"SELECT youtube_id FROM videos "
        f"WHERE fetch_status IN ({placeholders}) "
        f"  AND resolve_status = 'resolved' AND youtube_id IS NOT NULL "
        f"ORDER BY first_seen_at"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r["youtube_id"] for r in conn.execute(sql, statuses).fetchall()]


def fetch_pending(
    db_path: str | Path = db.DEFAULT_DB_PATH,
    binary: str = DEFAULT_BINARY,
    sleep: float = 2.0,
    jitter: float = 1.0,
    limit: int | None = None,
    retry_errors: bool = False,
    retry_unavailable: bool = False,
    timeout: float = 60.0,
    progress=None,
) -> dict[str, int]:
    """Fetch metadata for pending/eligible videos.

    Returns counts: {'attempted', 'ok', 'error', 'unavailable'}.
    """
    counts = {"attempted": 0, "ok": 0, "error": 0, "unavailable": 0}

    with db.session(db_path) as conn:
        targets = _select_targets(conn, retry_errors, retry_unavailable, limit)

    total = len(targets)
    for idx, yid in enumerate(targets, start=1):
        result = fetch_metadata(yid, binary=binary, timeout=timeout)
        now = datetime.now(timezone.utc).isoformat()
        raw = json.dumps(result.metadata) if result.metadata is not None else None

        # Commit each result immediately so a crash never loses progress.
        with db.session(db_path) as conn:
            conn.execute(
                """
                UPDATE videos
                   SET raw_json = ?, fetch_status = ?, fetch_error = ?,
                       ytdlp_version = ?, fetched_at = ?
                 WHERE youtube_id = ?
                """,
                (raw, result.status, result.error, result.ytdlp_version, now, yid),
            )

        counts["attempted"] += 1
        counts[result.status] = counts.get(result.status, 0) + 1
        if progress:
            progress(idx, total, yid, result.status)

        # Politeness delay (skip after the final item).
        if idx < total and sleep > 0:
            time.sleep(sleep + random.uniform(0, max(0.0, jitter)))

    return counts
