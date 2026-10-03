from ytid import cli, db, worklist
from ytid.filename_parse import parse_filename
from ytid.plan import sanitize_component

NOW = "2026-01-01T00:00:00+00:00"


def test_sanitize_reformats_characters_and_keeps_words():
    assert sanitize_component("  She\u2019s  Gone [Live]  (Audio)  ") == "She's Gone (Live) (Audio)"
    assert sanitize_component("AC/DC") == "AC-DC"
    assert sanitize_component("Hall & Oates") == "Hall & Oates"


def test_split_keeps_slash_then_sanitize_rewrites_it():
    parsed = parse_filename("AC/DC - Thunderstruck (Audio) [abcdefghijk].webm")
    assert parsed.artist == "AC-DC"
    assert parsed.title == "Thunderstruck (Audio)"
    assert parsed.confident is True


def test_brackets_inside_the_title_become_parentheses():
    parsed = parse_filename("Atomic Rooster - The Devils Answer [Live] [8R5El2HWMIo].webm")
    assert parsed.artist == "Atomic Rooster"
    assert parsed.title == "The Devils Answer (Live)"
    assert parsed.confident is True


def test_raw_keeps_brackets_and_dash_lookalikes():
    name = "Atomic Rooster – The Devils Answer [Live] [8R5El2HWMIo].webm"
    parsed = parse_filename(name, raw=True)
    assert parsed.artist == "Atomic Rooster"
    assert parsed.title == "The Devils Answer [Live]"
    assert "[" in parsed.title


def test_no_separator_is_a_title_only_suggestion():
    parsed = parse_filename("The Dust Bowl [abcdefghijk].webm")
    assert parsed.artist == ""
    assert parsed.title == "The Dust Bowl"
    assert parsed.confident is True


def test_dash_suffix_id_is_removed_before_the_split():
    parsed = parse_filename("1958 Ray Charles - Yes Indeed-kIv3hFd2-w4.mp4")
    assert parsed.artist == "1958 Ray Charles"
    assert parsed.title == "Yes Indeed"
    assert parsed.confident is True


def test_same_sides_are_not_confident():
    parsed = parse_filename("Song - Song [abcdefghijk].webm")
    assert parsed.artist == "Song"
    assert parsed.title == "Song"
    assert parsed.confident is False


def test_empty_side_is_not_confident():
    parsed = parse_filename(" - Only Title [abcdefghijk].webm")
    assert parsed.artist == ""
    assert parsed.title == "Only Title"
    assert parsed.confident is False


def test_one_side_dominating_the_name_is_not_confident():
    title = "X" * 36
    parsed = parse_filename(f"A - {title} [abcdefghijk].webm")
    assert parsed.artist == "A"
    assert parsed.title == title
    assert parsed.confident is False


def _seed(dbp, *, filename, yid, fetch_status="unavailable"):
    with db.session(dbp) as conn:
        conn.execute(
            "INSERT INTO videos (src_path, filename, ext, youtube_id, "
            "resolve_status, fetch_status, first_seen_at, last_seen_at) "
            "VALUES (?, ?, '.webm', ?, 'resolved', ?, ?, ?)",
            (f"/s/{filename}", filename, yid, fetch_status, NOW, NOW),
        )


def test_sync_fills_blank_names_from_the_filename(tmp_path):
    dbp = str(tmp_path / "ytid.db")
    wl = tmp_path / "ytid.yaml"
    filename = "Atomic Rooster - The Devils Answer [Live] [8R5El2HWMIo].webm"
    _seed(dbp, filename=filename, yid="8R5El2HWMIo")
    worklist.sync_worklist(dbp, wl, fill_names=True)
    entry = worklist._load(wl)["videos"]["8R5El2HWMIo"]
    assert entry["artist"] == "Atomic Rooster"
    assert entry["title"] == "The Devils Answer (Live)"
    assert entry["action"] == ""


