from pathlib import Path

from ytid import db
from ytid.plan import (
    build_plan,
    clean_filename,
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


def _seed_move(db_path, yid, filename, artist, genre, title=None):
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
            "VALUES (?, ?, ?, ?, NULL, 'move', 0.9, 'test', ?)",
            (yid, artist, title, genre, now),
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


def test_clean_moderate_strips_shell_hostile_chars():
    assert (
        clean_filename("Song (Live) & More.mp4", "moderate") == "Song_Live_More.mp4"
    )


def test_clean_moderate_ampersand_seam_without_spaces():
    assert clean_filename("A&B.mp4", "moderate") == "A-B.mp4"


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


# --- plan integration: clean_names flows into to_path ----------------------


def test_plan_clean_names_applied_to_filename(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(db_path, "ccccccccccc", "Bad: Name?.mp4", "Nazz", None)
    planned = build_plan(tmp_path / "out", db_path=db_path, clean_names="moderate")
    pm = next(p for p in planned if p.youtube_id == "ccccccccccc")
    assert Path(pm.to_path).name == "Bad_Name.mp4"


def test_plan_default_preserves_original_filename(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(db_path, "ddddddddddd", "Bad: Name?.mp4", "Nazz", None)
    planned = build_plan(tmp_path / "out", db_path=db_path)
    pm = next(p for p in planned if p.youtube_id == "ddddddddddd")
    assert Path(pm.to_path).name == "Bad: Name?.mp4"


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
        == "AC-DC_Hells_Bells_A-B_[dQw4w9WgXcQ].mp4"
    )


def test_enhance_is_idempotent():
    once = enhance_filename("[dQw4w9WgXcQ].mp4", "Nazz", "Open My Eyes", None)
    assert enhance_filename(once, "Nazz", "Open My Eyes", None) == once


# --- plan integration: enhance_names flows into to_path --------------------


def test_plan_enhance_names_applied(tmp_path):
    db_path = str(tmp_path / "t.db")
    _seed_move(
        db_path, "eeeeeeeeeee", "[dQw4w9WgXcQ].mp4", "Nazz", None,
        title="Open My Eyes",
    )
    planned = build_plan(tmp_path / "out", db_path=db_path, enhance_names=True)
    pm = next(p for p in planned if p.youtube_id == "eeeeeeeeeee")
    assert Path(pm.to_path).name == "Nazz_Open_My_Eyes_[dQw4w9WgXcQ].mp4"
