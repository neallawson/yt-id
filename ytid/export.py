"""Export a denormalized audit ledger of everything processed and moved.

Joins ``videos`` + ``decisions`` + the latest ``moves`` row into a single row
per tracked file, so the artifact archived next to the moved videos is a
human-readable master record that stands on its own without the SQLite DB.

This is a *view* of the database, not a replacement or a restore format: it is
meant for auditing ("what was processed, decided, and where did it land"), and
pairs with the raw ``ytid.db`` (queryable truth) and ``ytid.yaml`` (the
re-appliable manual layer).
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from importlib import metadata as _metadata
from pathlib import Path

from . import db

EXPORT_FORMATS = ("csv", "json", "both")

# Ordered so a row reads as a story: identity -> resolution -> decision ->
# outcome. Every field maps to exactly one source column in build_ledger().
LEDGER_FIELDS = [
    "youtube_id",
    "original_filename",
    "src_path",
    "id_source",
    "resolve_status",
    "fetch_status",
    "ytdlp_version",
    "artist",
    "title",
    "genre",
    "action",
    "confidence",
    "reason",
    "target_path",
    "moved_to",
    "move_status",
    "applied_at",
    "undone_at",
]


def _tool_version() -> str | None:
    """Best-effort distribution version, for a self-describing archive."""
    try:
        return _metadata.version("yt-id")
    except _metadata.PackageNotFoundError:
        return None


def build_ledger(db_path: str | Path = db.DEFAULT_DB_PATH) -> list[dict]:
    """One denormalized row per tracked file (including unresolved ones).

    ``LEFT JOIN`` keeps files that never reached a decision or a move so the
    ledger is a complete account of the corpus, not just the successes. The
    latest ``moves`` row (by insertion id) wins, so a move followed by an undo
    reports the current ``rolled_back`` state.
    """
    with db.session(db_path) as conn:
        rows = conn.execute(
            """
            SELECT v.youtube_id       AS youtube_id,
                   v.filename         AS original_filename,
                   v.src_path         AS src_path,
                   v.id_source        AS id_source,
                   v.resolve_status   AS resolve_status,
                   v.fetch_status     AS fetch_status,
                   v.ytdlp_version    AS ytdlp_version,
                   d.artist           AS artist,
                   d.title            AS title,
                   d.genre            AS genre,
                   d.action           AS action,
                   d.confidence       AS confidence,
                   d.reason           AS reason,
                   d.target_path      AS target_path,
                   m.to_path          AS moved_to,
                   m.status           AS move_status,
                   m.applied_at       AS applied_at,
                   m.undone_at        AS undone_at
              FROM videos v
              LEFT JOIN decisions d ON d.youtube_id = v.youtube_id
              LEFT JOIN moves m
                     ON m.id = (SELECT id FROM moves
                                 WHERE youtube_id = v.youtube_id
                                 ORDER BY id DESC LIMIT 1)
             ORDER BY d.action IS NULL, d.action, d.artist, v.filename
            """
        ).fetchall()
    return [{field: r[field] for field in LEDGER_FIELDS} for r in rows]


def build_meta(db_path: str | Path, ledger: list[dict]) -> dict:
    """A small self-describing header (counts, timestamps, versions)."""
    by_action: dict[str, int] = {}
    for row in ledger:
        key = row.get("action") or "unclassified"
        by_action[key] = by_action.get(key, 0) + 1
    moved = sum(1 for r in ledger if r.get("move_status") == "done")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "tool": "yt-id",
        "tool_version": _tool_version(),
        "total": len(ledger),
        "by_action": by_action,
        "moved": moved,
    }


def write_ledger(
    ledger: list[dict],
    out_prefix: str | Path,
    fmt: str = "both",
    meta: dict | None = None,
) -> dict[str, str]:
    """Write the ledger to ``<prefix>.json`` and/or ``<prefix>.csv``.

    JSON carries the ``meta`` header alongside the rows (structured, preserves
    nesting/nulls); CSV is a flat, spreadsheet-friendly table of just the rows.
    """
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"fmt must be one of {EXPORT_FORMATS}")

    out_prefix = Path(out_prefix)
    written: dict[str, str] = {}

    if fmt in ("json", "both"):
        json_path = out_prefix.with_suffix(".json")
        payload: dict | list = {"meta": meta, "ledger": ledger} if meta else ledger
        with json_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        written["json"] = str(json_path)

    if fmt in ("csv", "both"):
        csv_path = out_prefix.with_suffix(".csv")
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for row in ledger:
                writer.writerow(row)
        written["csv"] = str(csv_path)

    return written
