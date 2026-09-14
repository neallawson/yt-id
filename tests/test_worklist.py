from pathlib import Path

import yaml

import json

from ytid import classify, db, worklist
from ytid import cli
from ytid.cli import build_parser

NOW = "2026-01-01T00:00:00+00:00"


def _seed_video(
    db_path,
    *,
    src_path,
    filename,
    youtube_id=None,
    resolve_status="resolved",
    fetch_status="ok",
    detected_id=None,
    id_source="none",
):
    with db.session(db_path) as conn:
        conn.execute(
            "INSERT INTO videos (src_path, filename, ext, detected_id, id_source, "
            "youtube_id, resolve_status, fetch_status, first_seen_at, last_seen_at) "
            "VALUES (?, ?, '.webm', ?, ?, ?, ?, ?, ?, ?)",
            (src_path, filename, detected_id, id_source, youtube_id,
             resolve_status, fetch_status, NOW, NOW),
        )


def _read_yaml(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --- sync_worklist -------------------------------------------------------


def test_sync_lists_fetch_failures_and_unidentified(tmp_path):
    dbp = str(tmp_path / "ytid.db")
    wl = str(tmp_path / "ytid.yaml")
    _seed_video(dbp, src_path="/s/A [8R5El2HWMIo].webm", filename="A [8R5El2HWMIo].webm",
                youtube_id="8R5El2HWMIo", fetch_status="unavailable")
    _seed_video(dbp, src_path="/s/mystery.webm", filename="mystery.webm",
                resolve_status="unresolved", fetch_status="pending")

    stats = worklist.sync_worklist(dbp, wl)
    assert stats["added_videos"] == 1
    assert stats["added_unidentified"] == 1
    assert stats["wrote"] is True

    data = _read_yaml(wl)
    assert "8R5El2HWMIo" in data["videos"]
    assert data["videos"]["8R5El2HWMIo"]["file"] == "A [8R5El2HWMIo].webm"
    assert [e["file"] for e in data["unidentified"]] == ["mystery.webm"]


def test_sync_skips_healthy_videos(tmp_path):
    dbp = str(tmp_path / "ytid.db")
    wl = str(tmp_path / "ytid.yaml")
    _seed_video(dbp, src_path="/s/ok [aaaaaaaaaaa].webm", filename="ok [aaaaaaaaaaa].webm",
                youtube_id="aaaaaaaaaaa", fetch_status="ok")

    stats = worklist.sync_worklist(dbp, wl)
    assert stats["pending_videos"] == 0
    assert stats["pending_unidentified"] == 0
    assert stats["wrote"] is False
    assert not Path(wl).exists()


def test_sync_is_non_destructive(tmp_path):
    dbp = str(tmp_path / "ytid.db")
    wl = str(tmp_path / "ytid.yaml")
    _seed_video(dbp, src_path="/s/A [8R5El2HWMIo].webm", filename="A [8R5El2HWMIo].webm",
                youtube_id="8R5El2HWMIo", fetch_status="unavailable")
    worklist.sync_worklist(dbp, wl)

    # User fills in the stub...
    data = _read_yaml(wl)
    data["videos"]["8R5El2HWMIo"]["artist"] = "Atomic Rooster"
    with open(wl, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)

    # ...and a new problem appears; re-sync must preserve the edit.
    _seed_video(dbp, src_path="/s/B [bbbbbbbbbbb].webm", filename="B [bbbbbbbbbbb].webm",
                youtube_id="bbbbbbbbbbb", fetch_status="error")
    stats = worklist.sync_worklist(dbp, wl)

    data = _read_yaml(wl)
    assert data["videos"]["8R5El2HWMIo"]["artist"] == "Atomic Rooster"
    assert "bbbbbbbbbbb" in data["videos"]
    assert stats["added_videos"] == 1


def test_sync_includes_review_decisions(tmp_path):
    dbp = str(tmp_path / "ytid.db")
    wl = str(tmp_path / "ytid.yaml")
    _seed_video(dbp, src_path="/s/R [ccccccccccc].webm", filename="R [ccccccccccc].webm",
                youtube_id="ccccccccccc", fetch_status="ok")
    with db.session(dbp) as conn:
        conn.execute(
            "INSERT INTO decisions (youtube_id, artist, title, genre, target_path, "
            "action, confidence, reason, decided_at) "
            "VALUES ('ccccccccccc', NULL, NULL, NULL, NULL, 'review', 0.3, 'x', ?)",
            (NOW,),
        )
    worklist.sync_worklist(dbp, wl)
    assert "ccccccccccc" in _read_yaml(wl)["videos"]


def test_sync_writes_artist_title_double_quoted(tmp_path):
    dbp = str(tmp_path / "ytid.db")
    wl = str(tmp_path / "ytid.yaml")
    _seed_video(dbp, src_path="/s/R [ccccccccccc].webm", filename="R [ccccccccccc].webm",
                youtube_id="ccccccccccc", fetch_status="ok")
    _seed_video(dbp, src_path="/s/mystery.webm", filename="mystery.webm",
                resolve_status="unresolved", fetch_status="pending")
    with db.session(dbp) as conn:
        conn.execute(
            "INSERT INTO decisions (youtube_id, artist, title, genre, target_path, "
            "action, confidence, reason, decided_at) "
            "VALUES ('ccccccccccc', '40 Watt Sun', 'Astoria: Part II', NULL, NULL, "
            "'review', 0.5, 'heuristic', ?)",
            (NOW,),
        )
    worklist.sync_worklist(dbp, wl)
    text = Path(wl).read_text(encoding="utf-8")
    # artist/title are forced to double quotes (incl. blank stubs); a colon in the
    # title would break an unquoted scalar, so this is the safety guarantee.
    assert 'artist: "40 Watt Sun"' in text
    assert 'title: "Astoria: Part II"' in text
    assert 'artist: ""' in text  # the blank unidentified stub
    # ...and it still round-trips to the exact strings.
    v = _read_yaml(wl)["videos"]["ccccccccccc"]
    assert v["artist"] == "40 Watt Sun"
    assert v["title"] == "Astoria: Part II"


def test_sync_prefills_parsed_guess(tmp_path):
    dbp = str(tmp_path / "ytid.db")
    wl = str(tmp_path / "ytid.yaml")
    _seed_video(dbp, src_path="/s/R [ccccccccccc].webm", filename="R [ccccccccccc].webm",
                youtube_id="ccccccccccc", fetch_status="ok")
    with db.session(dbp) as conn:
        conn.execute(
            "INSERT INTO decisions (youtube_id, artist, title, genre, target_path, "
            "action, confidence, reason, decided_at) "
            "VALUES ('ccccccccccc', '40 Watt Sun', 'Astoria', NULL, NULL, "
            "'review', 0.5, 'heuristic', ?)",
            (NOW,),
        )
    worklist.sync_worklist(dbp, wl)
    entry = _read_yaml(wl)["videos"]["ccccccccccc"]
    # classify's guess is pre-filled so the user only corrects/adds genre.
    assert entry["artist"] == "40 Watt Sun"
    assert entry["title"] == "Astoria"
    assert entry["genre"] == ""


# --- apply_worklist ------------------------------------------------------


def test_apply_builds_video_overrides_with_title(tmp_path):
    dbp = str(tmp_path / "ytid.db")
    wl = tmp_path / "ytid.yaml"
    wl.write_text(
        "videos:\n"
        "  8R5El2HWMIo:\n"
        "    file: A.webm\n"
        "    artist: Atomic Rooster\n"
        "    title: The Devils Answer\n"
        "    genre: Rock\n"
        "    action: move\n",
        encoding="utf-8",
    )
    _seed_video(dbp, src_path="/s/A.webm", filename="A.webm",
                youtube_id="8R5El2HWMIo", fetch_status="unavailable")

    overrides, stats = worklist.apply_worklist(dbp, wl)
    ov = overrides.videos["8R5El2HWMIo"]
    assert ov.artist == "Atomic Rooster"
    assert ov.title == "The Devils Answer"
    assert ov.genre == "rock"  # lower-cased
    assert ov.action == "move"
    assert stats["video_overrides"] == 1


def test_apply_ignores_blank_stub(tmp_path):
    dbp = str(tmp_path / "ytid.db")
    wl = tmp_path / "ytid.yaml"
    wl.write_text(
        "videos:\n"
        "  8R5El2HWMIo:\n"
        "    file: A.webm\n"
        "    artist: ''\n"
        "    title: ''\n"
        "    genre: ''\n"
        "    action: ''\n",
        encoding="utf-8",
    )
    _seed_video(dbp, src_path="/s/A.webm", filename="A.webm", youtube_id="8R5El2HWMIo")
    overrides, stats = worklist.apply_worklist(dbp, wl)
    assert overrides.videos == {}
    assert stats["video_overrides"] == 0


def test_apply_assigns_id_to_unidentified_file(tmp_path):
    dbp = str(tmp_path / "ytid.db")
    wl = tmp_path / "ytid.yaml"
    wl.write_text(
        "unidentified:\n"
        "- file: mystery.webm\n"
        "  youtube_id: dQw4w9WgXcQ\n"
        "  artist: Rick Astley\n"
        "  title: Never Gonna Give You Up\n"
        "  genre: pop\n",
        encoding="utf-8",
    )
    _seed_video(dbp, src_path="/s/mystery.webm", filename="mystery.webm",
                resolve_status="unresolved", fetch_status="pending")

    overrides, stats = worklist.apply_worklist(dbp, wl)
    assert stats["assigned"] == 1
    with db.session(dbp) as conn:
        row = conn.execute(
            "SELECT youtube_id, resolve_status, id_source FROM videos "
            "WHERE filename = 'mystery.webm'"
        ).fetchone()
    assert row["youtube_id"] == "dQw4w9WgXcQ"
    assert row["resolve_status"] == "resolved"
    assert row["id_source"] == "manual"
    assert overrides.videos["dQw4w9WgXcQ"].artist == "Rick Astley"


def test_apply_reports_unmatched_filename(tmp_path):
    dbp = str(tmp_path / "ytid.db")
    wl = tmp_path / "ytid.yaml"
    wl.write_text(
        "unidentified:\n"
        "- file: nowhere.webm\n"
        "  youtube_id: dQw4w9WgXcQ\n",
        encoding="utf-8",
    )
    db.connect(dbp).close()  # create empty schema
    _overrides, stats = worklist.apply_worklist(dbp, wl)
    assert stats["assigned"] == 0
    assert stats["unmatched"] == 1


def test_apply_missing_file_is_noop(tmp_path):
    dbp = str(tmp_path / "ytid.db")
    db.connect(dbp).close()
    overrides, stats = worklist.apply_worklist(dbp, tmp_path / "absent.yaml")
    assert overrides.videos == {}
    assert overrides.artists == {}
    assert stats == {"assigned": 0, "unmatched": 0, "video_overrides": 0}


# --- classify integration ------------------------------------------------


def test_classify_applies_worklist_override_without_metadata(tmp_path):
    dbp = str(tmp_path / "ytid.db")
    wl = tmp_path / "ytid.yaml"
    wl.write_text(
        "videos:\n"
        "  8R5El2HWMIo:\n"
        "    file: A.webm\n"
        "    artist: Atomic Rooster\n"
        "    title: The Devils Answer\n"
        "    genre: rock\n"
        "    action: move\n",
        encoding="utf-8",
    )
    _seed_video(dbp, src_path="/s/A.webm", filename="A.webm",
                youtube_id="8R5El2HWMIo", fetch_status="unavailable")

    counts = classify.classify_all(dbp, worklist_path=str(wl))
    assert counts["move"] == 1
    with db.session(dbp) as conn:
        row = conn.execute(
            "SELECT artist, title, genre, action, reason FROM decisions "
            "WHERE youtube_id = '8R5El2HWMIo'"
        ).fetchone()
    assert row["artist"] == "Atomic Rooster"
    assert row["title"] == "The Devils Answer"
    assert row["genre"] == "rock"
    assert row["action"] == "move"
    assert row["reason"] == "video override"


# --- CLI defaults --------------------------------------------------------


def test_scan_source_defaults_to_cwd():
    args = build_parser().parse_args(["scan"])
    assert args.source == "."
    assert args.worklist == worklist.WORKLIST_FILE


def test_classify_worklist_default():
    args = build_parser().parse_args(["classify"])
    assert args.worklist == worklist.WORKLIST_FILE


def test_classify_cli_writes_review_items_to_worklist(tmp_path):
    dbp = str(tmp_path / "ytid.db")
    wl = str(tmp_path / "ytid.yaml")
    raw = json.dumps({"title": "40 Watt Sun - Astoria [live]", "channel": "40 Watt Sun"})
    with db.session(dbp) as conn:
        conn.execute(
            "INSERT INTO videos (src_path, filename, ext, youtube_id, "
            "resolve_status, fetch_status, raw_json, first_seen_at, last_seen_at) "
            "VALUES (?, ?, '.webm', 'vwIdwgFcEo4', 'resolved', 'ok', ?, ?, ?)",
            ("/s/x.webm", "40 Watt Sun - Astoria [vwIdwgFcEo4].webm", raw, NOW, NOW),
        )

    rc = cli.main(["--db", dbp, "classify", "--worklist", wl])
    assert rc == 0

    entry = _read_yaml(wl)["videos"]["vwIdwgFcEo4"]
    assert entry["artist"] == "40 Watt Sun"
    assert entry["title"] == "Astoria [live]"


# --- worklist --list filters (read_entries) ------------------------------


def _write_worklist(path, body):
    Path(path).write_text(body, encoding="utf-8")


def test_read_entries_captures_line_numbers_and_fields(tmp_path):
    wl = tmp_path / "ytid.yaml"
    _write_worklist(wl, (
        "videos:\n"
        "  aaaaaaaaaaa:\n"
        "    file: A.webm\n"
        '    artist: "Band A"\n'
        '    title: "Song A"\n'
        "    genre: ''\n"
        "    action: ''\n"
        "  bbbbbbbbbbb:\n"
        "    file: B.webm\n"
        '    artist: ""\n'
        '    title: "Song B"\n'
        "    genre: ''\n"
        "    action: 'move'\n"
        "unidentified:\n"
        "- file: mystery.webm\n"
        "  youtube_id: ''\n"
    ))
    entries = worklist.read_entries(wl)
    vids = {e["youtube_id"]: e for e in entries["videos"]}
    assert vids["aaaaaaaaaaa"]["line"] == 2      # the '  aaaaaaaaaaa:' line
    assert vids["aaaaaaaaaaa"]["artist"] == "Band A"
    assert vids["bbbbbbbbbbb"]["artist"] == ""   # blank
    assert vids["bbbbbbbbbbb"]["action"] == "move"
    assert entries["unidentified"][0]["file"] == "mystery.webm"
    assert entries["unidentified"][0]["line"] == 15


def test_worklist_list_missing_artist_filter(tmp_path, capsys):
    dbp = str(tmp_path / "ytid.db")
    wl = tmp_path / "ytid.yaml"
    _write_worklist(wl, (
        "videos:\n"
        "  aaaaaaaaaaa:\n"
        "    file: A.webm\n"
        '    artist: "Band A"\n'
        '    title: "Song A"\n'
        "  bbbbbbbbbbb:\n"
        "    file: B.webm\n"
        '    artist: ""\n'
        '    title: "Song B"\n'
    ))
    _seed_video(dbp, src_path="/s/A.webm", filename="A.webm", youtube_id="aaaaaaaaaaa")
    _seed_video(dbp, src_path="/s/B.webm", filename="B.webm", youtube_id="bbbbbbbbbbb")
    with db.session(dbp) as conn:
        conn.execute("INSERT INTO decisions (youtube_id, action, confidence, "
                     "reason, decided_at) VALUES ('aaaaaaaaaaa','move',1.0,'x',?)", (NOW,))
        conn.execute("INSERT INTO decisions (youtube_id, action, confidence, "
                     "reason, decided_at) VALUES ('bbbbbbbbbbb','review',0.0,'x',?)", (NOW,))
    rc = cli.main(["--db", dbp, "worklist", "--worklist", str(wl),
                   "--list", "--missing-artist", "--compact"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "bbbbbbbbbbb" in out           # missing artist -> listed
    assert "aaaaaaaaaaa\t" not in out     # has artist -> filtered out
    assert "shown 1" in out


def test_worklist_list_action_move_uses_resolved_decision(tmp_path, capsys):
    dbp = str(tmp_path / "ytid.db")
    wl = tmp_path / "ytid.yaml"
    _write_worklist(wl, (
        "videos:\n"
        "  aaaaaaaaaaa:\n"
        "    file: A.webm\n"
        '    artist: "Band A"\n'
        "  bbbbbbbbbbb:\n"
        "    file: B.webm\n"
        '    artist: "Band B"\n'
    ))
    _seed_video(dbp, src_path="/s/A.webm", filename="A.webm", youtube_id="aaaaaaaaaaa")
    _seed_video(dbp, src_path="/s/B.webm", filename="B.webm", youtube_id="bbbbbbbbbbb")
    with db.session(dbp) as conn:
        conn.execute("INSERT INTO decisions (youtube_id, action, confidence, "
                     "reason, decided_at) VALUES ('aaaaaaaaaaa','move',1.0,'x',?)", (NOW,))
        conn.execute("INSERT INTO decisions (youtube_id, action, confidence, "
                     "reason, decided_at) VALUES ('bbbbbbbbbbb','review',0.0,'x',?)", (NOW,))
    rc = cli.main(["--db", dbp, "worklist", "--worklist", str(wl),
                   "--list", "--action", "move", "--compact"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "aaaaaaaaaaa" in out
    assert "bbbbbbbbbbb" not in out
