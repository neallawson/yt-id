import json

from ytid import db, fetch
from ytid.metadata import slim_metadata
from ytid.ytdlp_client import FetchResult

NOW = "2026-01-01T00:00:00+00:00"


def _full_dump():
    return {
        "id": "aaaaaaaaaaa",
        "title": "Atomic Rooster - The Devils Answer",
        "channel": "Atomic Rooster",
        "uploader": "Atomic Rooster",
        "artist": "Atomic Rooster",
        "artists": ["Atomic Rooster"],
        "creator": "Atomic Rooster",
        "track": "The Devils Answer",
        "album": "Death Walks Behind You",
        "genre": "Rock",
        "duration": 212,
        "upload_date": "20120101",
        "webpage_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "formats": [{"format_id": "18", "url": "https://example.invalid/v"}],
        "thumbnails": [{"url": "https://example.invalid/t.jpg"}],
        "automatic_captions": {"en": [{"url": "https://example.invalid/c"}]},
        "heatmap": [{"start_time": 0, "end_time": 1, "value": 1}],
        "description": "a long description that should not be stored",
    }


def test_slim_metadata_keeps_classify_fields_and_drops_the_dump():
    slim = slim_metadata(_full_dump())
    assert slim["artist"] == "Atomic Rooster"
    assert slim["track"] == "The Devils Answer"
    assert slim["title"] == "Atomic Rooster - The Devils Answer"
    assert slim["channel"] == "Atomic Rooster"
    assert slim["album"] == "Death Walks Behind You"
    assert slim["duration"] == 212
    assert slim["upload_date"] == "20120101"
    assert "formats" not in slim
    assert "thumbnails" not in slim
    assert "automatic_captions" not in slim
    assert "heatmap" not in slim
    assert "description" not in slim


def test_slim_metadata_keeps_string_artists_only():
    slim = slim_metadata({
        "artists": ["Hall & Oates", {"name": "ignored"}],
        "title": "Maneater",
    })
    assert slim["artists"] == ["Hall & Oates"]


def test_opening_the_db_rewrites_a_full_dump(tmp_path):
    dbp = tmp_path / "ytid.db"
    fat = json.dumps(_full_dump())
    with db.session(dbp) as conn:
        conn.execute(
            "INSERT INTO videos (src_path, filename, ext, youtube_id, "
            "resolve_status, fetch_status, raw_json, first_seen_at, last_seen_at) "
            "VALUES (?, ?, '.webm', ?, 'resolved', 'ok', ?, ?, ?)",
            ("/s/a.webm", "a.webm", "aaaaaaaaaaa", fat, NOW, NOW),
        )

    # session() already opened the db, which compacts on connect.
    with db.session(dbp) as conn:
        raw = conn.execute(
            "SELECT raw_json FROM videos WHERE youtube_id = 'aaaaaaaaaaa'"
        ).fetchone()["raw_json"]
    stored = json.loads(raw)
    assert stored["track"] == "The Devils Answer"
    assert "formats" not in stored
    assert "thumbnails" not in stored

    # A second open leaves the short record alone.
    with db.session(dbp) as conn:
        again = conn.execute(
            "SELECT raw_json FROM videos WHERE youtube_id = 'aaaaaaaaaaa'"
        ).fetchone()["raw_json"]
    assert json.loads(again) == stored


def test_fetch_stores_the_short_record(tmp_path, monkeypatch):
    dbp = str(tmp_path / "ytid.db")
    with db.session(dbp) as conn:
        conn.execute(
            "INSERT INTO videos (src_path, filename, ext, youtube_id, "
            "resolve_status, fetch_status, first_seen_at, last_seen_at) "
            "VALUES (?, ?, '.webm', ?, 'resolved', 'pending', ?, ?)",
            ("/s/a.webm", "a.webm", "aaaaaaaaaaa", NOW, NOW),
        )

    def fake_fetch(youtube_id, binary="yt-dlp", timeout=60.0):
        return FetchResult(
            youtube_id=youtube_id,
            status="ok",
            metadata=_full_dump(),
            ytdlp_version="test",
        )

    monkeypatch.setattr(fetch, "fetch_metadata", fake_fetch)
    counts = fetch.fetch_pending(dbp, sleep=0)
    assert counts["ok"] == 1

    with db.session(dbp) as conn:
        raw = conn.execute(
            "SELECT raw_json FROM videos WHERE youtube_id = 'aaaaaaaaaaa'"
        ).fetchone()["raw_json"]
    stored = json.loads(raw)
    assert stored["artist"] == "Atomic Rooster"
    assert "formats" not in stored
