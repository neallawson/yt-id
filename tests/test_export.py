import csv
import json

from ytid import cli, db, export

NOW = "2026-01-01T00:00:00+00:00"


def _seed_video(db_path, *, yid, filename, resolve_status="resolved",
                fetch_status="ok", ytdlp_version="2026.01.01"):
    with db.session(db_path) as conn:
        conn.execute(
            "INSERT INTO videos (src_path, filename, ext, youtube_id, id_source, "
            "resolve_status, fetch_status, ytdlp_version, first_seen_at, last_seen_at) "
            "VALUES (?, ?, '.mp4', ?, 'bracket', ?, ?, ?, ?, ?)",
            (f"/src/{filename}", filename, yid, resolve_status, fetch_status,
             ytdlp_version, NOW, NOW),
        )


def _seed_decision(db_path, *, yid, artist, title, genre, action,
                   confidence=0.9, reason="test", target_path=None):
    with db.session(db_path) as conn:
        conn.execute(
            "INSERT INTO decisions (youtube_id, artist, title, genre, target_path, "
            "action, confidence, reason, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (yid, artist, title, genre, target_path, action, confidence, reason, NOW),
        )


def _seed_move(db_path, *, yid, to_path, status="done"):
    with db.session(db_path) as conn:
        conn.execute(
            "INSERT INTO moves (youtube_id, run_id, from_path, to_path, status, "
            "applied_at) VALUES (?, 'run1', ?, ?, ?, ?)",
            (yid, f"/src/{yid}.mp4", to_path, status, NOW),
        )


# --- build_ledger --------------------------------------------------------


def test_ledger_joins_video_decision_and_move(tmp_path):
    dbp = str(tmp_path / "t.db")
    _seed_video(dbp, yid="aaaaaaaaaaa", filename="A.mp4")
    _seed_decision(dbp, yid="aaaaaaaaaaa", artist="Nazz", title="Open My Eyes",
                   genre="rock", action="move", target_path="/tgt/rock/Nazz/A.mp4")
    _seed_move(dbp, yid="aaaaaaaaaaa", to_path="/tgt/rock/Nazz/A.mp4")

    ledger = export.build_ledger(dbp)
    assert len(ledger) == 1
    row = ledger[0]
    assert row["youtube_id"] == "aaaaaaaaaaa"
    assert row["original_filename"] == "A.mp4"
    assert row["artist"] == "Nazz"
    assert row["title"] == "Open My Eyes"
    assert row["genre"] == "rock"
    assert row["action"] == "move"
    assert row["moved_to"] == "/tgt/rock/Nazz/A.mp4"
    assert row["move_status"] == "done"
    assert row["ytdlp_version"] == "2026.01.01"


def test_ledger_includes_unresolved_and_unmoved(tmp_path):
    dbp = str(tmp_path / "t.db")
    # A resolved-but-review file (no move) and an unidentified file (no id).
    _seed_video(dbp, yid="bbbbbbbbbbb", filename="B.mp4", fetch_status="ok")
    _seed_decision(dbp, yid="bbbbbbbbbbb", artist=None, title=None, genre=None,
                   action="review", confidence=0.0, reason="unknown artist")
    with db.session(dbp) as conn:
        conn.execute(
            "INSERT INTO videos (src_path, filename, ext, id_source, "
            "resolve_status, fetch_status, first_seen_at, last_seen_at) "
            "VALUES ('/src/x.mp4', 'x.mp4', '.mp4', 'none', 'unresolved', "
            "'pending', ?, ?)",
            (NOW, NOW),
        )

    ledger = export.build_ledger(dbp)
    by_file = {r["original_filename"]: r for r in ledger}
    assert set(by_file) == {"B.mp4", "x.mp4"}
    assert by_file["B.mp4"]["action"] == "review"
    assert by_file["B.mp4"]["moved_to"] is None
    # Unidentified file: no decision, no id.
    assert by_file["x.mp4"]["youtube_id"] is None
    assert by_file["x.mp4"]["action"] is None
    assert by_file["x.mp4"]["resolve_status"] == "unresolved"


