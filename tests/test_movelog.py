import json

from ytid import apply as apply_mod
from ytid import cli
from ytid import db
from ytid import movelog
from ytid import scan as scan_mod


def _touch(path, content="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_escape_roundtrip_keeps_tab_and_newline_in_one_line():
    src = "/src/weirdo\tname\nfile.webm"
    dst = "/dest/Artist - Title [aaaaaaaaaaa].webm"
    line = movelog.format_record(src, dst)
    assert "\n" not in line
    assert line.count("\t") == 1
    assert movelog.parse_record(line) == (src, dst)


def test_apply_writes_a_move_log(tmp_path, capsys):
    src = tmp_path / "src"
    db_path = str(tmp_path / "t.db")
    file_a = src / "Song A [aaaaaaaaaaa].mp4"
    _touch(file_a, "aaa")
    scan_mod.scan(src, db_path=db_path)

    dst = tmp_path / "target" / "rock" / "A.mp4"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([
        {"action": "move", "youtube_id": "aaaaaaaaaaa",
         "from_path": str(file_a), "to_path": str(dst)},
    ]))

    rc = cli.main([
        "--db", db_path, "apply", "--manifest", str(manifest), "--apply",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "apply: wrote" in out

    logs = list(tmp_path.glob("manifest.moves.*.log"))
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8")
    assert text.startswith("# yt-id move log\n")
    assert not any(line.startswith("mv ") for line in text.splitlines())

    header, moves = movelog.read_move_log(logs[0])
    assert header["manifest"] == str(manifest.resolve())
    assert header["db"] == str((tmp_path / "t.db").resolve())
    assert header["moved"] == "1"
    assert header["ok"] == "true"
    assert header["tool"] == "yt-id"
    assert "generated_at" in header
    assert header["run_id"]
    assert moves == [(str(file_a), str(dst))]


def test_dry_run_writes_no_log(tmp_path, capsys):
    src = tmp_path / "src"
    db_path = str(tmp_path / "t.db")
    file_a = src / "Song A [aaaaaaaaaaa].mp4"
    _touch(file_a, "aaa")
    scan_mod.scan(src, db_path=db_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([
        {"action": "move", "youtube_id": "aaaaaaaaaaa",
         "from_path": str(file_a), "to_path": str(tmp_path / "target" / "A.mp4")},
    ]))

    rc = cli.main(["--db", db_path, "apply", "--manifest", str(manifest)])
    assert rc == 0
    assert list(tmp_path.glob("manifest.moves.*.log")) == []
    assert "apply: wrote" not in capsys.readouterr().out


def test_explicit_log_path_and_tab_in_filename(tmp_path):
    src = tmp_path / "src"
    db_path = tmp_path / "t.db"
    file_a = src / "Song\tA [aaaaaaaaaaa].mp4"
    _touch(file_a, "aaa")
    scan_mod.scan(src, db_path=str(db_path))
    dst = tmp_path / "target" / "A [aaaaaaaaaaa].mp4"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([
        {"action": "move", "youtube_id": "aaaaaaaaaaa",
         "from_path": str(file_a), "to_path": str(dst)},
    ]))
    log_path = tmp_path / "batch.log"

    result = apply_mod.apply_manifest(str(manifest), db_path=str(db_path), dry_run=False)
    assert movelog.should_write_move_log(result)
    written = movelog.write_run_log(
        log_path, db_path=db_path, manifest_path=manifest, result=result,
    )
    assert written == log_path
    _header, moves = movelog.read_move_log(log_path)
    assert moves == [(str(file_a), str(dst))]

    with db.session(db_path) as conn:
        status = conn.execute(
            "SELECT status FROM moves WHERE run_id = ?", (result["run_id"],)
        ).fetchone()["status"]
    assert status == "done"
