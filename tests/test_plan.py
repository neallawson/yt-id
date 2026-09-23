from pathlib import Path

from ytid import db
from ytid.plan import (
    build_plan,
    clean_filename,
    destination_filename,
    enhance_filename,
    sanitize_component,
)


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


def _seed_move(db_path, yid, filename, artist, genre, title=None, reason="test"):
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
            "VALUES (?, ?, ?, ?, NULL, 'move', 0.9, ?, ?)",
            (yid, artist, title, genre, reason, now),
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


# --- clean_filename: level=None is a no-op ---------------------------------


def test_clean_none_is_passthrough():
    assert clean_filename("Weird: Name?.mp4", None) == "Weird: Name?.mp4"


# --- clean_filename: conservative ------------------------------------------


def test_clean_seam_dash_when_removal_concatenates():
    assert clean_filename("Foo:Bar.mp4", "conservative") == "Foo-Bar.mp4"


def test_clean_illegal_slash_becomes_seam():
    assert clean_filename("AC/DC.mp4", "conservative") == "AC-DC.mp4"


def test_clean_trailing_bad_char_dropped_no_seam():
    assert clean_filename("What?.mp4", "conservative") == "What.mp4"


def test_clean_whitespace_becomes_single_underscore():
    assert clean_filename("a  b.mp4", "conservative") == "a_b.mp4"


def test_clean_conservative_keeps_parens_and_amp():
    assert (
        clean_filename("Song (Live) & More.mp4", "conservative")
        == "Song_(Live)_&_More.mp4"
    )


def test_clean_preserves_unicode_letters():
    assert clean_filename("Motörhead.mp4", "conservative") == "Motörhead.mp4"


def test_clean_reserved_name_is_prefixed():
    assert clean_filename("CON.mp4", "conservative") == "_CON.mp4"


def test_clean_empty_result_falls_back_to_unknown():
    assert clean_filename("???.mp4", "conservative") == "Unknown.mp4"


# --- clean_filename: moderate ----------------------------------------------


def test_clean_collapses_spaced_hyphen_to_single_underscore():
    assert (
        clean_filename("Nazz - Open My Eyes [abcdefghijk].mp4", "moderate")
        == "Nazz_Open_My_Eyes_[abcdefghijk].mp4"
    )


def test_clean_moderate_strips_shell_hostile_chars():
    assert (
        clean_filename("Song (Live) & More.mp4", "moderate")
        == "Song_Live_and_More.mp4"
    )


def test_clean_moderate_ampersand_becomes_and():
    assert clean_filename("A&B.mp4", "moderate") == "A_and_B.mp4"


def test_clean_moderate_ampersand_with_spaces_becomes_and():
    assert clean_filename("Salt & Pepper.mp4", "moderate") == "Salt_and_Pepper.mp4"


def test_clean_conservative_keeps_literal_ampersand():
    assert clean_filename("A&B.mp4", "conservative") == "A&B.mp4"


def test_clean_moderate_drops_comma_before_space():
    assert (
        clean_filename("Flowers De Moon, Olivia Price.mp4", "moderate")
        == "Flowers_De_Moon_Olivia_Price.mp4"
    )


def test_clean_moderate_comma_seam_without_space():
    assert clean_filename("A,B.mp4", "moderate") == "A-B.mp4"


def test_clean_moderate_removes_fullwidth_and_ideographic_commas():
    assert clean_filename("A\uff0cB.mp4", "moderate") == "A-B.mp4"
    assert clean_filename("A\u3001B.mp4", "moderate") == "A-B.mp4"


def test_clean_conservative_keeps_literal_comma():
    assert clean_filename("A,B.mp4", "conservative") == "A,B.mp4"


# --- clean_filename: ID + extension preservation ---------------------------


def test_clean_preserves_bracket_id_token():
    assert (
        clean_filename("My: Song [abcdefghijk].mp4", "conservative")
        == "My_Song_[abcdefghijk].mp4"
    )


def test_clean_preserves_dash_suffix_id_token():
    assert (
        clean_filename("Title: X-abcdefghijk.webm", "conservative")
        == "Title_X-abcdefghijk.webm"
    )


def test_clean_is_idempotent():
    once = clean_filename("Song (Live) & More [abcdefghijk].mkv", "moderate")
    assert clean_filename(once, "moderate") == once


def test_clean_preserves_id_with_separator_run():
    # regression: id containing "_-" must not be collapsed to "_"
    assert (
        clean_filename("1776 [EX_-1xbYx_E].webm", "moderate")
        == "1776_[EX_-1xbYx_E].webm"
    )


def test_clean_preserves_bracket_id_with_leading_underscore():
    assert (
        clean_filename("9-9 [_yN5ZboIT-o].mp4", "moderate") == "9-9_[_yN5ZboIT-o].mp4"
    )


def test_clean_no_double_dash_when_seam_meets_dash_id():
    # regression: removed ')' seam must not stack with the dash-suffix ID's dash
    assert (
        clean_filename(
            "35mm _ Moon Tower (Live at The Nave)-r2fJKaX_FoE.mkv", "moderate"
        )
        == "35mm_Moon_Tower_Live_at_The_Nave-r2fJKaX_FoE.mkv"
    )


# --- plan integration: clean_names flows into to_path ----------------------


