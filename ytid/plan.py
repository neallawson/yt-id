"""Build a move manifest from decisions. Writes files; moves nothing.

Only 'move' decisions become planned moves. Everything else (review/skip, or
missing genre/artist) is emitted into the review report so no file is ever
placed under a guessed folder.

Target layout: <target_root>/<genre>/<Artist>/<filename>
(when a move has no genre, the genre folder is omitted: <target_root>/<Artist>/...)

Every destination filename is built from the decision, not the source name:

    Artist - Title [id].ext

``--omit-artist-from-filename`` drops the artist prefix only when an artist
folder is actually created, leaving ``Title [id].ext``. Flattened files (no
artist folder) always keep the artist in the name. A video override keeps the
supplied artist and title verbatim: ``--clean-names`` does not rewrite them.
Other decisions are still scrubbed when ``--clean-names`` is set.

Filesystem safety:
- artist/genre path components are sanitized (slashes, control chars, reserved
  names, trailing dots/spaces).
- the filename gets that same baseline sanitizer. The YouTube id is always
  appended in brackets, and the original extension is kept.
- collisions append the YouTube ID to the stem rather than overwrite.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from . import db
from .scan import _BRACKET_RE, _DASH_RE, extract_youtube_id

CLEAN_LEVELS = ("conservative", "moderate")

# Characters that are illegal in filenames on common filesystems (reused from
# the folder sanitizer): < > : " / \ | ? * and C0 control chars.
# Additionally neutralized under the 'moderate' level: shell-hostile but
# otherwise-legal characters that routinely trip scripts and other tools, plus
# commas (ASCII, fullwidth, and ideographic).
# ('&' is handled separately: it is rewritten to '_and_' to preserve meaning.)
_MODERATE_EXTRA = set("'\"`;$(){}!#@~%,\uff0c\u3001")

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

# Private-use placeholder that stands in for the verbatim YouTube-ID token while
# the rest of the stem is scrubbed. It is treated as ordinary content (not a
# separator), so the separator-collapse passes never touch the real ID.
_ID_SENTINEL = "\uE000"


def sanitize_component(name: str, fallback: str = "Unknown") -> str:
    """Make a single path component safe across filesystems."""
    name = _ILLEGAL.sub("_", name)
    # Collapse fullwidth/odd separators already handled upstream; trim junk.
    name = name.strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    if not name or name.lower() in _RESERVED:
        return fallback
    return name[:200]


def _id_span(stem: str) -> tuple[int, int] | None:
    """Return the (start, end) span of the YouTube-ID token within a stem.

    Bracket form `[<id>]` is preferred; the dash-suffix form `-<id>` at the end
    is protected together with its leading dash so the separator survives.
    """
    m = _BRACKET_RE.search(stem)
    if m:
        return m.span()
    m = _DASH_RE.search(stem)
    if m:
        return m.span()
    return None


def _is_bad(ch: str, moderate: bool) -> bool:
    if _ILLEGAL.match(ch):
        return True
    return moderate and ch in _MODERATE_EXTRA


def _split_id(stem: str) -> tuple[str, str]:
    """Replace the YouTube-ID token in `stem` with a sentinel.

    Returns `(stem_with_sentinel, id_text)`. The sentinel is treated as ordinary
    content by the scrubber, so separator-collapse never mangles IDs that contain
    ``_``/``-`` runs (e.g. ``EX_-1xbYx_E``). Splice the verbatim `id_text` back in
    as the final step. When no ID is present, returns `(stem, "")`.
    """
    span = _id_span(stem)
    if span is None:
        return stem, ""
    return stem[: span[0]] + _ID_SENTINEL + stem[span[1] :], stem[span[0] : span[1]]


def _splice_id(text: str, id_text: str) -> str:
    """Substitute the ID sentinel back with the verbatim `id_text`.

    Junction-aware: the dash-suffix ID form carries its own leading ``-``
    separator, so if the scrubbed text already ends in a separator right before
    the sentinel (e.g. a seam left by removing ``)``), the duplicate is dropped
    to avoid ``--``/``__``. The same is applied symmetrically at the trailing
    edge. Separators *inside* `id_text` are never touched.
    """
    if not id_text:
        return text.replace(_ID_SENTINEL, "")
    idx = text.find(_ID_SENTINEL)
    if idx == -1:
        return text
    before, after = text[:idx], text[idx + 1 :]
    if id_text[:1] in "-_" and before[-1:] in "-_":
        before = before[:-1]
    if id_text[-1:] in "-_" and after[:1] in "-_":
        after = after[1:]
    return before + id_text + after


def _clean_stem(stem: str, moderate: bool) -> str:
    """Core stem scrubber shared by clean_filename and token normalization.

    Whitespace runs collapse to a single underscore; runs of removed characters
    collapse to nothing, except a single dash seam is inserted when removal would
    otherwise concatenate two kept characters. The ID sentinel (if present) is
    treated as ordinary content and passes through untouched.

    Under 'moderate', '&' is rewritten to ' and ' (surrounding whitespace then
    collapses to '_and_') so the meaning survives instead of being dropped.
    """
    if moderate:
        stem = stem.replace("&", " and ")

    tokens: list[tuple[str, str]] = []
    i, n = 0, len(stem)
    while i < n:
        ch = stem[i]
        if ch.isspace():
            j = i + 1
            while j < n and stem[j].isspace():
                j += 1
            tokens.append(("sp", ""))
            i = j
        elif _is_bad(ch, moderate):
            j = i + 1
            while j < n and _is_bad(stem[j], moderate):
                j += 1
            tokens.append(("bad", ""))
            i = j
        else:
            tokens.append(("c", ch))
            i += 1

    out: list[str] = []
    for idx, (kind, ch) in enumerate(tokens):
        if kind == "c":
            out.append(ch)
        elif kind == "sp":
            out.append("_")
        else:  # bad run: seam only when flanked by content on both sides
            prev_content = idx > 0 and tokens[idx - 1][0] == "c"
            next_content = idx + 1 < len(tokens) and tokens[idx + 1][0] == "c"
            if prev_content and next_content:
                out.append("-")

    cleaned = "".join(out)
    cleaned = re.sub(r"_+", "_", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned)
    # A mixed run like "_-_" (from " - ") must collapse to one underscore.
    cleaned = re.sub(r"(?:_-|-_)+_?", "_", cleaned)
    cleaned = cleaned.strip("_-")
    cleaned = cleaned.rstrip(" .")  # Windows: no trailing dot/space
    return cleaned


def _squash(s: str) -> str:
    """Reduce to lowercase alphanumerics for tolerant substring comparison."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _norm_token(text: str, moderate: bool) -> str:
    """Normalize a free-form artist/title into an OS-safe filename fragment."""
    return _clean_stem(text.strip(), moderate)


