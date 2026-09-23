from pathlib import Path

from ytid import db
from ytid.plan import (
    build_plan,
    destination_filename,
    sanitize_component,
    strict_names,
)


def test_illegal_chars_replaced():
    assert sanitize_component("AC/DC") == "AC-DC"


def test_trailing_dot_and_space_stripped():
    assert sanitize_component("  The Band. ") == "The Band"


def test_reserved_name_falls_back():
    assert sanitize_component("con") == "Unknown"


def test_empty_falls_back():
    assert sanitize_component("   ") == "Unknown"


def test_normal_unicode_preserved():
    assert sanitize_component("Motörhead") == "Motörhead"


def test_trailing_illegal_char_is_dropped():
    assert sanitize_component("Hello?") == "Hello"


def test_illegal_char_between_words_becomes_hyphen():
    assert sanitize_component("Foo:Bar") == "Foo-Bar"


def test_strict_names_keeps_words_and_drops_parens():
    assert strict_names("The Devils Answer (Live)") == "The Devils Answer Live"
    assert strict_names("Hall & Oates") == "Hall and Oates"
    assert strict_names("R&B") == "R and B"


def test_sanitize_keeps_identifying_punctuation():
    assert sanitize_component("Good Lovin' (Live) & More") == "Good Lovin' (Live) & More"
    assert sanitize_component("Song [demo]") == "Song [demo]"


def test_sanitize_collapses_an_illegal_run_to_one_hyphen():
    assert sanitize_component("AC//DC") == "AC-DC"


def test_sanitize_collapses_whitespace():
    assert sanitize_component("The   Band") == "The Band"


def test_strict_names_drops_apostrophe_and_comma():
    assert strict_names("Good Lovin'") == "Good Lovin"
    assert strict_names("Atlanta, Georgia") == "Atlanta Georgia"


def _seed_move(
    db_path, yid, filename, artist, genre, title=None, reason="test", action="move",
):
    now = "2026-01-01T00:00:00+00:00"
    ext = Path(filename).suffix or ".mp4"
    with db.session(db_path) as conn:
        conn.execute(
            "INSERT INTO videos (src_path, filename, ext, youtube_id, "
            "resolve_status, fetch_status, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, 'resolved', 'ok', ?, ?)",
            (f"/src/{filename}", filename, ext, yid, now, now),
        )
        conn.execute(
            "INSERT INTO decisions (youtube_id, artist, title, genre, "
            "target_path, action, confidence, reason, decided_at) "
            "VALUES (?, ?, ?, ?, NULL, ?, 0.9, ?, ?)",
            (yid, artist, title, genre, action, reason, now),
        )