def test_sync_marks_a_bad_parse_review_and_keeps_a_hand_edit(tmp_path):
    dbp = str(tmp_path / "ytid.db")
    wl = tmp_path / "ytid.yaml"
    _seed(dbp, filename="Song - Song [aaaaaaaaaaa].webm", yid="aaaaaaaaaaa")
    _seed(dbp, filename="Kept - Already [bbbbbbbbbbb].webm", yid="bbbbbbbbbbb")
    wl.write_text(
        "videos:\n"
        "  bbbbbbbbbbb:\n"
        "    file: Kept - Already [bbbbbbbbbbb].webm\n"
        '    artist: "Kept"\n'
        '    title: "Already"\n'
        '    genre: ""\n'
        '    action: ""\n',
        encoding="utf-8",
    )
    worklist.sync_worklist(dbp, wl, fill_names=True)
    data = worklist._load(wl)["videos"]
    assert data["aaaaaaaaaaa"]["action"] == "review"
    assert data["aaaaaaaaaaa"]["artist"] == "Song"
    assert data["bbbbbbbbbbb"]["artist"] == "Kept"
    assert data["bbbbbbbbbbb"]["title"] == "Already"


def test_decision_guess_wins_over_the_filename(tmp_path):
    dbp = str(tmp_path / "ytid.db")
    wl = tmp_path / "ytid.yaml"
    _seed(dbp, filename="Wrong - Name [ccccccccccc].webm", yid="ccccccccccc",
          fetch_status="ok")
    with db.session(dbp) as conn:
        conn.execute(
            "INSERT INTO decisions (youtube_id, artist, title, genre, target_path, "
            "action, confidence, reason, decided_at) "
            "VALUES ('ccccccccccc', '40 Watt Sun', 'Astoria', NULL, NULL, "
            "'review', 0.5, 'heuristic', ?)",
            (NOW,),
        )
    worklist.sync_worklist(dbp, wl, fill_names=True)
    entry = worklist._load(wl)["videos"]["ccccccccccc"]
    assert entry["artist"] == "40 Watt Sun"
    assert entry["title"] == "Astoria"
    assert entry["action"] == ""


def test_second_classify_moves_a_clean_filename_suggestion(tmp_path, capsys):
    dbp = str(tmp_path / "ytid.db")
    wl = str(tmp_path / "ytid.yaml")
    filename = "Atomic Rooster - The Devils Answer [8R5El2HWMIo].webm"
    _seed(dbp, filename=filename, yid="8R5El2HWMIo")

    assert cli.main(["--db", dbp, "classify", "--worklist", wl]) == 0
    entry = worklist._load(wl)["videos"]["8R5El2HWMIo"]
    assert entry["artist"] == "Atomic Rooster"
    assert entry["title"] == "The Devils Answer"
    assert entry["action"] == ""

    assert cli.main(["--db", dbp, "classify", "--worklist", wl]) == 0
    with db.session(dbp) as conn:
        row = conn.execute(
            "SELECT action, artist, title FROM decisions WHERE youtube_id = ?",
            ("8R5El2HWMIo",),
        ).fetchone()
    assert row["action"] == "move"
    assert row["artist"] == "Atomic Rooster"
    assert row["title"] == "The Devils Answer"
    capsys.readouterr()


def test_raw_flag_keeps_brackets(tmp_path, capsys):
    dbp = str(tmp_path / "ytid.db")
    wl = str(tmp_path / "ytid.yaml")
    filename = "Artist - Title [Live] [abcdefghijk].webm"
    _seed(dbp, filename=filename, yid="abcdefghijk")
    rc = cli.main([
        "--db", dbp, "classify", "--worklist", wl, "--raw-artist-title",
    ])
    assert rc == 0
    entry = worklist._load(wl)["videos"]["abcdefghijk"]
    assert entry["title"] == "Title [Live]"
    capsys.readouterr()
