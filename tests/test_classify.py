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


def test_video_override_supplies_title_without_metadata():
    cfg = make_config()
    cfg.overrides.videos["8R5El2HWMIo"] = VideoOverride(
        artist="Atomic Rooster", title="The Devils Answer", genre="rock", action="move"
    )
    # meta is None (fetch was unavailable) -- the override must still win and
    # carry the title through.
    d = decide("8R5El2HWMIo", None, cfg)
    assert d.artist == "Atomic Rooster"
    assert d.title == "The Devils Answer"
    assert d.genre == "rock"
    assert d.action == "move"
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


def test_confident_artist_no_genre_moves_by_default():
    cfg = make_config()
    # Structured artist/track (confidence 0.9) but unknown genre. Genre is
    # optional by default, so a confident artist-only file moves to /Artist.
    meta = {"artist": "Unknown Band", "track": "A Song"}
    d = decide("bbbbbbbbbbb", meta, cfg)
    assert d.genre is None
    assert d.action == "move"
    assert "no genre" in d.reason


def test_require_genre_reviews_confident_artist_only():
    cfg = make_config()
    meta = {"artist": "Unknown Band", "track": "A Song"}
    d = decide("bbbbbbbbbbb", meta, cfg, allow_missing_genre=False)
    assert d.genre is None
    assert d.action == "review"


def test_low_confidence_still_reviews_even_with_genre_optional():
    cfg = make_config()
    # Heuristic title split -> confidence 0.5, below the move threshold.
    meta = {"title": "Unknown Artist - Some Song"}
    d = decide("ccccccccccc", meta, cfg)
    assert d.genre is None
    assert d.action == "review"


def test_override_artist_no_genre_moves_by_default():
    cfg = make_config()
    # A filled ytid.yaml entry: artist/title but no genre and no explicit action.
    cfg.overrides.videos["ddddddddddd"] = VideoOverride(
        artist="Some Band", title="A Song"
    )
    d = decide("ddddddddddd", {"title": "irrelevant"}, cfg)
    assert d.artist == "Some Band"
    assert d.title == "A Song"
    assert d.genre is None
    assert d.action == "move"
    assert d.reason == "video override"


def test_override_artist_without_title_stays_in_review():
    cfg = make_config()
    cfg.overrides.videos["eeeeeeeeeee"] = VideoOverride(artist="Some Band")
    d = decide("eeeeeeeeeee", None, cfg)
    assert d.artist == "Some Band"
    assert d.title is None
    assert d.action == "review"
    assert d.reason == "video override"


def test_override_title_only_moves():
    cfg = make_config()
    cfg.overrides.videos["titleonly01"] = VideoOverride(title="The Dust Bowl")
    d = decide("titleonly01", None, cfg)
    assert d.artist is None
    assert d.title == "The Dust Bowl"
    assert d.genre is None
    assert d.action == "move"


def test_override_title_only_reviews_when_artist_required():
    cfg = make_config()
    cfg.overrides.videos["titleonly01"] = VideoOverride(
        title="The Dust Bowl", genre="other"
    )
    d = decide("titleonly01", None, cfg, allow_missing_artist=False)
    assert d.action == "review"


def test_override_explicit_move_without_title_stays_in_review():
    cfg = make_config()
    cfg.overrides.videos["notitle0001"] = VideoOverride(
        artist="Some Band", action="move"
    )
    d = decide("notitle0001", None, cfg)
    assert d.title is None
    assert d.action == "review"


def test_require_artist_reviews_when_artist_missing():
    cfg = make_config()
    # Confidence stays low without an artist, so this uses an override title
    # to show the flag itself, not the confidence gate.
    cfg.overrides.videos["titleonly01"] = VideoOverride(title="August")
    d = decide("titleonly01", None, cfg, allow_missing_artist=False)
    assert d.action == "review"
    assert d.title == "August"


def test_override_artist_no_genre_reviews_when_genre_required():
    cfg = make_config()
    cfg.overrides.videos["ddddddddddd"] = VideoOverride(
        artist="Some Band", title="A Song"
    )
    d = decide("ddddddddddd", {"title": "irrelevant"}, cfg, allow_missing_genre=False)
    assert d.action == "review"
