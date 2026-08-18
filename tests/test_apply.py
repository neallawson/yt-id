import json

from ytid import apply as apply_mod
from ytid import db
from ytid import scan as scan_mod


def _touch(path, content="x"):
    with open(path, "w") as fh:
        fh.write(content)


def _setup(tmp_path):
    """Two source files with bracket IDs, scanned into a fresh DB."""
    src = tmp_path / "src"
    src.mkdir()
    target = tmp_path / "target"
    db_path = str(tmp_path / "t.db")

    file_a = src / "Song A [aaaaaaaaaaa].mp4"
    file_b = src / "Song B [bbbbbbbbbbb].mp4"
    _touch(file_a, "aaa")
    _touch(file_b, "bbbbb")
    scan_mod.scan(src, db_path=db_path)
    return db_path, target, file_a, file_b


def _write_manifest(tmp_path, rows):
    path = tmp_path / "manifest.json"
    with open(path, "w") as fh:
        json.dump(rows, fh)
    return str(path)


def _src_path(db_path, yid):
    with db.session(db_path) as conn:
        return conn.execute(
            "SELECT src_path FROM videos WHERE youtube_id = ?", (yid,)
        ).fetchone()["src_path"]


def test_apply_success_moves_and_journals(tmp_path):
    db_path, target, file_a, file_b = _setup(tmp_path)
    dst_a = target / "rock" / "A.mp4"
    manifest = _write_manifest(tmp_path, [
        {"action": "move", "youtube_id": "aaaaaaaaaaa",
         "from_path": str(file_a), "to_path": str(dst_a)},
    ])

    result = apply_mod.apply_manifest(manifest, db_path=db_path, dry_run=False)

    assert result["ok"] is True
    assert result["moved"] == 1
    assert result["errors"] == 0
    assert dst_a.exists()
    assert not file_a.exists()
    assert _src_path(db_path, "aaaaaaaaaaa") == str(dst_a)

    with db.session(db_path) as conn:
        mv = conn.execute(
            "SELECT status, size FROM moves WHERE youtube_id = 'aaaaaaaaaaa'"
        ).fetchone()
    assert mv["status"] == "done"
    assert mv["size"] == 3


def test_dry_run_mutates_nothing(tmp_path):
    db_path, target, file_a, _ = _setup(tmp_path)
    dst_a = target / "rock" / "A.mp4"
    manifest = _write_manifest(tmp_path, [
        {"action": "move", "youtube_id": "aaaaaaaaaaa",
         "from_path": str(file_a), "to_path": str(dst_a)},
    ])

    result = apply_mod.apply_manifest(manifest, db_path=db_path, dry_run=True)
    assert result["ok"] is True
    assert result["moved"] == 0
    assert file_a.exists()
    assert not dst_a.exists()


def test_preflight_collision_aborts_without_mutation(tmp_path):
    db_path, target, file_a, _ = _setup(tmp_path)
    dst_a = target / "rock" / "A.mp4"
    dst_a.parent.mkdir(parents=True)
    _touch(dst_a, "existing")  # destination already exists -> collision

    manifest = _write_manifest(tmp_path, [
        {"action": "move", "youtube_id": "aaaaaaaaaaa",
         "from_path": str(file_a), "to_path": str(dst_a)},
    ])

    result = apply_mod.apply_manifest(manifest, db_path=db_path, dry_run=False)
    assert result["ok"] is False
    assert result["errors"] == 1
    assert any("collision" in p for p in result["problems"])
    # Nothing moved.
    assert file_a.exists()
    assert dst_a.read_text() == "existing"


def test_already_applied_is_idempotent(tmp_path):
    db_path, target, file_a, _ = _setup(tmp_path)
    dst_a = target / "rock" / "A.mp4"
    manifest = _write_manifest(tmp_path, [
        {"action": "move", "youtube_id": "aaaaaaaaaaa",
         "from_path": str(file_a), "to_path": str(dst_a)},
    ])

    first = apply_mod.apply_manifest(manifest, db_path=db_path, dry_run=False)
    assert first["moved"] == 1
    # Re-running the same manifest: source gone, dest present -> already applied.
    second = apply_mod.apply_manifest(manifest, db_path=db_path, dry_run=False)
    assert second["ok"] is True
    assert second["already_applied"] == 1
    assert second["moved"] == 0


def _failing_manifest(tmp_path, file_a, file_b, target):
    """A succeeds; B fails because its destination parent is a regular file."""
    good = target / "good" / "A.mp4"
    bad_parent = target / "bad"
    bad_parent.parent.mkdir(parents=True, exist_ok=True)
    _touch(bad_parent, "iamafile")  # mkdir(target/bad) will fail
    bad = bad_parent / "B.mp4"
    manifest = _write_manifest(tmp_path, [
        {"action": "move", "youtube_id": "aaaaaaaaaaa",
         "from_path": str(file_a), "to_path": str(good)},
        {"action": "move", "youtube_id": "bbbbbbbbbbb",
         "from_path": str(file_b), "to_path": str(bad)},
    ])
    return manifest, good, bad


def test_rollback_reverses_completed_moves(tmp_path):
    db_path, target, file_a, file_b = _setup(tmp_path)
    manifest, good, _bad = _failing_manifest(tmp_path, file_a, file_b, target)

    result = apply_mod.apply_manifest(
        manifest, db_path=db_path, dry_run=False, on_error="rollback"
    )
    assert result["ok"] is False
    assert result["errors"] == 1
    assert result["rolled_back"] == 1
    assert result["moved"] == 0
    # A must be back where it started; nothing left in target/good.
    assert file_a.exists()
    assert not good.exists()
    assert _src_path(db_path, "aaaaaaaaaaa") == str(file_a)

    with db.session(db_path) as conn:
        statuses = {
            r["youtube_id"]: r["status"]
            for r in conn.execute("SELECT youtube_id, status FROM moves")
        }
    assert statuses.get("aaaaaaaaaaa") == "rolled_back"


def test_stop_leaves_completed_moves_in_place(tmp_path):
    db_path, target, file_a, file_b = _setup(tmp_path)
    manifest, good, _bad = _failing_manifest(tmp_path, file_a, file_b, target)

    result = apply_mod.apply_manifest(
        manifest, db_path=db_path, dry_run=False, on_error="stop"
    )
    assert result["ok"] is False
    assert result["moved"] == 1
    assert result["rolled_back"] == 0
    assert good.exists()
    assert not file_a.exists()


def test_skip_continues_collecting_errors(tmp_path):
    db_path, target, file_a, file_b = _setup(tmp_path)
    manifest, good, _bad = _failing_manifest(tmp_path, file_a, file_b, target)

    result = apply_mod.apply_manifest(
        manifest, db_path=db_path, dry_run=False, on_error="skip"
    )
    assert result["ok"] is False
    assert result["moved"] == 1
    assert result["errors"] == 1
    assert result["rolled_back"] == 0
    assert good.exists()
