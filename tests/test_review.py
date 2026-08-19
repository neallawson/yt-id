from ytid import classify as classify_mod
from ytid import db


def _seed(db_path):
    now = "2026-01-01T00:00:00+00:00"
    with db.session(db_path) as conn:
        for yid, fname in [("aaaaaaaaaaa", "A.mp4"),
                           ("bbbbbbbbbbb", "B.mp4"),
                           ("ccccccccccc", "C.mp4")]:
            conn.execute(
                "INSERT INTO videos (src_path, filename, ext, youtube_id, "
                "resolve_status, fetch_status, first_seen_at, last_seen_at) "
                "VALUES (?, ?, '.mp4', ?, 'resolved', 'ok', ?, ?)",
                (f"/src/{fname}", fname, yid, now, now),
            )
        decisions = [
            ("aaaaaaaaaaa", "move", "Artist A", 0.9, "confident"),
            ("bbbbbbbbbbb", "review", None, 0.0, "no metadata"),
            ("ccccccccccc", "review", None, 0.5, "no artist"),
        ]
        for yid, action, artist, conf, reason in decisions:
            conn.execute(
                "INSERT INTO decisions (youtube_id, artist, title, genre, "
                "target_path, action, confidence, reason, decided_at) "
                "VALUES (?, ?, NULL, NULL, NULL, ?, ?, ?, ?)",
                (yid, artist, action, conf, reason, now),
            )


def test_list_decisions_defaults_to_review(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed(db_path)
    rows = classify_mod.list_decisions(db_path=db_path)
    assert {r["youtube_id"] for r in rows} == {"bbbbbbbbbbb", "ccccccccccc"}
    assert all(r["action"] == "review" for r in rows)
    # joined file info is present
    assert all(r["filename"] for r in rows)


def test_list_decisions_action_filter(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed(db_path)
    assert len(classify_mod.list_decisions(db_path=db_path, action="move")) == 1
    assert len(classify_mod.list_decisions(db_path=db_path, action="skip")) == 0
    assert len(classify_mod.list_decisions(db_path=db_path, action="all")) == 3
