"""Load and validate the YAML configuration files.

Two files drive classification:

- genre_map.yaml: the coarse folder buckets and a fine->coarse normalize map.
- overrides.yaml: ground-truth artist->genre and per-video decisions.

Each file is resolved independently across a search path so the tool works both
from the source tree and once installed anywhere. Precedence (first match wins,
per file), with packaged defaults as the always-present fallback:

    1. an explicit --config directory (the ``config_dir`` argument)
    2. ``./config`` in the current working directory (dev convenience)
    3. the user config dir ($XDG_CONFIG_HOME/ytid, else ~/.config/ytid)
    4. the defaults bundled inside the package (ytid/data)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

GENRE_MAP_FILE = "genre_map.yaml"
OVERRIDES_FILE = "overrides.yaml"
CONFIG_FILES = (GENRE_MAP_FILE, OVERRIDES_FILE)
_PACKAGE_DATA = "ytid.data"


@dataclass
class GenreMap:
    buckets: list[str]
    normalize: dict[str, str] = field(default_factory=dict)

    def to_bucket(self, raw_genre: str | None) -> str | None:
        """Map an arbitrary genre string to a coarse bucket, or None if unknown."""
        if not raw_genre:
            return None
        key = raw_genre.strip().lower()
        if key in self.buckets:
            return key
        return self.normalize.get(key)


@dataclass
class VideoOverride:
    artist: str | None = None
    genre: str | None = None
    action: str | None = None  # move|review|skip


@dataclass
class Overrides:
    artists: dict[str, str] = field(default_factory=dict)
    videos: dict[str, VideoOverride] = field(default_factory=dict)

    def artist_genre(self, artist: str | None) -> str | None:
        if not artist:
            return None
        # case-insensitive lookup
        lowered = {k.lower(): v for k, v in self.artists.items()}
        return lowered.get(artist.strip().lower())


@dataclass
class Config:
    genre_map: GenreMap
    overrides: Overrides


def _parse_yaml(fh: Any, source: str) -> dict[str, Any]:
    data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{source} must contain a mapping at the top level")
    return data


def _user_config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "ytid"
    return Path.home() / ".config" / "ytid"


def _search_dirs(config_dir: str | Path | None) -> list[Path]:
    dirs: list[Path] = []
    if config_dir is not None:
        dirs.append(Path(config_dir))
    dirs.append(Path.cwd() / "config")
    dirs.append(_user_config_dir())
    return dirs


def _read_config_file(filename: str, config_dir: str | Path | None) -> dict[str, Any]:
    """Return the first matching config file's data, else the packaged default."""
    for directory in _search_dirs(config_dir):
        path = directory / filename
        if path.is_file():
            with path.open("r", encoding="utf-8") as fh:
                return _parse_yaml(fh, str(path))

    resource = resources.files(_PACKAGE_DATA).joinpath(filename)
    with resource.open("r", encoding="utf-8") as fh:
        return _parse_yaml(fh, f"{_PACKAGE_DATA}/{filename}")


def packaged_path(filename: str) -> str:
    """Return a display path for a packaged default config file."""
    return str(resources.files(_PACKAGE_DATA).joinpath(filename))


@dataclass
class ConfigResolution:
    """Where a single config file is resolved from, and the paths considered."""

    filename: str
    candidates: list[tuple[Path, bool]]  # ordered (path, exists) for each search dir
    packaged: str                        # display path of the packaged default
    in_effect: str                       # the path actually used
    from_packaged: bool                  # True when the packaged default is used


def resolve_sources(config_dir: str | Path | None = None) -> list[ConfigResolution]:
    """Report, per config file, which source is in effect and what was searched.

    Mirrors the resolution used by load_config so `ytid config path` can show the
    user exactly which files are active without loading/parsing them.
    """
    results: list[ConfigResolution] = []
    for filename in CONFIG_FILES:
        candidates: list[tuple[Path, bool]] = []
        in_effect: str | None = None
        for directory in _search_dirs(config_dir):
            path = directory / filename
            exists = path.is_file()
            candidates.append((path, exists))
            if in_effect is None and exists:
                in_effect = str(path)
        packaged = packaged_path(filename)
        from_packaged = in_effect is None
        results.append(
            ConfigResolution(
                filename=filename,
                candidates=candidates,
                packaged=packaged,
                in_effect=packaged if from_packaged else in_effect,
                from_packaged=from_packaged,
            )
        )
    return results


def load_config(config_dir: str | Path | None = None) -> Config:
    gm_raw = _read_config_file(GENRE_MAP_FILE, config_dir)
    buckets = gm_raw.get("buckets") or ["other"]
    if "other" not in buckets:
        buckets.append("other")
    normalize = {
        str(k).strip().lower(): str(v).strip().lower()
        for k, v in (gm_raw.get("normalize") or {}).items()
    }
    genre_map = GenreMap(buckets=[str(b).strip().lower() for b in buckets], normalize=normalize)

    ov_raw = _read_config_file(OVERRIDES_FILE, config_dir)
    artists = {str(k): str(v).strip().lower() for k, v in (ov_raw.get("artists") or {}).items()}
    videos: dict[str, VideoOverride] = {}
    for vid, spec in (ov_raw.get("videos") or {}).items():
        spec = spec or {}
        videos[str(vid)] = VideoOverride(
            artist=spec.get("artist"),
            genre=(str(spec["genre"]).strip().lower() if spec.get("genre") else None),
            action=spec.get("action"),
        )

    return Config(genre_map=genre_map, overrides=Overrides(artists=artists, videos=videos))
