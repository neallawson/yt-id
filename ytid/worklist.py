"""The working-directory file of record: ``ytid.yaml``.

A single YAML file, kept next to ``ytid.db`` in the folder you run from, lists
everything the automated pipeline could not resolve on its own and lets you
supply the missing ground truth in one place:

- ``videos:``       a known YouTube ID whose metadata fetch came back empty
                    (unavailable/error) or that classify routed to review. Fill
                    in artist/title (genre optional) to force a clean decision.
- ``unidentified:`` a file with no detectable YouTube ID. Supply the id plus
                    metadata; classify promotes the row to resolved and applies
                    your override.
- ``artists:``      optional artist -> genre shortcuts applied to every video by
                    that artist.

The file is *regenerated non-destructively*: ``sync_worklist`` only appends
stubs for newly-seen problems and never overwrites fields you have edited.
``apply_worklist`` reads it back, assigns any supplied ids, and returns the
overrides for classify to merge (worklist wins over ``overrides.yaml``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import config, db

WORKLIST_FILE = "ytid.yaml"

# Fields written with forced double-quotes so the user can type free-form text
# (colons, brackets, bare words like "yes") without hand-adding quotes.
_QUOTED_FIELDS = ("artist", "title")


class _QuotedStr(str):
    """A string that always serializes as a double-quoted YAML scalar."""


class _WorklistDumper(yaml.SafeDumper):
    """SafeDumper that renders _QuotedStr values in double-quote style."""


def _represent_quoted(dumper: yaml.Dumper, data: _QuotedStr):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


_WorklistDumper.add_representer(_QuotedStr, _represent_quoted)

_HEADER = """\
# ytid.yaml -- file of record for this folder (auto-managed; safe to edit).
#
# Fill in the blanks, then run:  yt-id classify
# Regenerated non-destructively: your entries are preserved and new problems
# are appended. `genre` is optional; `action` is one of move|review|skip.
#
# videos:        known YouTube ID that needs artist/title/genre -- fetch had no
#                clean data, or classify wasn't confident enough to move it.
# unidentified:  no ID detected -- supply youtube_id plus metadata.
# artists:       optional artist -> genre shortcuts applied to all their videos.

"""


def _nz(value: Any) -> str | None:
    """Return a stripped non-empty string, else None (blanks count as unset)."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {}
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{p} must contain a mapping at the top level")
    return data


def _force_quoted_fields(data: dict[str, Any]) -> None:
    """Wrap artist/title values (incl. blanks) so they dump double-quoted."""
    entries: list[Any] = list((data.get("videos") or {}).values())
    entries.extend(data.get("unidentified") or [])
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for key in _QUOTED_FIELDS:
            if key in entry and entry[key] is not None:
                entry[key] = _QuotedStr(entry[key])


def _write(path: str | Path, data: dict[str, Any]) -> None:
    _force_quoted_fields(data)
    body = yaml.dump(
        data, Dumper=_WorklistDumper, sort_keys=False, allow_unicode=True,
        default_flow_style=False,
    )
    Path(path).write_text(_HEADER + body, encoding="utf-8")


def _scalar(node: Any) -> str:
    """Return a mapping value node's scalar text ('' for missing/non-scalar)."""
    return node.value if isinstance(node, yaml.ScalarNode) else ""


