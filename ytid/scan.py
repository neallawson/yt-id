"""Scan a source folder for video files and index them by YouTube ID.

Every video file is tracked as a row (keyed by src_path), whether or not an ID
can be parsed. Two real-world filename conventions are recognized, both using
the 11-char YouTube alphabet [A-Za-z0-9_-]:

- bracket:  `Atomic Rooster - The Devils Answer [8R5El2HWMIo].webm`
- dash:     `About the World-kxNehhCWIrs.webm`  (yt-dlp default `%(title)s-%(id)s`)

Because the ID itself may contain hyphens, the dash form is matched by anchoring
to the end of the stem and taking exactly 11 valid chars preceded by '-'.

resolve_status per row:
  resolved    - a usable, unique youtube_id was assigned
  unresolved  - no id could be parsed (needs manual attention)
  duplicate   - parsed id is already claimed by another tracked file
  ignored     - user opted this file out (set manually)

This stage is fast, local, and idempotent: re-running inserts newly seen files
and refreshes existing ones.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, NamedTuple

from . import db

VIDEO_EXTENSIONS = {".webm", ".mkv", ".mp4", ".m4v", ".mov", ".avi", ".flv"}

# Bracketed id: LAST bracketed 11-char token (titles may contain other brackets).
_BRACKET_RE = re.compile(r"\[([A-Za-z0-9_-]{11})\](?=[^\[]*$)")
# Dash-suffix id: exactly 11 valid chars at the END of the stem, preceded by '-'.
_DASH_RE = re.compile(r"-([A-Za-z0-9_-]{11})$")


class ScannedFile(NamedTuple):
    detected_id: str | None
    id_source: str  # bracket|dash|none
    src_path: str
    filename: str
    ext: str


def extract_youtube_id(filename: str) -> tuple[str | None, str]:
    """Return (detected_id, id_source) for a filename.

    id_source is 'bracket', 'dash', or 'none'. Bracket is preferred (higher
    confidence); dash-suffix is the yt-dlp default template and is inherently
    more ambiguous.
    """
    stem = Path(filename).stem
    m = _BRACKET_RE.search(stem)
    if m:
        return m.group(1), "bracket"
    m = _DASH_RE.search(stem)
    if m:
        return m.group(1), "dash"
    return None, "none"


def iter_video_files(source: Path, extensions: set[str] = VIDEO_EXTENSIONS) -> Iterable[Path]:
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.suffix.lower() in extensions:
            yield path


def scan_folder(source: str | Path, extensions: set[str] = VIDEO_EXTENSIONS) -> list[ScannedFile]:
    """Return every video file, with parsed id/source (no DB writes)."""
    source = Path(source).expanduser()
    if not source.is_dir():
        raise NotADirectoryError(f"source is not a directory: {source}")

    results: list[ScannedFile] = []
    for path in iter_video_files(source, extensions):
        detected_id, id_source = extract_youtube_id(path.name)
        results.append(
            ScannedFile(
                detected_id=detected_id,
                id_source=id_source,
                src_path=str(path),
                filename=path.name,
                ext=path.suffix.lower(),
            )
        )
    return results


def _id_taken_by_other(conn, youtube_id: str, src_path: str) -> bool:
    """True if another tracked file already owns this youtube_id."""
    row = conn.execute(
        "SELECT 1 FROM videos WHERE youtube_id = ? AND src_path <> ? LIMIT 1",
        (youtube_id, src_path),
    ).fetchone()
    return row is not None


def scan(source: str | Path, db_path: str | Path = db.DEFAULT_DB_PATH) -> dict[str, int]:
    """Scan the source folder and upsert EVERY video file into the videos table.

    Files are keyed by src_path. An id is assigned as youtube_id only when it is
    parseable and not already claimed; otherwise the row is left unresolved or
    flagged as a duplicate for manual attention.

    Returns counts keyed by resolve_status plus 'seen', 'new', 'updated'.
    """
    source = Path(source).expanduser()
    if not source.is_dir():
        raise NotADirectoryError(f"source is not a directory: {source}")

    now = datetime.now(timezone.utc).isoformat()
    counts = {
        "seen": 0, "new": 0, "updated": 0,
        "resolved": 0, "unresolved": 0, "duplicate": 0,
    }

    with db.session(db_path) as conn:
        for path in iter_video_files(source):
            counts["seen"] += 1
            src_path = str(path)
            detected_id, id_source = extract_youtube_id(path.name)

            # Decide the authoritative youtube_id and resolve_status.
            youtube_id: str | None = None
            if detected_id is None:
                resolve_status = "unresolved"
            elif _id_taken_by_other(conn, detected_id, src_path):
                resolve_status = "duplicate"
            else:
                youtube_id = detected_id
                resolve_status = "resolved"

            existing = conn.execute(
                "SELECT resolve_status, id_source FROM videos WHERE src_path = ?",
                (src_path,),
            ).fetchone()

            if existing is None:
                conn.execute(
                    """
                    INSERT INTO videos
                        (src_path, filename, ext, detected_id, id_source,
                         youtube_id, resolve_status, fetch_status,
                         first_seen_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (src_path, path.name, path.suffix.lower(), detected_id,
                     id_source, youtube_id, resolve_status, now, now),
                )
                counts["new"] += 1
                counts[resolve_status] = counts.get(resolve_status, 0) + 1
            else:
                # Never clobber a manual/ignored resolution on rescan.
                if existing["resolve_status"] in ("ignored",) or existing["id_source"] == "manual":
                    conn.execute(
                        "UPDATE videos SET filename = ?, ext = ?, last_seen_at = ? WHERE src_path = ?",
                        (path.name, path.suffix.lower(), now, src_path),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE videos
                           SET filename = ?, ext = ?, detected_id = ?, id_source = ?,
                               youtube_id = ?, resolve_status = ?, last_seen_at = ?
                         WHERE src_path = ?
                        """,
                        (path.name, path.suffix.lower(), detected_id, id_source,
                         youtube_id, resolve_status, now, src_path),
                    )
                counts["updated"] += 1

    return counts
