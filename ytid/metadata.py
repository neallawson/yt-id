"""The metadata kept from a yt-dlp dump.

``--dump-single-json`` includes download data this tool never reads (formats,
thumbnails, captions, heatmap). Classify and review only need a short record,
so that is all that is stored in ``videos.raw_json``.
"""

from __future__ import annotations

import json
import sqlite3

# Fields classify/review read, plus a few small ones worth keeping for later.
# Anything else in the yt-dlp dump is dropped.
KEEP_KEYS = (
    "id",
    "title",
    "channel",
    "uploader",
    "artist",
    "artists",
    "creator",
    "track",
    "album",
    "genre",
    "duration",
    "upload_date",
    "webpage_url",
)

# JSON keys that only appear in the full dump. A stored blob containing one
# of these still needs slimming.
_FAT_MARKERS = (
    '"formats"',
    '"thumbnails"',
    '"automatic_captions"',
    '"heatmap"',
)


def slim_metadata(meta: dict) -> dict:
    """Return the short record stored in ``videos.raw_json``."""
    out: dict = {}
    for key in KEEP_KEYS:
        value = meta.get(key)
        if value is None or value == "" or value == []:
            continue
        if key == "artists":
            if isinstance(value, list):
                names = [a for a in value if isinstance(a, str) and a.strip()]
                if names:
                    out[key] = names
            continue
        if isinstance(value, (dict, list)):
            continue
        out[key] = value
    return out


def _is_fat(raw: str) -> bool:
    return any(marker in raw for marker in _FAT_MARKERS)


def compact_stored_metadata(conn: sqlite3.Connection) -> int:
    """Rewrite full yt-dlp dumps already stored in ``videos.raw_json``.

    Returns the number of rows rewritten. Idempotent: a row that is already
    the short record is left alone.
    """
    where = " OR ".join("raw_json LIKE ?" for _ in _FAT_MARKERS)
    params = tuple(f"%{marker}%" for marker in _FAT_MARKERS)
    hit = conn.execute(
        f"SELECT 1 FROM videos WHERE {where} LIMIT 1",
        params,
    ).fetchone()
    if hit is None:
        return 0

    rows = conn.execute(
        f"SELECT id, raw_json FROM videos WHERE {where}",
        params,
    ).fetchall()
    rewritten = 0
    for row in rows:
        raw = row["raw_json"]
        if not raw or not _is_fat(raw):
            continue
        try:
            meta = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(meta, dict):
            continue
        conn.execute(
            "UPDATE videos SET raw_json = ? WHERE id = ?",
            (json.dumps(slim_metadata(meta), ensure_ascii=False), row["id"]),
        )
        rewritten += 1
    return rewritten