def _entry_from_node(map_node: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(map_node, yaml.MappingNode):
        for key, val in map_node.value:
            out[key.value] = _scalar(val)
    return out


def read_entries(path: str | Path = WORKLIST_FILE) -> dict[str, list[dict]]:
    """Parse ``ytid.yaml`` preserving 1-based line numbers for each entry.

    Unlike ``pending_items`` (which reads the DB), this reflects the *file* as
    the user sees it, so blank fields and editor line numbers are available for
    ``worklist --list`` filtering. Each returned dict carries a ``line`` key.
    """
    empty: dict[str, list[dict]] = {"videos": [], "unidentified": []}
    p = Path(path)
    if not p.is_file():
        return empty
    with p.open("r", encoding="utf-8") as fh:
        root = yaml.compose(fh)
    if not isinstance(root, yaml.MappingNode):
        return empty
    top = {k.value: v for k, v in root.value}

    vids = top.get("videos")
    if isinstance(vids, yaml.MappingNode):
        for key, val in vids.value:
            entry = _entry_from_node(val)
            entry["youtube_id"] = key.value
            entry["line"] = key.start_mark.line + 1
            empty["videos"].append(entry)

    unid = top.get("unidentified")
    if isinstance(unid, yaml.SequenceNode):
        for item in unid.value:
            entry = _entry_from_node(item)
            entry["line"] = item.start_mark.line + 1
            empty["unidentified"].append(entry)
    return empty


def resolved_actions(db_path: str | Path = db.DEFAULT_DB_PATH) -> dict[str, str]:
    """Map youtube_id -> the latest classify decision action (move|review|skip)."""
    with db.session(db_path) as conn:
        return {
            r["youtube_id"]: r["action"]
            for r in conn.execute("SELECT youtube_id, action FROM decisions")
        }


def pending_items(db_path: str | Path = db.DEFAULT_DB_PATH) -> dict[str, list[dict]]:
    """Return the two problem buckets the worklist tracks.

    ``videos``       -- resolved rows with a known id that still lack usable
                        metadata (fetch unavailable/error) or whose current
                        decision is 'review'.
    ``unidentified`` -- rows with no id yet (scan unresolved/duplicate).
    """
    with db.session(db_path) as conn:
        videos = [
            dict(r)
            for r in conn.execute(
                """
                SELECT v.youtube_id, v.filename, v.fetch_status,
                       d.artist AS artist, d.title AS title, d.genre AS genre
                  FROM videos v
                  LEFT JOIN decisions d ON d.youtube_id = v.youtube_id
                 WHERE v.resolve_status = 'resolved' AND v.youtube_id IS NOT NULL
                   AND (v.fetch_status IN ('unavailable', 'error')
                        OR d.action = 'review')
                 ORDER BY v.filename
                """
            ).fetchall()
        ]
        unidentified = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, filename, resolve_status
                  FROM videos
                 WHERE resolve_status IN ('unresolved', 'duplicate')
                   AND youtube_id IS NULL
                 ORDER BY filename
                """
            ).fetchall()
        ]
    return {"videos": videos, "unidentified": unidentified}


def _video_stub(
    filename: str,
    artist: str | None = None,
    title: str | None = None,
    genre: str | None = None,
) -> dict[str, str]:
    # Pre-fill classify's best guess so the user only corrects what's wrong
    # rather than re-typing every field.
    return {
        "file": filename,
        "artist": artist or "",
        "title": title or "",
        "genre": genre or "",
        "action": "",
    }


def _unidentified_stub(filename: str) -> dict[str, str]:
    return {
        "file": filename,
        "youtube_id": "",
        "artist": "",
        "title": "",
        "genre": "",
        "action": "",
    }


def sync_worklist(
    db_path: str | Path = db.DEFAULT_DB_PATH,
    path: str | Path = WORKLIST_FILE,
) -> dict[str, int | bool]:
    """Merge current problems into ``ytid.yaml`` without clobbering user edits.

    New ``videos`` ids and new ``unidentified`` filenames are appended as blank
    stubs; existing entries are left exactly as-is. The file is only created
    when there is something to record (or it already exists).
    """
    existed = Path(path).is_file()
    data = _load(path)
    videos = data.get("videos")
    data["videos"] = videos if isinstance(videos, dict) else {}
    unident = data.get("unidentified")
    data["unidentified"] = unident if isinstance(unident, list) else []
    if "artists" not in data or not isinstance(data.get("artists"), dict):
        data["artists"] = data.get("artists") or {}

    problems = pending_items(db_path)

    added_videos = 0
    for row in problems["videos"]:
        yid = row["youtube_id"]
        if yid not in data["videos"]:
            data["videos"][yid] = _video_stub(
                row["filename"], row.get("artist"), row.get("title"), row.get("genre")
            )
            added_videos += 1

    existing_files = {
        e.get("file") for e in data["unidentified"] if isinstance(e, dict)
    }
    added_unident = 0
    for row in problems["unidentified"]:
        if row["filename"] not in existing_files:
            data["unidentified"].append(_unidentified_stub(row["filename"]))
            added_unident += 1

    total = len(data["videos"]) + len(data["unidentified"])
    wrote = bool(total) or existed
    if wrote:
        _write(path, data)

    return {
        "added_videos": added_videos,
        "added_unidentified": added_unident,
        "pending_videos": len(data["videos"]),
        "pending_unidentified": len(data["unidentified"]),
        "wrote": wrote,
    }


def _make_override(spec: dict[str, Any]) -> config.VideoOverride | None:
    artist = _nz(spec.get("artist"))
    title = _nz(spec.get("title"))
    genre = _nz(spec.get("genre"))
    action = _nz(spec.get("action"))
    if not any((artist, title, genre, action)):
        return None
    return config.VideoOverride(
        artist=artist,
        title=title,
        genre=genre.lower() if genre else None,
        action=action,
    )


def _assign_by_filename(conn, filename: str, youtube_id: str) -> bool:
    """Promote an unidentified row (matched by filename) to a resolved id.

    Returns True when a row was assigned (or already carries this id), False if
    no unique unresolved match exists or the id clashes with another row.
    """
    if not filename:
        return False
    row = conn.execute(
        "SELECT id FROM videos "
        " WHERE filename = ? AND (youtube_id IS NULL OR youtube_id = '')",
        (filename,),
    ).fetchone()
    if row is None:
        # Already assigned on a prior run? Treat as success (idempotent).
        done = conn.execute(
            "SELECT 1 FROM videos WHERE filename = ? AND youtube_id = ?",
            (filename, youtube_id),
        ).fetchone()
        return done is not None

    clash = conn.execute(
        "SELECT 1 FROM videos WHERE youtube_id = ? AND id <> ?",
        (youtube_id, row["id"]),
    ).fetchone()
    if clash is not None:
        return False

    conn.execute(
        """
        UPDATE videos
           SET youtube_id = ?, detected_id = ?, id_source = 'manual',
               resolve_status = 'resolved', last_seen_at = ?
         WHERE id = ?
        """,
        (youtube_id, youtube_id, _now(), row["id"]),
    )
    return True


def apply_worklist(
    db_path: str | Path = db.DEFAULT_DB_PATH,
    path: str | Path = WORKLIST_FILE,
) -> tuple[config.Overrides, dict[str, int]]:
    """Read ``ytid.yaml``, assign supplied ids, and return overrides to merge.

    Side effect: ``unidentified`` entries that now carry a ``youtube_id`` are
    assigned to their DB row (promoted to resolved). The returned Overrides is
    meant to be layered on top of the packaged/user ``overrides.yaml`` so the
    working-dir file of record wins.
    """
    data = _load(path)
    stats = {"assigned": 0, "unmatched": 0, "video_overrides": 0}

    artists: dict[str, str] = {}
    for name, genre in (data.get("artists") or {}).items():
        g = _nz(genre)
        if _nz(name) and g:
            artists[str(name)] = g.lower()

    videos: dict[str, config.VideoOverride] = {}
    for vid, spec in (data.get("videos") or {}).items():
        vo = _make_override(spec or {})
        if vo is not None:
            videos[str(vid)] = vo
            stats["video_overrides"] += 1

    with db.session(db_path) as conn:
        for entry in data.get("unidentified") or []:
            if not isinstance(entry, dict):
                continue
            yid = _nz(entry.get("youtube_id"))
            if not yid:
                continue
            if _assign_by_filename(conn, entry.get("file"), yid):
                stats["assigned"] += 1
            else:
                stats["unmatched"] += 1
            vo = _make_override(entry)
            if vo is not None:
                videos[yid] = vo
                stats["video_overrides"] += 1

    return config.Overrides(artists=artists, videos=videos), stats
