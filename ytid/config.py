"""Load and validate the YAML configuration files.

Two files drive classification:

- genre_map.yaml: the coarse folder buckets and a fine->coarse normalize map.
- overrides.yaml: ground-truth artist->genre and per-video decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_DIR = Path("config")


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


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    return data


def load_config(config_dir: str | Path = DEFAULT_CONFIG_DIR) -> Config:
    config_dir = Path(config_dir)

    gm_raw = _load_yaml(config_dir / "genre_map.yaml")
    buckets = gm_raw.get("buckets") or ["other"]
    if "other" not in buckets:
        buckets.append("other")
    normalize = {
        str(k).strip().lower(): str(v).strip().lower()
        for k, v in (gm_raw.get("normalize") or {}).items()
    }
    genre_map = GenreMap(buckets=[str(b).strip().lower() for b in buckets], normalize=normalize)

    ov_raw = _load_yaml(config_dir / "overrides.yaml")
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
