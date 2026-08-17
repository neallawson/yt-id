import os

from ytid import resolve as resolve_mod
from ytid import scan as scan_mod


def _touch(path):
    with open(path, "w") as fh:
        fh.write("x")


def test_scan_tracks_all_and_flags_status(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    db_path = str(tmp_path / "t.db")

    # bracket id, dash id, no id, and a duplicate of the bracket id
    _touch(src / "Song One [aaaaaaaaaaa].webm")
    _touch(src / "Song Two-bbbbbbbbbbb.mp4")
    _touch(src / "A Place With No Id.mkv")
    _touch(src / "Song One duplicate [aaaaaaaaaaa].mkv")

    counts = scan_mod.scan(src, db_path=db_path)
    assert counts["seen"] == 4
    assert counts["new"] == 4

    tally = resolve_mod.counts_by_status(db_path=db_path)
    assert tally.get("resolved") == 2      # bracket + dash
    assert tally.get("unresolved") == 1    # no id
    assert tally.get("duplicate") == 1     # second file with same id


def test_scan_is_idempotent(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    db_path = str(tmp_path / "t.db")
    _touch(src / "Song Two-bbbbbbbbbbb.mp4")

    scan_mod.scan(src, db_path=db_path)
    counts = scan_mod.scan(src, db_path=db_path)
    assert counts["new"] == 0
    assert counts["updated"] == 1


def test_manual_resolve_survives_rescan(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    db_path = str(tmp_path / "t.db")
    f = src / "A Place With No Id.mkv"
    _touch(f)

    scan_mod.scan(src, db_path=db_path)
    resolve_mod.assign_id("ccccccccccc", path=str(f), db_path=db_path)

    # Rescan must not wipe the manual assignment.
    scan_mod.scan(src, db_path=db_path)
    rows = resolve_mod.list_videos(db_path=db_path, statuses=(), include_dash=False)
    # list with empty statuses returns nothing; query directly instead
    all_rows = resolve_mod.list_videos(
        db_path=db_path, statuses=("resolved", "unresolved", "duplicate", "ignored")
    )
    match = [r for r in all_rows if r["src_path"] == str(f)][0]
    assert match["youtube_id"] == "ccccccccccc"
    assert match["id_source"] == "manual"
    assert match["resolve_status"] == "resolved"