def test_plan_clean_names_scrubs_canonical_name(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(
        db_path, "ccccccccccc", "Bad: Name?.mp4", "AC/DC", None,
        title="Hells & Bells",
    )
    planned = build_plan(tmp_path / "out", db_path=db_path, clean_names="moderate")
    pm = next(p for p in planned if p.youtube_id == "ccccccccccc")
    assert Path(pm.to_path).name == "AC-DC_Hells_and_Bells_[ccccccccccc].mp4"


def test_plan_default_uses_artist_title_and_id(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(
        db_path, "ddddddddddd", "Bad: Name?.mp4", "Nazz", None,
        title="Open My Eyes",
    )
    planned = build_plan(tmp_path / "out", db_path=db_path)
    pm = next(p for p in planned if p.youtube_id == "ddddddddddd")
    assert Path(pm.to_path).name == "Nazz - Open My Eyes [ddddddddddd].mp4"


# --- enhance_filename: prepend artist/title --------------------------------


def test_enhance_id_only_bracket_prepends_both():
    assert (
        enhance_filename("[dQw4w9WgXcQ].mp4", "Nazz", "Open My Eyes", None)
        == "Nazz_Open_My_Eyes_[dQw4w9WgXcQ].mp4"
    )


def test_enhance_id_only_dash_keeps_suffix_id():
    assert (
        enhance_filename("just-abcdefghijk.webm", "Nazz", "Open My Eyes", None)
        == "Nazz_Open_My_Eyes_just-abcdefghijk.webm"
    )


def test_enhance_skips_title_already_present():
    assert (
        enhance_filename("Open My Eyes [dQw4w9WgXcQ].mp4", "Nazz", "Open My Eyes", None)
        == "Nazz_Open My Eyes [dQw4w9WgXcQ].mp4"
    )


def test_enhance_both_present_returns_base_unchanged():
    name = "Nazz - Open My Eyes [dQw4w9WgXcQ].mp4"
    assert enhance_filename(name, "Nazz", "Open My Eyes", None) == name


def test_enhance_dedups_when_artist_equals_title():
    assert (
        enhance_filename("[dQw4w9WgXcQ].mp4", "Foo", "Foo", None)
        == "Foo_[dQw4w9WgXcQ].mp4"
    )


def test_enhance_missing_artist_uses_title_only():
    assert (
        enhance_filename("[dQw4w9WgXcQ].mp4", None, "Solo Track", None)
        == "Solo_Track_[dQw4w9WgXcQ].mp4"
    )


def test_enhance_combined_with_moderate_clean():
    assert (
        enhance_filename("A&B [dQw4w9WgXcQ].mp4", "AC/DC", "Hells & Bells", "moderate")
        == "AC-DC_Hells_and_Bells_A_and_B_[dQw4w9WgXcQ].mp4"
    )


def test_enhance_is_idempotent():
    once = enhance_filename("[dQw4w9WgXcQ].mp4", "Nazz", "Open My Eyes", None)
    assert enhance_filename(once, "Nazz", "Open My Eyes", None) == once


def test_enhance_preserves_id_with_separator_run():
    # regression: id "_-" run survives enhance + moderate clean
    assert (
        enhance_filename(
            "1776 [EX_-1xbYx_E].webm", "Hope Of The States", "1776", "moderate"
        )
        == "Hope_Of_The_States_1776_[EX_-1xbYx_E].webm"
    )


def test_enhance_no_double_dash_when_seam_meets_dash_id():
    # regression: the Far Caspian case from the manifest
    assert (
        enhance_filename(
            "35mm _ Moon Tower (Live at The Nave)-r2fJKaX_FoE.mkv",
            "Far Caspian",
            "35mm / Moon Tower (Live at The Nave)",
            "moderate",
        )
        == "Far_Caspian_35mm_Moon_Tower_Live_at_The_Nave-r2fJKaX_FoE.mkv"
    )


# --- plan integration: enhance_names flows into to_path --------------------


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


def test_destination_filename_sanitizes_illegal_chars_only():
    assert (
        destination_filename("Nazz", "Hello: World?", "abcdefghijk", "x.mp4")
        == "Nazz - Hello_ World_ [abcdefghijk].mp4"
    )


def test_plan_supplied_title_renames_under_artist(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(
        db_path, "8R5El2HWMIo",
        "Atomic Rooster - Tomorrow Night (TOTP 1971) [8R5El2HWMIo].webm",
        "Atomic Rooster", "rock", title="The Devils Answer",
        reason="video override",
    )
    planned = build_plan(
        tmp_path / "out", db_path=db_path,
        clean_names="moderate", enhance_names=True,
    )
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
    planned = build_plan(
        tmp_path / "out", db_path=db_path, clean_names="moderate",
    )
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


def test_plan_enhance_names_does_not_change_canonical_name(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(
        db_path, "eeeeeeeeeee", "[dQw4w9WgXcQ].mp4", "Nazz", None,
        title="Open My Eyes",
    )
    planned = build_plan(tmp_path / "out", db_path=db_path, enhance_names=True)
    pm = next(p for p in planned if p.youtube_id == "eeeeeeeeeee")
    assert Path(pm.to_path).name == "Nazz - Open My Eyes [eeeeeeeeeee].mp4"
