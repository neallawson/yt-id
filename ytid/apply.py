"""Execute (or reverse) a move manifest produced by `plan`.

Moves are recorded in the `moves` table before/at execution so they can be
replayed in reverse with --undo. Only rows whose action is 'move' and that have
a resolved to_path are touched. Directories are created as needed.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import db


def _load_manifest(manifest_path: str | Path) -> list[dict]:
    with Path(manifest_path).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def apply_manifest(
    manifest_path: str | Path,
    db_path: str | Path = db.DEFAULT_DB_PATH,
    dry_run: bool = False,
) -> dict[str, int]:
    """Move files according to the manifest's 'move' rows."""
    rows = _load_manifest(manifest_path)
    counts = {"planned": 0, "moved": 0, "skipped": 0, "errors": 0}

    for row in rows:
        if row.get("action") != "move" or not row.get("to_path"):
            continue
        counts["planned"] += 1
        src = Path(row["from_path"])
        dst = Path(row["to_path"])

        if not src.exists():
            counts["skipped"] += 1
            continue
        if dst.exists():
            # Collision should have been resolved at plan time; never overwrite.
            counts["skipped"] += 1
            continue

        if dry_run:
            continue

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        except OSError:
            counts["errors"] += 1
            continue

        now = datetime.now(timezone.utc).isoformat()
        with db.session(db_path) as conn:
            conn.execute(
                """
                INSERT INTO moves (youtube_id, from_path, to_path, applied_at)
                VALUES (?, ?, ?, ?)
                """,
                (row["youtube_id"], str(src), str(dst), now),
            )
            conn.execute(
                "UPDATE videos SET src_path = ? WHERE youtube_id = ?",
                (str(dst), row["youtube_id"]),
            )
        counts["moved"] += 1

    return counts


def undo_manifest(
    manifest_path: str | Path,
    db_path: str | Path = db.DEFAULT_DB_PATH,
    dry_run: bool = False,
) -> dict[str, int]:
    """Reverse previously applied moves recorded for this manifest's videos."""
    rows = _load_manifest(manifest_path)
    ids = [r["youtube_id"] for r in rows if r.get("action") == "move" and r.get("to_path")]
    counts = {"candidates": len(ids), "restored": 0, "skipped": 0, "errors": 0}

    for yid in ids:
        with db.session(db_path) as conn:
            mv = conn.execute(
                """
                SELECT id, from_path, to_path FROM moves
                 WHERE youtube_id = ? AND undone_at IS NULL
                 ORDER BY id DESC LIMIT 1
                """,
                (yid,),
            ).fetchone()

        if mv is None:
            counts["skipped"] += 1
            continue

        src_now = Path(mv["to_path"])   # current location
        dst_orig = Path(mv["from_path"])  # where to restore

        if not src_now.exists() or dst_orig.exists():
            counts["skipped"] += 1
            continue
        if dry_run:
            continue

        try:
            dst_orig.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_now), str(dst_orig))
        except OSError:
            counts["errors"] += 1
            continue

        now = datetime.now(timezone.utc).isoformat()
        with db.session(db_path) as conn:
            conn.execute("UPDATE moves SET undone_at = ? WHERE id = ?", (now, mv["id"]))
            conn.execute(
                "UPDATE videos SET src_path = ? WHERE youtube_id = ?",
                (str(dst_orig), yid),
            )
        counts["restored"] += 1

    return counts
