from ytid.scan import extract_youtube_id


def test_basic_bracket_id():
    assert extract_youtube_id("Atomic Rooster - The Devils Answer [8R5El2HWMIo].webm") == ("8R5El2HWMIo", "bracket")


def test_id_with_bracketed_title():
    # A bracketed token appears earlier; the trailing one is the real ID.
    name = "Atomic Rooster - The Devils Answer [totp2] [8R5El2HWMIo].webm"
    assert extract_youtube_id(name) == ("8R5El2HWMIo", "bracket")


def test_underscore_and_dash_bracket_ids():
    assert extract_youtube_id("Atlanta Rhythm Section - Doraville (480p) [_7VVZQ_BYFE].mkv") == ("_7VVZQ_BYFE", "bracket")


def test_no_id():
    assert extract_youtube_id("August.webm") == (None, "none")


def test_wrong_length_not_matched():
    assert extract_youtube_id("Song [tooShort].mp4") == (None, "none")


def test_fullwidth_separator_filename_still_finds_id():
    name = "Baker Street Muse⧸Jethro Tull [BOxwov8TCsw].mkv"
    assert extract_youtube_id(name) == ("BOxwov8TCsw", "bracket")


def test_dash_suffix_convention():
    # yt-dlp default template: %(title)s-%(id)s.%(ext)s
    assert extract_youtube_id("About the World-kxNehhCWIrs.webm") == ("kxNehhCWIrs", "dash")


def test_dash_id_containing_hyphen():
    # The id itself contains a hyphen; anchored 11-char match must still win.
    assert extract_youtube_id("1958 Ray Charles - Yes Indeed-kIv3hFd2-w4.mp4") == ("kIv3hFd2-w4", "dash")


def test_dash_numeric_stem():
    assert extract_youtube_id("02266-Wh_MPMoSA60.mkv") == ("Wh_MPMoSA60", "dash")


def test_bracket_preferred_over_dash():
    # A dash appears in the title but a bracketed id is present -> bracket wins.
    assert extract_youtube_id("86 - MAN OVERHEAD [Nr0CVPO12Ds].mp4") == ("Nr0CVPO12Ds", "bracket")
