"""Build a move manifest from decisions. Writes files; moves nothing.

Only 'move' decisions become planned moves. Everything else (review/skip, or
missing genre/artist) is emitted into the review report so no file is ever
placed under a guessed folder.

Target layout: <target_root>/<genre>/<Artist>/<original filename>
(when a move has no genre, the genre folder is omitted: <target_root>/<Artist>/...)

Filesystem safety:
- artist/genre path components are sanitized (slashes, control chars, reserved
  names, trailing dots/spaces).
- the original filename is preserved so the YouTube ID stays embedded.
- collisions append the YouTube ID to the stem rather than overwrite.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from . import db

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def sanitize_component(name: str, fallback: str = "Unknown") -> str:
    """Make a single path component safe across filesystems."""
    name = _ILLEGAL.sub("_", name)
    # Collapse fullwidth/odd separators already handled upstream; trim junk.
    name = name.strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    if not name or name.lower() in _RESERVED:
        return fallback
    return name[:200]


@dataclass
class PlannedMove:
    youtube_id: str
    action: str
    artist: str | None
    title: str | None
    genre: str | None
    confidence: float
    reason: str
    from_path: str
    to_path: str | None


def _target_path(
    target_root: Path, genre: str | None, artist: str, filename: str
) -> Path:
    dest = target_root
    if genre:
        dest = dest / sanitize_component(genre)
    return dest / sanitize_component(artist) / filename


def _resolve_collision(dest: Path, youtube_id: str, taken: set[str]) -> Path:
    """Append the YouTube ID if the destination already exists or is claimed."""
    key = str(dest)
    if key not in taken and not dest.exists():
        return dest
    new_name = f"{dest.stem} [{youtube_id}]{dest.suffix}"
    return dest.with_name(new_name)


def build_plan(
    target_root: str | Path,
    db_path: str | Path = db.DEFAULT_DB_PATH,
) -> list[PlannedMove]:
    target_root = Path(target_root).expanduser()
    planned: list[PlannedMove] = []
    taken: set[str] = set()

    with db.session(db_path) as conn:
        rows = conn.execute(
            """
            SELECT d.youtube_id, d.artist, d.title, d.genre, d.action,
                   d.confidence, d.reason, v.src_path, v.filename
              FROM decisions d
              JOIN videos v ON v.youtube_id = d.youtube_id
             ORDER BY d.action, d.artist
            """
        ).fetchall()

        for r in rows:
            to_path: str | None = None
            if r["action"] == "move" and r["artist"]:
                dest = _target_path(target_root, r["genre"], r["artist"], r["filename"])
                dest = _resolve_collision(dest, r["youtube_id"], taken)
                taken.add(str(dest))
                to_path = str(dest)

            planned.append(
                PlannedMove(
                    youtube_id=r["youtube_id"],
                    action=r["action"],
                    artist=r["artist"],
                    title=r["title"],
                    genre=r["genre"],
                    confidence=r["confidence"],
                    reason=r["reason"],
                    from_path=r["src_path"],
                    to_path=to_path,
                )
            )

    # Persist chosen target paths back onto decisions for traceability.
    with db.session(db_path) as conn:
        for pm in planned:
            conn.execute(
                "UPDATE decisions SET target_path = ? WHERE youtube_id = ?",
                (pm.to_path, pm.youtube_id),
            )

    return planned


def write_manifest(planned: list[PlannedMove], out_prefix: str | Path) -> dict[str, str]:
    """Write <prefix>.json (all rows) and <prefix>.csv (human review)."""
    out_prefix = Path(out_prefix)
    json_path = out_prefix.with_suffix(".json")
    csv_path = out_prefix.with_suffix(".csv")

    rows = [asdict(pm) for pm in planned]

    with json_path.open("w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False)

    fields = [
        "action", "confidence", "genre", "artist", "title",
        "youtube_id", "from_path", "to_path", "reason",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return {"json": str(json_path), "csv": str(csv_path)}


def summarize(planned: list[PlannedMove]) -> dict[str, int]:
    counts: dict[str, int] = {"total": len(planned)}
    for pm in planned:
        counts[pm.action] = counts.get(pm.action, 0) + 1
    return counts
