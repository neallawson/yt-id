"""Turn cached metadata + config into a per-video decision.

This stage is pure and network-free, so it can be re-run infinitely as you
curate `config/overrides.yaml`. The decision model (v1, no MusicBrainz):

    if per-video override exists:        use it
    elif structured artist & track:      use them; genre via override/struct
    elif artist recognizable in title:   use it; genre via override map
    else:                                review_required

Genre in v1 comes from the artist override map or a structured `genre` field
normalized to a coarse bucket. Anything without a confident genre is routed to
review rather than guessed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .config import Config, load_config

# Words/patterns that mark a title as non-song context; used to lower confidence.
_NOISE_RE = re.compile(
    r"\b(official|video|audio|visuali[sz]er|lyric[s]?|live|remaster(ed)?|hd|4k|"
    r"full concert|totp|top of the pops|the midnight special|mtv)\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_TOPIC_SUFFIX = " - Topic"
_VEVO_RE = re.compile(r"vevo$", re.IGNORECASE)

# Separators seen between artist and title, including fullwidth lookalikes.
_SEPARATORS = [" - ", " – ", " — ", "｜", " | ", "/", "⧸", "~", "："]


@dataclass
class Decision:
    youtube_id: str
    artist: str | None
    title: str | None
    genre: str | None
    action: str  # move|review|skip
    confidence: float
    reason: str


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _channel_artist(meta: dict) -> str | None:
    """Derive an artist from the channel/uploader for official channels."""
    for key in ("channel", "uploader"):
        name = _clean(meta.get(key))
        if not name:
            continue
        if name.endswith(_TOPIC_SUFFIX):
            return _clean(name[: -len(_TOPIC_SUFFIX)])
        if _VEVO_RE.search(name):
            return _clean(_VEVO_RE.sub("", name))
    return None


def _split_title(title: str) -> tuple[str | None, str | None]:
    """Best-effort split of 'Artist - Title' style strings."""
    for sep in _SEPARATORS:
        if sep in title:
            left, right = title.split(sep, 1)
            return _clean(left), _clean(right)
    return None, None


def _structured_fields(meta: dict) -> tuple[str | None, str | None]:
    """Prefer yt-dlp's music-panel fields when present."""
    artist = meta.get("artist")
    if not artist and isinstance(meta.get("artists"), list) and meta["artists"]:
        artist = meta["artists"][0]
    if not artist:
        artist = meta.get("creator")
    track = meta.get("track")
    return _clean(artist), _clean(track)


def decide(
    youtube_id: str,
    meta: dict | None,
    cfg: Config,
    allow_missing_genre: bool = False,
) -> Decision:
    now_reason = []

    # 1. Per-video override wins unconditionally.
    ov = cfg.overrides.videos.get(youtube_id)
    if ov is not None:
        genre = ov.genre or cfg.overrides.artist_genre(ov.artist) or "other"
        action = ov.action or ("move" if genre != "other" else "review")
        return Decision(
            youtube_id, _clean(ov.artist), None, genre, action, 1.0,
            "video override",
        )

    if not meta:
        return Decision(
            youtube_id, None, None, None, "review", 0.0,
            "no metadata (fetch failed or pending)",
        )

    title = _clean(meta.get("title"))

    # 2. Structured music fields.
    s_artist, s_track = _structured_fields(meta)
    if s_artist and s_track:
        artist, track = s_artist, s_track
        confidence = 0.9
        now_reason.append("structured artist/track")
    else:
        # 3. Heuristic parse from title / channel.
        artist, track = (None, None)
        if title:
            a, t = _split_title(title)
            artist, track = a, t
        if not artist:
            artist = _channel_artist(meta)
            track = track or title
        confidence = 0.5 if artist else 0.0
        now_reason.append("heuristic title/channel parse" if artist else "unparseable")

    # Reduce confidence for live/compilation/documentary-style noise.
    if title and _NOISE_RE.search(title):
        confidence -= 0.15
        now_reason.append("noise markers present")

    # Genre resolution: artist override map, then structured genre field.
    genre = cfg.overrides.artist_genre(artist)
    if genre:
        now_reason.append("genre from artist override")
    else:
        raw_genre = meta.get("genre")
        genre = cfg.genre_map.to_bucket(raw_genre)
        if genre:
            now_reason.append("genre from structured field")

    # Decide the action. A confident artist is required; genre is required too
    # unless allow_missing_genre lets confident artist-only files land in /Artist.
    if artist and confidence >= 0.6 and (genre or allow_missing_genre):
        action = "move"
        if not genre:
            now_reason.append("artist-only (no genre)")
    else:
        action = "review"
        if not genre:
            now_reason.append("no confident genre")
        if not artist:
            now_reason.append("no artist")

    return Decision(
        youtube_id,
        artist,
        _clean(track) or title,
        genre,
        action,
        round(max(0.0, confidence), 2),
        "; ".join(now_reason),
    )


def list_decisions(
    db_path: str | Path = db.DEFAULT_DB_PATH,
    action: str = "review",
) -> list[dict]:
    """Return classify decisions joined with file info, for a manual worklist.

    action='review' (default) is the manual-handling bucket. Pass 'all' to list
    every decision, or a specific action ('move'|'skip'|'review').
    """
    sql = (
        "SELECT d.youtube_id, d.action, d.artist, d.title, d.genre, d.reason, "
        "       d.confidence, v.filename, v.src_path, v.fetch_status "
        "  FROM decisions d "
        "  JOIN videos v ON v.youtube_id = d.youtube_id "
    )
    params: list[str] = []
    if action != "all":
        sql += " WHERE d.action = ? "
        params.append(action)
    sql += " ORDER BY d.action, v.filename"
    with db.session(db_path) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def classify_all(
    db_path: str | Path = db.DEFAULT_DB_PATH,
    config_dir: str | Path = "config",
    allow_missing_genre: bool = False,
) -> dict[str, int]:
    """Classify every known video and upsert into the decisions table.

    When allow_missing_genre is True, a confidently-identified artist with no
    genre is moved into `<target>/<Artist>/` (genre folder omitted) rather than
    routed to review. Low-confidence artists still go to review.
    """
    cfg = load_config(config_dir)
    counts = {"total": 0, "move": 0, "review": 0, "skip": 0}
    now = datetime.now(timezone.utc).isoformat()

    with db.session(db_path) as conn:
        rows = conn.execute(
            "SELECT youtube_id, raw_json FROM videos "
            "WHERE resolve_status = 'resolved' AND youtube_id IS NOT NULL"
        ).fetchall()
        for row in rows:
            meta = json.loads(row["raw_json"]) if row["raw_json"] else None
            d = decide(row["youtube_id"], meta, cfg, allow_missing_genre)
            conn.execute(
                """
                INSERT INTO decisions
                    (youtube_id, artist, title, genre, target_path, action,
                     confidence, reason, decided_at)
                VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?)
                ON CONFLICT(youtube_id) DO UPDATE SET
                    artist=excluded.artist, title=excluded.title,
                    genre=excluded.genre, action=excluded.action,
                    confidence=excluded.confidence, reason=excluded.reason,
                    decided_at=excluded.decided_at
                """,
                (d.youtube_id, d.artist, d.title, d.genre, d.action,
                 d.confidence, d.reason, now),
            )
            counts["total"] += 1
            counts[d.action] = counts.get(d.action, 0) + 1

    return counts
