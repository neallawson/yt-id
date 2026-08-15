from ytid.scan import extract_youtube_id


def test_basic_id():
    assert extract_youtube_id("Atomic Rooster - The Devils Answer [8R5El2HWMIo].webm") == "8R5El2HWMIo"


def test_id_with_bracketed_title():
    # A bracketed token appears earlier; the trailing one is the real ID.
    name = "Atomic Rooster - The Devils Answer [totp2] [8R5El2HWMIo].webm"
    assert extract_youtube_id(name) == "8R5El2HWMIo"


def test_underscore_and_dash_ids():
    assert extract_youtube_id("Atlanta Rhythm Section - Doraville (480p) [_7VVZQ_BYFE].mkv") == "_7VVZQ_BYFE"


def test_no_id():
    assert extract_youtube_id("August.webm") is None


def test_wrong_length_not_matched():
    assert extract_youtube_id("Song [tooShort].mp4") is None


def test_fullwidth_separator_filename_still_finds_id():
    name = "Baker Street Muse⧸Jethro Tull [BOxwov8TCsw].mkv"
    assert extract_youtube_id(name) == "BOxwov8TCsw"