def clean_filename(name: str, level: str | None) -> str:
    """Return an OS-safe, tidied version of a filename.

    Goals: stay as close to the original as possible while removing characters
    that trip other software. The YouTube-ID token and the file extension are
    preserved verbatim. Whitespace runs collapse to a single underscore; runs of
    removed characters collapse to nothing, except a single dash is inserted as a
    seam when removal would otherwise concatenate two kept characters.

    level:
      - None            -> no change (original name returned)
      - 'conservative'  -> remove only filesystem-illegal/control characters
      - 'moderate'      -> also neutralize shell-hostile characters
    """
    if level is None:
        return name
    if level not in CLEAN_LEVELS:
        raise ValueError(f"level must be one of {CLEAN_LEVELS} or None")
    moderate = level == "moderate"

    ext = Path(name).suffix
    stem = name[: len(name) - len(ext)] if ext else name

    proto, id_text = _split_id(stem)
    cleaned = _splice_id(_clean_stem(proto, moderate)[:200], id_text)

    if not cleaned:
        cleaned = extract_youtube_id(name)[0] or "Unknown"
    if cleaned.lower() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned + ext


def enhance_filename(
    name: str,
    artist: str | None,
    title: str | None,
    level: str | None,
) -> str:
    """Prepend the known artist/title to a filename, keeping ID + extension.

    Produces roughly `Artist_Title_<original-rest-including-id>.ext`. Each of
    the artist/title fragments is normalized to be OS-safe and is skipped when
    it is already present in the name (tolerant, case-insensitive match), so
    already-descriptive files are not doubled up. The original tail is scrubbed
    per `level` (or left as-is when `level` is None), while the injected
    fragments are always sanitized.
    """
    moderate = level == "moderate"

    ext = Path(name).suffix
    stem = name[: len(name) - len(ext)] if ext else name

    # Work on the sentinel form so the ID survives scrubbing and collapsing.
    proto, id_text = _split_id(stem)
    proto = _clean_stem(proto, moderate) if level else proto

    # Duplication guard compares against the tail with the ID excluded (the
    # sentinel squashes away), so an ID never falsely matches an artist/title.
    seen = _squash(proto)
    parts: list[str] = []
    for token in (artist, title):
        if not token:
            continue
        norm = _norm_token(token, moderate)
        squashed = _squash(norm)
        if not squashed or squashed in seen:
            continue  # missing after normalization, or already present
        parts.append(norm)
        seen += squashed  # guard against artist == title duplication

    if not parts:
        return clean_filename(name, level) if level else name

    prefix = "_".join(parts)
    tail = proto.lstrip("_-")
    combined = f"{prefix}_{tail}" if tail else prefix
    combined = _splice_id(combined.strip("_-")[:200], id_text)

    if not combined:
        combined = extract_youtube_id(name)[0] or "Unknown"
    if combined.lower() in _RESERVED:
        combined = "_" + combined
    return combined + ext


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


