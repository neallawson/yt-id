"""Build a move manifest from decisions. Writes files; moves nothing.

Only 'move' decisions with a title become planned moves. A row with no title
is left unplaced. Artist and genre folders are omitted when those fields are
empty.

Target layout: <target_root>/<genre>/<Artist>/<filename>
No genre omits that folder. No artist omits the artist folder.

Every destination filename is built from the decision, not the source name:

    Artist - Title [id].ext

With no artist the name is ``Title [id].ext``. ``--omit-artist-from-filename``
drops the artist prefix only when an artist folder is actually created.
Flattened files that have an artist keep it in the name.

Every genre folder, artist folder, and filename is shaped the same way:
OS-safe characters first, then ``--strict-names`` if set, then
``--spaces-to-underscores`` if set. The bracketed YouTube id is appended
after that, so it is never rewritten. Collisions append the id again.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from . import db

# Illegal on Windows (the strictest of the common filesystems), plus C0 controls.
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# OS-legal characters that still trip shells and other software. Removed only
# under --strict-names. '&' is rewritten to " and " rather than dropped.
_STRICT_DROP = re.compile(r"['\"`;$(){}!#@~%,\uff0c\u3001]")
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

def _is_illegal(ch: str) -> bool:
    return bool(_ILLEGAL.match(ch))


def sanitize_component(name: str, fallback: str = "Unknown") -> str:
    """Make one path component legal on Windows, macOS, and Linux.

    Illegal characters (``< > : " / \\ | ? *`` and controls) are dropped.
    When dropping one would glue two words together, a single hyphen is left
    (``AC/DC`` → ``AC-DC``, ``Hello?`` → ``Hello``). Spaces, parentheses,
    brackets, ``&``, and apostrophes stay. Whitespace collapses to one space.
    """
    out: list[str] = []
    i, n = 0, len(name)
    while i < n:
        ch = name[i]
        if _is_illegal(ch):
            j = i + 1
            while j < n and _is_illegal(name[j]):
                j += 1
            prev = out[-1] if out else ""
            nxt = name[j] if j < n else ""
            if prev and not prev.isspace() and nxt and not nxt.isspace():
                out.append("-")
            i = j
        else:
            out.append(ch)
            i += 1
    text = re.sub(r"\s+", " ", "".join(out)).strip(" .")
    if not text or text.lower() in _RESERVED:
        return fallback
    return text[:200]


def strict_names(text: str) -> str:
    """Drop shell-hostile punctuation and spell ``&`` as ``and``.

    Parentheses, quotes, and commas are removed. The YouTube id is not passed
    through here; callers append ``[id]`` afterwards.
    """
    text = text.replace("&", " and ")
    text = _STRICT_DROP.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def shape_component(
    text: str,
    *,
    strict: bool = False,
    spaces_to_underscores: bool = False,
    fallback: str = "Unknown",
) -> str:
    """OS-safe a component, then apply the optional strict and space flags."""
    shaped = sanitize_component(text, fallback="")
    if strict and shaped:
        shaped = strict_names(shaped).strip(" .")
    if not shaped or shaped.lower() in _RESERVED:
        shaped = fallback
    if len(shaped) > 200:
        shaped = shaped[:200].rstrip(" .") or fallback
    if spaces_to_underscores:
        shaped = shaped.replace(" ", "_")
    return shaped


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


def destination_filename(
    artist: str | None,
    title: str | None,
    youtube_id: str,
    original_name: str,
    *,
    include_artist: bool = True,
    strict: bool = False,
    spaces_to_underscores: bool = False,
) -> str:
    """Build ``Artist - Title [id].ext`` from a decision.

    The words are shaped before the id is appended, so brackets and
    underscores inside the YouTube id are left alone. ``include_artist`` is
    false when the artist folder already carries the name, or when there is
    no artist. ``--spaces-to-underscores`` also turns the space before
    ``[id]`` into ``_``.
    """
    ext = Path(original_name).suffix
    artist_text = artist.strip() if artist else ""
    title_text = title.strip() if title else ""
    if artist_text and include_artist:
        label = f"{artist_text} - {title_text}" if title_text else artist_text
    else:
        label = title_text or artist_text

    joiner = "_" if spaces_to_underscores else " "
    id_token = f"[{youtube_id}]"
    room = 200 - len(joiner) - len(id_token)
    stem = shape_component(
        label, strict=strict, spaces_to_underscores=spaces_to_underscores, fallback="",
    )
    if len(stem) > room:
        stem = stem[:room].rstrip(" ._")
    if not stem:
        stem = "Unknown"
    return f"{stem}{joiner}{id_token}{ext}"


def _target_path(
    target_root: Path,
    genre: str | None,
    artist: str | None,
    filename: str,
    title: str | None = None,
    include_artist_folder: bool = True,
    youtube_id: str | None = None,
    omit_artist_from_filename: bool = False,
    strict: bool = False,
    spaces_to_underscores: bool = False,
) -> Path:
    policy = {"strict": strict, "spaces_to_underscores": spaces_to_underscores}
    dest = target_root
    if genre:
        dest = dest / shape_component(genre, **policy)
    artist_text = artist.strip() if artist else ""
    if include_artist_folder and artist_text:
        dest = dest / shape_component(artist_text, **policy)
    title_text = title.strip() if title else ""
    include_artist = bool(artist_text) and not (
        omit_artist_from_filename and include_artist_folder and title_text
    )
    if youtube_id:
        fname = destination_filename(
            artist, title, youtube_id, filename,
            include_artist=include_artist, **policy,
        )
    else:
        fname = shape_component(Path(filename).stem, **policy) + Path(filename).suffix
    return dest / fname


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
    min_artist_files: int = 1,
    omit_artist_from_filename: bool = False,
    strict_names: bool = False,
    spaces_to_underscores: bool = False,
) -> list[PlannedMove]:
    """Compute destination paths for every moved file.

    min_artist_files controls when an ``<Artist>/`` folder is created: an artist
    with fewer than this many moved files in the plan is flattened one level up
    (its files land in ``<target>/<genre>/`` instead of
    ``<target>/<genre>/<Artist>/``, or directly in ``<target>/`` when there is
    no genre). The default of 1 always creates the artist folder.

    A planned move requires a title. Destination names are
    ``Artist - Title [id].ext``, or ``Title [id].ext`` when the artist is
    absent or omit_artist_from_filename drops the prefix inside an artist
    folder. Flattened files that have an artist keep it in the name. An
    artist folder is created only when the artist is present and the file
    count meets min_artist_files.
    """
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

        # Pre-count moved files per artist so sparse artists can be flattened.
        artist_counts: dict[str, int] = {}
        for r in rows:
            if r["action"] == "move" and (r["title"] or "").strip() and r["artist"]:
                artist_counts[r["artist"]] = artist_counts.get(r["artist"], 0) + 1

        for r in rows:
            to_path: str | None = None
            title_text = (r["title"] or "").strip()
            if r["action"] == "move" and title_text:
                include_artist_folder = bool(r["artist"]) and (
                    artist_counts.get(r["artist"], 0) >= min_artist_files
                )
                dest = _target_path(
                    target_root, r["genre"], r["artist"], r["filename"],
                    title=r["title"],
                    include_artist_folder=include_artist_folder,
                    youtube_id=r["youtube_id"],
                    omit_artist_from_filename=omit_artist_from_filename,
                    strict=strict_names,
                    spaces_to_underscores=spaces_to_underscores,
                )
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
