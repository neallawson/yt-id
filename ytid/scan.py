"""Scan a source folder for video files and index them by YouTube ID.

The YouTube ID is the last 11-character token inside square brackets in the
filename, e.g. `Atomic Rooster - The Devils Answer [8R5El2HWMIo].webm`.

This stage is fast, local, and idempotent: re-running only inserts newly seen
files and refreshes `last_seen_at` for existing ones.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, NamedTuple

from . import db

VIDEO_EXTENSIONS = {".webm", ".mkv", ".mp4", ".m4v", ".mov", ".avi", ".flv"}

# YouTube IDs are exactly 11 chars from [A-Za-z0-9_-]. Match the LAST bracketed
# occurrence in the basename, since titles may contain other bracketed text.
_ID_RE = re.compile(r"\[([A-Za-z0-9_-]{11})\](?=[^\[]*$)")


class ScannedFile(NamedTuple):
    youtube_id: str
    src_path: str
    filename: str
    ext: str


def extract_youtube_id(filename: str) -> str | None:
    """Return the 11-char YouTube ID from a filename, or None if absent."""
    stem = Path(filename).stem
    match = _ID_RE.search(stem)
    return match.group(1) if match else None


def iter_video_files(source: Path, extensions: set[str] = VIDEO_EXTENSIONS) -> Iterable[Path]:
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.suffix.lower() in extensions:
            yield path


def scan_folder(source: str | Path, extensions: set[str] = VIDEO_EXTENSIONS) -> list[ScannedFile]:
    """Return scanned files that carry a valid YouTube ID (no DB writes)."""
    source = Path(source).expanduser()
    if not source.is_dir():
        raise NotADirectoryError(f"source is not a directory: {source}")

    results: list[ScannedFile] = []
    for path in iter_video_files(source, extensions):
        yid = extract_youtube_id(path.name)
        if yid is None:
            continue
        results.append(
            ScannedFile(
                youtube_id=yid,
                src_path=str(path),
                filename=path.name,
                ext=path.suffix.lower(),
            )
        )
    return results


def scan(source: str | Path, db_path: str | Path = db.DEFAULT_DB_PATH) -> dict[str, int]:
    """Scan the source folder and upsert into the videos table.

    Returns counts: {'seen', 'new', 'updated', 'skipped_no_id'}.
    """
    source = Path(source).expanduser()
    if not source.is_dir():
        raise NotADirectoryError(f"source is not a directory: {source}")

    now = datetime.now(timezone.utc).isoformat()
    counts = {"seen": 0, "new": 0, "updated": 0, "skipped_no_id": 0}

    with db.session(db_path) as conn:
        for path in iter_video_files(source):
            counts["seen"] += 1
            yid = extract_youtube_id(path.name)
            if yid is None:
                counts["skipped_no_id"] += 1
                continue

            row = conn.execute(
                "SELECT youtube_id FROM videos WHERE youtube_id = ?", (yid,)
            ).fetchone()

            if row is None:
                conn.execute(
                    """
                    INSERT INTO videos
                        (youtube_id, src_path, filename, ext,
                         fetch_status, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (yid, str(path), path.name, path.suffix.lower(), now, now),
                )
                counts["new"] += 1
            else:
                # Refresh path (files may have moved) and last_seen timestamp.
                conn.execute(
                    """
                    UPDATE videos
                       SET src_path = ?, filename = ?, ext = ?, last_seen_at = ?
                     WHERE youtube_id = ?
                    """,
                    (str(path), path.name, path.suffix.lower(), now, yid),
                )
                counts["updated"] += 1

    return counts