def uses_supplied_title(reason: str | None, title: str | None) -> bool:
    """True when a decision carries a user-supplied title from a video override."""
    return reason == "video override" and bool(title and title.strip())


def destination_filename(
    artist: str,
    title: str | None,
    youtube_id: str,
    original_name: str,
    *,
    include_artist: bool = True,
    clean_names: str | None = None,
) -> str:
    """Build ``Artist - Title [id].ext`` from a decision.

    ``include_artist`` selects ``Title [id].ext`` when an artist folder already
    carries the artist and the caller asked to omit the prefix. A blank title
    keeps the artist in the name either way (``Artist [id].ext``).

    ``clean_names`` scrubs the constructed name. ``None`` applies only
    ``sanitize_component``, so spaces and punctuation the user kept survive.
    The bracketed YouTube id and the original extension are always present.
    """
    ext = Path(original_name).suffix
    artist_text = artist.strip()
    title_text = title.strip() if title else ""
    if include_artist or not title_text:
        label = f"{artist_text} - {title_text}" if title_text else artist_text
    else:
        label = title_text

    id_token = f" [{youtube_id}]"
    if clean_names:
        return clean_filename(f"{label}{id_token}{ext}", clean_names)

    stem = sanitize_component(label, fallback="Unknown")
    room = 200 - len(id_token)
    if len(stem) > room:
        stem = stem[:room].rstrip(" .") or youtube_id
    return f"{stem}{id_token}{ext}"


def _target_path(
    target_root: Path,
    genre: str | None,
    artist: str,
    filename: str,
    clean_names: str | None = None,
    title: str | None = None,
    enhance_names: bool = False,
    include_artist_folder: bool = True,
    youtube_id: str | None = None,
    reason: str | None = None,
    omit_artist_from_filename: bool = False,
) -> Path:
    # enhance_names is accepted for compatibility. Artist and title are already
    # part of every destination name, so the flag does not change the result.
    del enhance_names
    dest = target_root
    if genre:
        dest = dest / sanitize_component(genre)
    if include_artist_folder:
        dest = dest / sanitize_component(artist)
    title_text = title.strip() if title else ""
    include_artist = not (
        omit_artist_from_filename and include_artist_folder and title_text
    )
    supplied = uses_supplied_title(reason, title_text)
    if youtube_id:
        fname = destination_filename(
            artist, title, youtube_id, filename,
            include_artist=include_artist,
            clean_names=None if supplied else clean_names,
        )
    elif clean_names:
        fname = clean_filename(filename, clean_names)
    else:
        fname = filename
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
    clean_names: str | None = None,
    enhance_names: bool = False,
    min_artist_files: int = 1,
    omit_artist_from_filename: bool = False,
) -> list[PlannedMove]:
    """Compute destination paths for every moved file.

    min_artist_files controls when an ``<Artist>/`` folder is created: an artist
    with fewer than this many moved files in the plan is flattened one level up
    (its files land in ``<target>/<genre>/`` instead of
    ``<target>/<genre>/<Artist>/``, or directly in ``<target>/`` when there is
    no genre). The default of 1 always creates the artist folder.

    Destination names are ``Artist - Title [id].ext``. When
    omit_artist_from_filename is set, files that land in an artist folder use
    ``Title [id].ext`` instead. Flattened files keep the artist in the name.
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
            if r["action"] == "move" and r["artist"]:
                artist_counts[r["artist"]] = artist_counts.get(r["artist"], 0) + 1

        for r in rows:
            to_path: str | None = None
            if r["action"] == "move" and r["artist"]:
                include_artist_folder = (
                    artist_counts.get(r["artist"], 0) >= min_artist_files
                )
                dest = _target_path(
                    target_root, r["genre"], r["artist"], r["filename"],
                    clean_names, title=r["title"], enhance_names=enhance_names,
                    include_artist_folder=include_artist_folder,
                    youtube_id=r["youtube_id"], reason=r["reason"],
                    omit_artist_from_filename=omit_artist_from_filename,
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
