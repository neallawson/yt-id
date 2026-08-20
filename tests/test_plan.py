from pathlib import Path

from ytid import db
from ytid.plan import build_plan, sanitize_component


def test_illegal_chars_replaced():
    assert sanitize_component("AC/DC") == "AC_DC"


def test_trailing_dot_and_space_stripped():
    assert sanitize_component("  The Band. ") == "The Band"


def test_reserved_name_falls_back():
    assert sanitize_component("con") == "Unknown"


def test_empty_falls_back():
    assert sanitize_component("   ") == "Unknown"


def test_normal_unicode_preserved():
    assert sanitize_component("Motörhead") == "Motörhead"


def _seed_move(db_path, yid, filename, artist, genre):
    now = "2026-01-01T00:00:00+00:00"
    with db.session(db_path) as conn:
        conn.execute(
            "INSERT INTO videos (src_path, filename, ext, youtube_id, "
            "resolve_status, fetch_status, first_seen_at, last_seen_at) "
            "VALUES (?, ?, '.mp4', ?, 'resolved', 'ok', ?, ?)",
            (f"/src/{filename}", filename, yid, now, now),
        )
        conn.execute(
            "INSERT INTO decisions (youtube_id, artist, title, genre, "
            "target_path, action, confidence, reason, decided_at) "
            "VALUES (?, ?, NULL, ?, NULL, 'move', 0.9, 'test', ?)",
            (yid, artist, genre, now),
        )


def test_plan_with_genre_nests_under_genre(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(db_path, "aaaaaaaaaaa", "A.mp4", "Nazz", "rock")
    planned = build_plan(tmp_path / "out", db_path=db_path)
    pm = next(p for p in planned if p.youtube_id == "aaaaaaaaaaa")
    assert Path(pm.to_path) == tmp_path / "out" / "rock" / "Nazz" / "A.mp4"


def test_plan_without_genre_places_directly_under_artist(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(db_path, "bbbbbbbbbbb", "B.mp4", "Nazz", None)
    planned = build_plan(tmp_path / "out", db_path=db_path)
    pm = next(p for p in planned if p.youtube_id == "bbbbbbbbbbb")
    assert Path(pm.to_path) == tmp_path / "out" / "Nazz" / "B.mp4"
