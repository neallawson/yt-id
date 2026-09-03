import textwrap

import pytest

from ytid.cli import main
from ytid.config import CONFIG_FILES, load_config, resolve_sources


@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    """Run each test from an empty CWD with an empty user config dir.

    This removes the repo's ./config and any real ~/.config/ytid from the
    search path so tests exercise the packaged defaults deterministically.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def test_packaged_defaults_used_when_nothing_else_present():
    cfg = load_config()
    # Packaged genre_map ships the coarse buckets.
    assert "rock" in cfg.genre_map.buckets
    assert "other" in cfg.genre_map.buckets
    assert cfg.genre_map.to_bucket("classic rock") == "rock"
    # Packaged overrides template is intentionally empty.
    assert cfg.overrides.artists == {}
    assert cfg.overrides.videos == {}


def test_explicit_config_dir_wins(tmp_path):
    cfgdir = tmp_path / "custom"
    _write(cfgdir / "genre_map.yaml", """
        buckets:
          - foo
        normalize:
          weird genre: foo
    """)
    cfg = load_config(cfgdir)
    assert "foo" in cfg.genre_map.buckets
    assert "other" in cfg.genre_map.buckets  # always appended
    assert cfg.genre_map.to_bucket("weird genre") == "foo"
    # rock only exists in the packaged default, which was overridden here.
    assert "rock" not in cfg.genre_map.buckets


def test_per_file_fallback_to_packaged(tmp_path):
    # Explicit dir provides only overrides.yaml; genre_map falls back to packaged.
    cfgdir = tmp_path / "custom"
    _write(cfgdir / "overrides.yaml", """
        artists:
          Jethro Tull: rock
    """)
    cfg = load_config(cfgdir)
    assert cfg.overrides.artist_genre("Jethro Tull") == "rock"
    assert "rock" in cfg.genre_map.buckets  # from packaged default


def test_cwd_config_is_picked_up(tmp_path):
    _write(tmp_path / "config" / "genre_map.yaml", """
        buckets:
          - bespoke
    """)
    cfg = load_config()
    assert "bespoke" in cfg.genre_map.buckets


def test_user_config_dir_used(tmp_path):
    _write(tmp_path / "xdg" / "ytid" / "overrides.yaml", """
        artists:
          Ash: rock
    """)
    cfg = load_config()
    assert cfg.overrides.artist_genre("Ash") == "rock"


def test_explicit_dir_beats_cwd_and_user(tmp_path):
    _write(tmp_path / "config" / "genre_map.yaml", "buckets: [cwd]\n")
    _write(tmp_path / "xdg" / "ytid" / "genre_map.yaml", "buckets: [user]\n")
    explicit = tmp_path / "explicit"
    _write(explicit / "genre_map.yaml", "buckets: [explicit]\n")
    cfg = load_config(explicit)
    assert "explicit" in cfg.genre_map.buckets
    assert "cwd" not in cfg.genre_map.buckets
    assert "user" not in cfg.genre_map.buckets


def test_invalid_yaml_top_level_raises(tmp_path):
    cfgdir = tmp_path / "custom"
    _write(cfgdir / "genre_map.yaml", "- just\n- a\n- list\n")
    with pytest.raises(ValueError, match="mapping at the top level"):
        load_config(cfgdir)


# --- resolve_sources / `ytid config path` ----------------------------------


def test_resolve_sources_all_packaged_when_nothing_present():
    sources = resolve_sources()
    assert [s.filename for s in sources] == list(CONFIG_FILES)
    for src in sources:
        assert src.from_packaged is True
        assert src.in_effect == src.packaged
        # every candidate dir was searched and none existed
        assert src.candidates
        assert all(not exists for _, exists in src.candidates)


def test_resolve_sources_reports_active_file(tmp_path):
    _write(tmp_path / "config" / "genre_map.yaml", "buckets: [cwd]\n")
    sources = {s.filename: s for s in resolve_sources()}
    gm = sources["genre_map.yaml"]
    assert gm.from_packaged is False
    assert gm.in_effect == str(tmp_path / "config" / "genre_map.yaml")
    # overrides still falls back to packaged
    assert sources["overrides.yaml"].from_packaged is True


def test_resolve_sources_explicit_dir_is_first_candidate(tmp_path):
    explicit = tmp_path / "explicit"
    sources = resolve_sources(explicit)
    first_paths = [s.candidates[0][0] for s in sources]
    assert all(p == explicit / s.filename for p, s in zip(first_paths, sources))


def test_config_path_command_runs(capsys):
    rc = main(["config", "path"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "genre_map.yaml" in out
    assert "overrides.yaml" in out
    assert "packaged default" in out


def test_config_command_defaults_to_path(capsys):
    assert main(["config"]) == 0
    assert "genre_map.yaml" in capsys.readouterr().out
