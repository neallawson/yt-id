from ytid.classify import decide
from ytid.config import Config, GenreMap, Overrides, VideoOverride


def make_config():
    genre_map = GenreMap(
        buckets=["rock", "jazz", "classical", "funk", "other"],
        normalize={"classic rock": "rock", "jazz fusion": "jazz"},
    )
    overrides = Overrides(
        artists={"Atomic Rooster": "rock", "Average White Band": "funk"},
        videos={
            "CKdRIp_TCrk": VideoOverride(artist="Unknown", genre="other", action="review"),
        },
    )
    return Config(genre_map=genre_map, overrides=overrides)


def test_video_override_wins():
    cfg = make_config()
    d = decide("CKdRIp_TCrk", {"title": "Athens, Georgia"}, cfg)
    assert d.action == "review"
    assert d.genre == "other"
    assert d.reason == "video override"


def test_structured_fields_with_artist_override_genre():
    cfg = make_config()
    meta = {"artist": "Atomic Rooster", "track": "The Devil's Answer", "title": "whatever"}
    d = decide("8R5El2HWMIo", meta, cfg)
    assert d.artist == "Atomic Rooster"
    assert d.title == "The Devil's Answer"
    assert d.genre == "rock"
    assert d.action == "move"
    assert d.confidence >= 0.6


def test_heuristic_title_split_known_artist():
    cfg = make_config()
    meta = {"title": "Average White Band - Pick up the pieces"}
    d = decide("MfAJLGFWxYo", meta, cfg)
    assert d.artist == "Average White Band"
    assert d.genre == "funk"
    # heuristic confidence is 0.5 -> below move threshold -> review
    assert d.action == "review"


def test_unknown_artist_goes_to_review():
    cfg = make_config()
    d = decide("wPnRADykW1E", {"title": "August"}, cfg)
    assert d.action == "review"
    assert d.genre is None


def test_missing_metadata_is_review():
    cfg = make_config()
    d = decide("zzzzzzzzzzz", None, cfg)
    assert d.action == "review"
    assert "no metadata" in d.reason


def test_structured_genre_normalized():
    cfg = make_config()
    meta = {"artist": "Some Band", "track": "A Song", "genre": "classic rock"}
    d = decide("aaaaaaaaaaa", meta, cfg)
    assert d.genre == "rock"
    assert d.action == "move"


def test_confident_artist_no_genre_is_review_by_default():
    cfg = make_config()
    # Structured artist/track (confidence 0.9) but unknown genre.
    meta = {"artist": "Unknown Band", "track": "A Song"}
    d = decide("bbbbbbbbbbb", meta, cfg)
    assert d.genre is None
    assert d.action == "review"


def test_allow_missing_genre_moves_confident_artist_only():
    cfg = make_config()
    meta = {"artist": "Unknown Band", "track": "A Song"}
    d = decide("bbbbbbbbbbb", meta, cfg, allow_missing_genre=True)
    assert d.genre is None
    assert d.action == "move"
    assert "artist-only (no genre)" in d.reason


def test_allow_missing_genre_still_reviews_low_confidence():
    cfg = make_config()
    # Heuristic title split -> confidence 0.5, below the move threshold.
    meta = {"title": "Unknown Artist - Some Song"}
    d = decide("ccccccccccc", meta, cfg, allow_missing_genre=True)
    assert d.genre is None
    assert d.action == "review"
