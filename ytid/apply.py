"""Execute (or reverse) a move manifest produced by `plan`.

Validation is inline and transactional: the whole manifest is pre-flighted
before any file is touched, each move is verified the instant it completes, and
intent is journaled to the `moves` table *before* the move (write-ahead) so a
crash is always discoverable and reversible.

Failure policy (default `rollback`):
- rollback: on the first failure, reverse every move made in this run.
- stop:     halt at once, leaving completed moves in place (resume later).
- skip:     best-effort; collect errors and continue.

Scope: same-filesystem moves via atomic `os.rename`. Cross-device moves are
rejected at pre-flight for now (planned as a future copy-verify-rename update).
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import db

ON_ERROR_POLICIES = ("rollback", "stop", "skip")


def _load_manifest(manifest_path: str | Path) -> list[dict]:
    with Path(manifest_path).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _move_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("action") == "move" and r.get("to_path")]


def _nearest_existing(path: Path) -> Path:
    p = path
    while not p.exists() and p.parent != p:
        p = p.parent
    return p


def _device_of(path: Path) -> int:
    return os.stat(_nearest_existing(path)).st_dev


def _free_space(path: Path) -> int:
    return shutil.disk_usage(_nearest_existing(path)).free


def _classify(row: dict) -> tuple[str, str | None]:
    """Classify a single move's current state.

    Returns (state, detail) where state is one of:
      'ok'              - ready to move
      'already_applied' - source gone, destination present (idempotent success)
      'error'           - a fatal condition (detail explains)
    """
    src = Path(row["from_path"])
    dst = Path(row["to_path"])

    if not src.exists():
        if dst.exists():
            return "already_applied", None
        return "error", f"neither source nor destination exists (lost file): {src}"
    if dst.exists():
        return "error", f"destination already exists (collision): {dst}"
    if _device_of(src) != _device_of(dst):
        return "error", f"cross-device move not supported yet: {src} -> {dst}"
    try:
        if _free_space(dst) < src.stat().st_size:
            return "error", f"insufficient free space on target for: {dst}"
    except OSError as exc:
        return "error", f"cannot stat source / check space for {dst}: {exc}"
    return "ok", None


def preflight(move_rows: list[dict]) -> tuple[list[dict], list[dict], list[str]]:
    """Validate the whole manifest before any mutation.

    Returns (ready, already_applied, problems).
    """
    ready: list[dict] = []
    already: list[dict] = []
    problems: list[str] = []
    seen_dst: set[str] = set()

    for row in move_rows:
        dst = row["to_path"]
        if dst in seen_dst:
            problems.append(f"duplicate destination in manifest: {dst}")
        seen_dst.add(dst)

        state, detail = _classify(row)
        if state == "ok":
            ready.append(row)
        elif state == "already_applied":
            already.append(row)
        else:
            problems.append(detail or "unknown error")
    return ready, already, problems


def _verify_move(src: Path, dst: Path, size: int) -> str | None:
    """Return an error string if the post-move invariant does not hold."""
    if src.exists():
        return f"source still present after move: {src}"
    if not dst.exists():
        return f"destination missing after move: {dst}"
    try:
        if dst.stat().st_size != size:
            return f"size mismatch after move: {dst}"
    except OSError as exc:
        return f"cannot stat destination after move: {dst}: {exc}"
    return None


def _execute_one(conn, run_id: str, row: dict) -> str:
    """Journal, move, and verify a single row.

    Returns 'moved', 'already_applied', or an error string.
    """
    # Just-in-time re-check: state may have changed since pre-flight.
    state, detail = _classify(row)
    if state == "already_applied":
        return "already_applied"
    if state != "ok":
        return detail or "unknown error"

    src = Path(row["from_path"])
    dst = Path(row["to_path"])
    size = src.stat().st_size
    now = datetime.now(timezone.utc).isoformat()

    # 1. Write-ahead: record intent as 'pending' and commit before moving.
    cur = conn.execute(
        """
        INSERT INTO moves (youtube_id, run_id, from_path, to_path, size, status, applied_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """,
        (row["youtube_id"], run_id, str(src), str(dst), size, now),
    )
    move_id = cur.lastrowid
    conn.commit()

    # 2. Atomic move (same filesystem).
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(str(src), str(dst))
    except OSError as exc:
        # No move happened: drop the pending record so rollback ignores it.
        conn.execute("DELETE FROM moves WHERE id = ?", (move_id,))
        conn.commit()
        return f"move failed: {src} -> {dst}: {exc}"

    # 3. Verify the invariant immediately.
    err = _verify_move(src, dst, size)
    if err:
        # The rename did land the file at dst; mark 'done' so rollback reverses it.
        conn.execute("UPDATE moves SET status = 'done' WHERE id = ?", (move_id,))
        conn.execute(
            "UPDATE videos SET src_path = ? WHERE youtube_id = ?",
            (str(dst), row["youtube_id"]),
        )
        conn.commit()
        return err

    # 4. Commit success.
    conn.execute("UPDATE moves SET status = 'done' WHERE id = ?", (move_id,))
    conn.execute(
        "UPDATE videos SET src_path = ? WHERE youtube_id = ?",
        (str(dst), row["youtube_id"]),
    )
    conn.commit()
    return "moved"


def _rollback_run(conn, run_id: str) -> int:
    """Reverse every completed move from this run, newest first."""
    rows = conn.execute(
        "SELECT id, youtube_id, from_path, to_path FROM moves "
        "WHERE run_id = ? AND status = 'done' ORDER BY id DESC",
        (run_id,),
    ).fetchall()
    now = datetime.now(timezone.utc).isoformat()
    reversed_count = 0
    for mv in rows:
        cur_loc = Path(mv["to_path"])
        orig = Path(mv["from_path"])
        try:
            if cur_loc.exists() and not orig.exists():
                orig.parent.mkdir(parents=True, exist_ok=True)
                os.rename(str(cur_loc), str(orig))
        except OSError:
            # Best-effort: leave this move recorded for manual inspection.
            continue
        conn.execute(
            "UPDATE moves SET status = 'rolled_back', undone_at = ? WHERE id = ?",
            (now, mv["id"]),
        )
        conn.execute(
            "UPDATE videos SET src_path = ? WHERE youtube_id = ?",
            (str(orig), mv["youtube_id"]),
        )
        reversed_count += 1
    conn.commit()
    return reversed_count


def apply_manifest(
    manifest_path: str | Path,
    db_path: str | Path = db.DEFAULT_DB_PATH,
    dry_run: bool = False,
    on_error: str = "rollback",
) -> dict:
    """Validate and execute a move manifest transactionally.

    Returns a result dict with numeric counts plus 'ok' (bool) and 'problems'
    (list of error strings). No files are mutated unless pre-flight is clean and
    dry_run is False.
    """
    if on_error not in ON_ERROR_POLICIES:
        raise ValueError(f"on_error must be one of {ON_ERROR_POLICIES}")

    move_rows = _move_rows(_load_manifest(manifest_path))
    ready, already, problems = preflight(move_rows)

    result = {
        "planned": len(move_rows),
        "moved": 0,
        "already_applied": len(already),
        "skipped": 0,
        "errors": 0,
        "rolled_back": 0,
        "ok": True,
        "problems": list(problems),
        "on_error": on_error,
        "dry_run": dry_run,
    }

    # Pre-flight failures are structural: never mutate anything.
    if problems:
        result["errors"] = len(problems)
        result["ok"] = False
        return result

    if dry_run:
        return result

    run_id = uuid.uuid4().hex
    conn = db.connect(db_path)
    try:
        for row in ready:
            outcome = _execute_one(conn, run_id, row)
            if outcome == "moved":
                result["moved"] += 1
            elif outcome == "already_applied":
                result["already_applied"] += 1
                continue
            else:
                result["errors"] += 1
                result["problems"].append(outcome)
                result["ok"] = False
                if on_error == "skip":
                    continue
                if on_error == "rollback":
                    result["rolled_back"] = _rollback_run(conn, run_id)
                    result["moved"] = 0  # everything this run was reversed
                break  # stop and rollback both abort the loop
        conn.commit()
    finally:
        conn.close()

    return result


def undo_manifest(
    manifest_path: str | Path,
    db_path: str | Path = db.DEFAULT_DB_PATH,
    dry_run: bool = False,
) -> dict[str, int]:
    """Reverse previously applied moves recorded for this manifest's videos."""
    rows = _load_manifest(manifest_path)
    ids = [r["youtube_id"] for r in rows if r.get("action") == "move" and r.get("to_path")]
    counts = {"candidates": len(ids), "restored": 0, "skipped": 0, "errors": 0}

    for yid in ids:
        with db.session(db_path) as conn:
            mv = conn.execute(
                """
                SELECT id, from_path, to_path FROM moves
                 WHERE youtube_id = ? AND status = 'done' AND undone_at IS NULL
                 ORDER BY id DESC LIMIT 1
                """,
                (yid,),
            ).fetchone()

        if mv is None:
            counts["skipped"] += 1
            continue

        src_now = Path(mv["to_path"])   # current location
        dst_orig = Path(mv["from_path"])  # where to restore

        if not src_now.exists() or dst_orig.exists():
            counts["skipped"] += 1
            continue
        if dry_run:
            continue

        try:
            dst_orig.parent.mkdir(parents=True, exist_ok=True)
            os.rename(str(src_now), str(dst_orig))
        except OSError:
            counts["errors"] += 1
            continue

        now = datetime.now(timezone.utc).isoformat()
        with db.session(db_path) as conn:
            conn.execute(
                "UPDATE moves SET status = 'rolled_back', undone_at = ? WHERE id = ?",
                (now, mv["id"]),
            )
            conn.execute(
                "UPDATE videos SET src_path = ? WHERE youtube_id = ?",
                (str(dst_orig), yid),
            )
        counts["restored"] += 1

    return counts