def test_plan_with_genre_nests_under_genre(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(db_path, "aaaaaaaaaaa", "A.mp4", "Nazz", "rock", title="Song")
    planned = build_plan(tmp_path / "out", db_path=db_path)
    pm = next(p for p in planned if p.youtube_id == "aaaaaaaaaaa")
    assert Path(pm.to_path) == (
        tmp_path / "out" / "rock" / "Nazz" / "Nazz - Song [aaaaaaaaaaa].mp4"
    )


def test_plan_without_genre_places_directly_under_artist(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(db_path, "bbbbbbbbbbb", "B.mp4", "Nazz", None, title="Song")
    planned = build_plan(tmp_path / "out", db_path=db_path)
    pm = next(p for p in planned if p.youtube_id == "bbbbbbbbbbb")
    assert Path(pm.to_path) == (
        tmp_path / "out" / "Nazz" / "Nazz - Song [bbbbbbbbbbb].mp4"
    )


def test_min_artist_files_flattens_sparse_artist_keeping_genre(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(db_path, "sparse00001", "S.mp4", "Solo", "rock", title="Song")
    planned = build_plan(tmp_path / "out", db_path=db_path, min_artist_files=2)
    pm = next(p for p in planned if p.youtube_id == "sparse00001")
    # Below threshold: artist folder dropped, genre grouping kept.
    assert Path(pm.to_path) == (
        tmp_path / "out" / "rock" / "Solo - Song [sparse00001].mp4"
    )


def test_min_artist_files_flattens_sparse_artist_to_root_without_genre(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(db_path, "sparse00002", "S.mp4", "Solo", None, title="Song")
    planned = build_plan(tmp_path / "out", db_path=db_path, min_artist_files=2)
    pm = next(p for p in planned if p.youtube_id == "sparse00002")
    # Below threshold and no genre: lands directly in the target root.
    assert Path(pm.to_path) == tmp_path / "out" / "Solo - Song [sparse00002].mp4"


def test_min_artist_files_keeps_folder_when_threshold_met(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(db_path, "many0000001", "A.mp4", "Nazz", "rock", title="Song")
    _seed_move(db_path, "many0000002", "B.mp4", "Nazz", "rock", title="Song")
    planned = build_plan(tmp_path / "out", db_path=db_path, min_artist_files=2)
    for yid in ("many0000001", "many0000002"):
        pm = next(p for p in planned if p.youtube_id == yid)
        assert Path(pm.to_path) == (
            tmp_path / "out" / "rock" / "Nazz" / f"Nazz - Song [{yid}].mp4"
        )


def test_min_artist_files_default_always_creates_folder(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(db_path, "single00001", "S.mp4", "Solo", "rock", title="Song")
    planned = build_plan(tmp_path / "out", db_path=db_path)
    pm = next(p for p in planned if p.youtube_id == "single00001")
    assert Path(pm.to_path) == (
        tmp_path / "out" / "rock" / "Solo" / "Solo - Song [single00001].mp4"
    )



# --- supplied title: video override is authoritative ---------------------


def test_destination_filename_is_artist_title_and_bracket_id():
    assert (
        destination_filename(
            "Atomic Rooster", "The Devils Answer (Live)", "8R5El2HWMIo",
            "messy name.webm",
        )
        == "Atomic Rooster - The Devils Answer (Live) [8R5El2HWMIo].webm"
    )


def test_destination_filename_can_omit_artist_when_title_present():
    assert (
        destination_filename(
            "Atomic Rooster", "The Devils Answer (Live)", "8R5El2HWMIo",
            "messy name.webm", include_artist=False,
        )
        == "The Devils Answer (Live) [8R5El2HWMIo].webm"
    )


def test_destination_filename_drops_illegal_chars():
    assert (
        destination_filename("Nazz", "Hello: World?", "abcdefghijk", "x.mp4")
        == "Nazz - Hello World [abcdefghijk].mp4"
    )


def test_destination_filename_strict_then_spaces():
    assert (
        destination_filename(
            "Atomic Rooster", "The Devils Answer (Live)", "8R5El2HWMIo",
            "messy.webm", strict=True, spaces_to_underscores=True,
        )
        == "Atomic_Rooster_-_The_Devils_Answer_Live_[8R5El2HWMIo].webm"
    )


def test_destination_filename_spaces_only_keeps_parens():
    assert (
        destination_filename(
            "Atomic Rooster", "The Devils Answer (Live)", "8R5El2HWMIo",
            "messy.webm", spaces_to_underscores=True,
        )
        == "Atomic_Rooster_-_The_Devils_Answer_(Live)_[8R5El2HWMIo].webm"
    )


def test_destination_filename_strict_only_keeps_spaces():
    assert (
        destination_filename(
            "Atomic Rooster", "The Devils Answer (Live)", "8R5El2HWMIo",
            "messy.webm", strict=True,
        )
        == "Atomic Rooster - The Devils Answer Live [8R5El2HWMIo].webm"
    )


def test_destination_filename_empty_title_falls_back_to_unknown():
    assert (
        destination_filename(None, "???", "abcdefghijk", "x.mp4")
        == "Unknown [abcdefghijk].mp4"
    )


def test_destination_filename_caps_stem_and_keeps_id():
    name = destination_filename("Nazz", "A" * 300, "abcdefghijk", "x.mp4")
    assert name.endswith(" [abcdefghijk].mp4")
    assert len(Path(name).stem) <= 200


def test_destination_filename_leaves_id_underscores_alone():
    assert (
        destination_filename(
            "Hope Of The States", "1776", "EX_-1xbYx_E", "x.webm",
            spaces_to_underscores=True,
        )
        == "Hope_Of_The_States_-_1776_[EX_-1xbYx_E].webm"
    )


def test_plan_supplied_title_renames_under_artist(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(
        db_path, "8R5El2HWMIo",
        "Atomic Rooster - Tomorrow Night (TOTP 1971) [8R5El2HWMIo].webm",
        "Atomic Rooster", "rock", title="The Devils Answer",
        reason="video override",
    )
    planned = build_plan(tmp_path / "out", db_path=db_path, strict_names=True)
    pm = next(p for p in planned if p.youtube_id == "8R5El2HWMIo")
    assert Path(pm.to_path) == (
        tmp_path / "out" / "rock" / "Atomic Rooster"
        / "Atomic Rooster - The Devils Answer [8R5El2HWMIo].webm"
    )


def test_plan_supplied_title_keeps_parens_that_moderate_would_strip(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(
        db_path, "mKWy9LhRjpE", "original.webm",
        "Atomic Rooster", None, title="Breakthrough Take (1971)",
        reason="video override",
    )
    planned = build_plan(tmp_path / "out", db_path=db_path)
    pm = next(p for p in planned if p.youtube_id == "mKWy9LhRjpE")
    assert Path(pm.to_path) == (
        tmp_path / "out" / "Atomic Rooster"
        / "Atomic Rooster - Breakthrough Take (1971) [mKWy9LhRjpE].webm"
    )


def test_plan_omit_artist_from_filename_inside_artist_folder(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(
        db_path, "8R5El2HWMIo", "original.webm",
        "Atomic Rooster", "rock", title="The Devils Answer",
        reason="video override",
    )
    planned = build_plan(
        tmp_path / "out", db_path=db_path, omit_artist_from_filename=True,
    )
    pm = next(p for p in planned if p.youtube_id == "8R5El2HWMIo")
    assert Path(pm.to_path) == (
        tmp_path / "out" / "rock" / "Atomic Rooster"
        / "The Devils Answer [8R5El2HWMIo].webm"
    )


def test_plan_omit_artist_keeps_artist_when_folder_is_flattened(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(
        db_path, "8R5El2HWMIo", "original.webm",
        "Atomic Rooster", "rock", title="The Devils Answer",
    )
    planned = build_plan(
        tmp_path / "out", db_path=db_path,
        min_artist_files=2, omit_artist_from_filename=True,
    )
    pm = next(p for p in planned if p.youtube_id == "8R5El2HWMIo")
    assert Path(pm.to_path) == (
        tmp_path / "out" / "rock"
        / "Atomic Rooster - The Devils Answer [8R5El2HWMIo].webm"
    )


def test_plan_move_without_title_is_not_placed(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(db_path, "notitle0001", "A.mp4", "Nazz", "rock", title=None)
    planned = build_plan(tmp_path / "out", db_path=db_path)
    pm = next(p for p in planned if p.youtube_id == "notitle0001")
    assert pm.to_path is None


def test_plan_title_only_lands_in_genre_folder(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(
        db_path, "titleonly01", "Dust.webm", None, "western documentaries",
        title="The Dust Bowl",
    )
    planned = build_plan(tmp_path / "out", db_path=db_path)
    pm = next(p for p in planned if p.youtube_id == "titleonly01")
    assert Path(pm.to_path) == (
        tmp_path / "out" / "western documentaries"
        / "The Dust Bowl [titleonly01].webm"
    )


def test_plan_title_only_lands_at_target_root(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(db_path, "titleonly02", "Dust.webm", None, None, title="The Dust Bowl")
    planned = build_plan(tmp_path / "out", db_path=db_path)
    pm = next(p for p in planned if p.youtube_id == "titleonly02")
    assert Path(pm.to_path) == tmp_path / "out" / "The Dust Bowl [titleonly02].webm"


def test_plan_spaces_only_shapes_folders_and_keeps_parens(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(
        db_path, "8R5El2HWMIo", "original.webm",
        "Atomic Rooster", "western documentaries",
        title="The Devils Answer (Live)",
    )
    planned = build_plan(
        tmp_path / "out", db_path=db_path, spaces_to_underscores=True,
    )
    pm = next(p for p in planned if p.youtube_id == "8R5El2HWMIo")
    assert Path(pm.to_path) == (
        tmp_path / "out" / "western_documentaries" / "Atomic_Rooster"
        / "Atomic_Rooster_-_The_Devils_Answer_(Live)_[8R5El2HWMIo].webm"
    )


def test_plan_strict_only_shapes_folders_and_keeps_spaces(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(
        db_path, "8R5El2HWMIo", "original.webm",
        "Hall & Oates", "rock", title="The Devils Answer (Live)",
    )
    planned = build_plan(tmp_path / "out", db_path=db_path, strict_names=True)
    pm = next(p for p in planned if p.youtube_id == "8R5El2HWMIo")
    assert Path(pm.to_path) == (
        tmp_path / "out" / "rock" / "Hall and Oates"
        / "Hall and Oates - The Devils Answer Live [8R5El2HWMIo].webm"
    )


def test_plan_artist_folder_uses_os_hyphen_seam(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(
        db_path, "abcdefghijk", "original.mp4",
        "AC/DC", "rock", title="Highway to Hell",
    )
    planned = build_plan(tmp_path / "out", db_path=db_path)
    pm = next(p for p in planned if p.youtube_id == "abcdefghijk")
    assert Path(pm.to_path) == (
        tmp_path / "out" / "rock" / "AC-DC"
        / "AC-DC - Highway to Hell [abcdefghijk].mp4"
    )


def test_plan_review_row_is_not_placed(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(
        db_path, "review00001", "A.mp4", "Nazz", "rock",
        title="Song", action="review",
    )
    planned = build_plan(tmp_path / "out", db_path=db_path)
    pm = next(p for p in planned if p.youtube_id == "review00001")
    assert pm.action == "review"
    assert pm.to_path is None


def test_plan_collision_appends_id_again(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(db_path, "aaaaaaaaaaa", "A.mp4", "Nazz", "rock", title="Song")
    occupied = tmp_path / "out" / "rock" / "Nazz"
    occupied.mkdir(parents=True)
    (occupied / "Nazz - Song [aaaaaaaaaaa].mp4").write_bytes(b"x")
    planned = build_plan(tmp_path / "out", db_path=db_path)
    pm = next(p for p in planned if p.youtube_id == "aaaaaaaaaaa")
    assert Path(pm.to_path) == (
        occupied / "Nazz - Song [aaaaaaaaaaa] [aaaaaaaaaaa].mp4"
    )


def test_plan_strict_and_spaces_apply_to_folder_and_override(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(
        db_path, "8R5El2HWMIo", "original.webm",
        "Hall & Oates", "rock", title="The Devils Answer (Live)",
        reason="video override",
    )
    planned = build_plan(
        tmp_path / "out", db_path=db_path,
        strict_names=True, spaces_to_underscores=True,
    )
    pm = next(p for p in planned if p.youtube_id == "8R5El2HWMIo")
    assert Path(pm.to_path) == (
        tmp_path / "out" / "rock" / "Hall_and_Oates"
        / "Hall_and_Oates_-_The_Devils_Answer_Live_[8R5El2HWMIo].webm"
    )
