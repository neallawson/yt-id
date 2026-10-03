"""Suggest an artist and title from a video filename.

Used when a fetch produced nothing to classify. The YouTube id is removed
first. A spaced dash (or a bar, tilde, or fullwidth colon) splits artist from
title. A slash is not a split, so ``AC/DC`` stays one name.

Each side is then passed through ``plan.sanitize_component``, the same cleaner
used for destination paths: ``[Live]`` becomes ``(Live)``, ``AC/DC`` becomes
``AC-DC``, unicode dashes become ``-``, and curly quotes become ``'``.
``raw=True`` skips that pass and keeps the split text as it appeared in the
filename.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .plan import sanitize_component
from .scan import human_stem

# Artist/title separators. Slash is intentionally absent: "AC/DC - Song"
# must split on the spaced dash and leave the slash in the artist.
# sanitize_component then rewrites that artist to "AC-DC".
_SEPARATORS = [" - ", " – ", " — ", "｜", " | ", "~", "："]


@dataclass(frozen=True)
class ParsedName:
    artist: str
    title: str
    confident: bool


def _clean_side(text: str) -> str:
    if not text:
        return ""
    return sanitize_component(text)


def _split(text: str) -> tuple[str, str, bool]:
    for sep in _SEPARATORS:
        if sep in text:
            left, right = text.split(sep, 1)
            return left.strip(), right.strip(), True
    return "", text.strip(), False


def _nearly_same(artist: str, title: str) -> bool:
    left = re.sub(r"\s+", " ", artist.casefold()).strip()
    right = re.sub(r"\s+", " ", title.casefold()).strip()
    if not left or not right:
        return False
    if left == right:
        return True
    return SequenceMatcher(None, left, right).ratio() >= 0.9


def _dominates(artist: str, title: str, remainder: str) -> bool:
    """True when one side is almost the entire remainder, so the split failed."""
    whole = len(remainder.strip())
    if whole == 0:
        return False
    return max(len(artist), len(title)) / whole >= 0.9


def parse_filename(filename: str, *, raw: bool = False) -> ParsedName:
    """Parse artist and title from a filename.

    No separator means a title-only name (artist empty) and is confident when
    the title is non-empty. A split is confident when both sides are non-empty,
    they are not the same string, and neither side is almost the whole name.
    """
    rest = human_stem(filename)
    # Collapse whitespace so "Artist  -  Title" still splits, without stripping
    # a leading separator the way sanitize_component would.
    if not raw:
        rest = re.sub(r"\s+", " ", rest)
    artist, title, separated = _split(rest)
    if not raw:
        artist = _clean_side(artist)
        title = _clean_side(title)
    if not separated:
        return ParsedName("", title, confident=bool(title))
    confident = bool(artist and title) and not (
        _nearly_same(artist, title) or _dominates(artist, title, rest)
    )
    return ParsedName(artist, title, confident)