def test_ledger_reports_latest_move_after_undo(tmp_path):
    dbp = str(tmp_path / "t.db")
    _seed_video(dbp, yid="ccccccccccc", filename="C.mp4")
    _seed_decision(dbp, yid="ccccccccccc", artist="Band", title="T", genre="rock",
                   action="move")
    _seed_move(dbp, yid="ccccccccccc", to_path="/tgt/C.mp4", status="done")
    _seed_move(dbp, yid="ccccccccccc", to_path="/tgt/C.mp4", status="rolled_back")

    ledger = export.build_ledger(dbp)
    assert ledger[0]["move_status"] == "rolled_back"


# --- build_meta ----------------------------------------------------------


def test_meta_counts_actions_and_moves(tmp_path):
    dbp = str(tmp_path / "t.db")
    _seed_video(dbp, yid="aaaaaaaaaaa", filename="A.mp4")
    _seed_decision(dbp, yid="aaaaaaaaaaa", artist="N", title="T", genre="rock",
                   action="move")
    _seed_move(dbp, yid="aaaaaaaaaaa", to_path="/tgt/A.mp4")
    _seed_video(dbp, yid="bbbbbbbbbbb", filename="B.mp4")
    _seed_decision(dbp, yid="bbbbbbbbbbb", artist=None, title=None, genre=None,
                   action="review", confidence=0.0)

    ledger = export.build_ledger(dbp)
    meta = export.build_meta(dbp, ledger)
    assert meta["total"] == 2
    assert meta["moved"] == 1
    assert meta["by_action"] == {"move": 1, "review": 1}
    assert meta["tool"] == "yt-id"


# --- write_ledger --------------------------------------------------------


def test_write_ledger_both_formats(tmp_path):
    dbp = str(tmp_path / "t.db")
    _seed_video(dbp, yid="aaaaaaaaaaa", filename="A.mp4")
    _seed_decision(dbp, yid="aaaaaaaaaaa", artist="Nazz", title="T", genre="rock",
                   action="move")
    _seed_move(dbp, yid="aaaaaaaaaaa", to_path="/tgt/A.mp4")

    ledger = export.build_ledger(dbp)
    meta = export.build_meta(dbp, ledger)
    written = export.write_ledger(ledger, tmp_path / "ledger", "both", meta=meta)

    assert set(written) == {"json", "csv"}

    payload = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert payload["meta"]["total"] == 1
    assert payload["ledger"][0]["artist"] == "Nazz"

    with open(tmp_path / "ledger.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["artist"] == "Nazz"
    assert rows[0]["moved_to"] == "/tgt/A.mp4"
    assert list(rows[0].keys()) == export.LEDGER_FIELDS


def test_write_ledger_csv_only(tmp_path):
    written = export.write_ledger([], tmp_path / "out", "csv")
    assert set(written) == {"csv"}
    assert not (tmp_path / "out.json").exists()
    assert (tmp_path / "out.csv").exists()


def test_write_ledger_rejects_bad_format(tmp_path):
    try:
        export.write_ledger([], tmp_path / "out", "xml")
    except ValueError:
        return
    raise AssertionError("expected ValueError for bad format")


# --- CLI integration -----------------------------------------------------


def test_cli_export_writes_both_and_reports(tmp_path, capsys):
    dbp = str(tmp_path / "t.db")
    _seed_video(dbp, yid="aaaaaaaaaaa", filename="A.mp4")
    _seed_decision(dbp, yid="aaaaaaaaaaa", artist="Nazz", title="T", genre="rock",
                   action="move")
    _seed_move(dbp, yid="aaaaaaaaaaa", to_path="/tgt/A.mp4")

    out = tmp_path / "ledger"
    rc = cli.main(["--db", dbp, "export", "--out", str(out)])
    assert rc == 0
    assert (tmp_path / "ledger.json").exists()
    assert (tmp_path / "ledger.csv").exists()
    printed = capsys.readouterr().out
    assert "1 row(s)" in printed
    assert "1 moved" in printed


def test_cli_export_json_only(tmp_path):
    dbp = str(tmp_path / "t.db")
    _seed_video(dbp, yid="aaaaaaaaaaa", filename="A.mp4")
    _seed_decision(dbp, yid="aaaaaaaaaaa", artist="Nazz", title="T", genre="rock",
                   action="move")

    out = tmp_path / "audit"
    rc = cli.main(["--db", dbp, "export", "--out", str(out), "--format", "json"])
    assert rc == 0
    assert (tmp_path / "audit.json").exists()
    assert not (tmp_path / "audit.csv").exists()
