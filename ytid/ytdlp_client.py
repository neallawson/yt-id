"""Thin adapter around the yt-dlp *binary*.

yt-dlp is invoked as an external process (not imported) so it can be updated
independently and frequently -- YouTube changes often break older versions.
All subprocess/JSON messiness is isolated here; the rest of the app only sees
`fetch_metadata()` returning a `FetchResult`.

The yt-dlp version is captured so fetch results can be tied to the exact
version that produced them, enabling selective re-fetching of failures after
an upgrade.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache

DEFAULT_BINARY = "yt-dlp"
WATCH_URL = "https://www.youtube.com/watch?v={id}"

# Substrings in yt-dlp stderr that indicate the video is gone/blocked rather
# than a transient failure. These should not be retried automatically.
_UNAVAILABLE_MARKERS = (
    "video unavailable",
    "private video",
    "removed by the uploader",
    "account associated with this video has been terminated",
    "this video is not available",
    "video has been removed",
    "sign in to confirm your age",
    "who has blocked it in your country",
    "is not available in your country",
)


class YtDlpNotFound(RuntimeError):
    """Raised when the yt-dlp binary cannot be located."""


@dataclass
class FetchResult:
    youtube_id: str
    status: str  # ok|error|unavailable
    metadata: dict | None = None
    error: str | None = None
    ytdlp_version: str | None = None


@lru_cache(maxsize=None)
def get_version(binary: str = DEFAULT_BINARY) -> str:
    """Return the installed yt-dlp version string."""
    if shutil.which(binary) is None:
        raise YtDlpNotFound(
            f"'{binary}' not found on PATH. Install it independently, e.g. "
            f"`pipx install yt-dlp`, so it can be updated frequently."
        )
    out = subprocess.run(
        [binary, "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _classify_error(stderr: str) -> str:
    low = stderr.lower()
    for marker in _UNAVAILABLE_MARKERS:
        if marker in low:
            return "unavailable"
    return "error"


def fetch_metadata(
    youtube_id: str,
    binary: str = DEFAULT_BINARY,
    timeout: float = 60.0,
) -> FetchResult:
    """Fetch metadata JSON for a single video without downloading it."""
    version = get_version(binary)
    url = WATCH_URL.format(id=youtube_id)
    cmd = [
        binary,
        "--skip-download",
        "--no-playlist",
        "--no-warnings",
        "--dump-single-json",
        url,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return FetchResult(
            youtube_id=youtube_id,
            status="error",
            error=f"timeout after {timeout}s",
            ytdlp_version=version,
        )

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        return FetchResult(
            youtube_id=youtube_id,
            status=_classify_error(stderr),
            error=stderr[:2000] or f"exit code {proc.returncode}",
            ytdlp_version=version,
        )

    try:
        metadata = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return FetchResult(
            youtube_id=youtube_id,
            status="error",
            error=f"invalid JSON from yt-dlp: {exc}",
            ytdlp_version=version,
        )

    return FetchResult(
        youtube_id=youtube_id,
        status="ok",
        metadata=metadata,
        ytdlp_version=version,
    )
