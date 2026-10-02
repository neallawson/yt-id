"""A readable record of one apply run.

This is an audit file, not a script. A ``#`` header describes the batch, then
each completed move is one line: the original path, a tab, the destination
path. Tab, backslash, and newlines inside a path are escaped so a line stays
one record and can be parsed later.
"""

from __future__ import annotations

from datetime import datetime, timezone
from importlib import metadata as _metadata
from pathlib import Path

from . import db

HEADER_KEYS = (
    "generated_at",
    "manifest",
    "db",
    "run_id",
    "on_error",
    "planned",
    "moved",
    "already_applied",
    "rolled_back",
    "errors",
    "ok",
    "tool",
    "tool_version",
)


def should_write_move_log(result: dict) -> bool:
    """A real apply that moved, rolled back, or recorded an error."""
    if result.get("dry_run") or not result.get("run_id"):
        return False
    return bool(result.get("moved") or result.get("rolled_back") or result.get("errors"))


def escape_field(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def unescape_field(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            code = value[i + 1]
            out.append({"\\": "\\", "t": "\t", "r": "\r", "n": "\n"}.get(code, code))
            i += 2
            continue
        out.append(value[i])
        i += 1
    return "".join(out)


def format_record(from_path: str, to_path: str) -> str:
    return f"{escape_field(from_path)}\t{escape_field(to_path)}"


def parse_record(line: str) -> tuple[str, str]:
    src, sep, dst = line.partition("\t")
    if not sep:
        raise ValueError(f"move log line has no tab separator: {line!r}")
    return unescape_field(src), unescape_field(dst)


def _tool_version() -> str:
    try:
        return _metadata.version("yt-id")
    except _metadata.PackageNotFoundError:
        return "-"


def default_log_path(manifest_path: str | Path, generated_at: datetime) -> Path:
    """``<manifest stem>.moves.<UTC stamp>.log`` beside the manifest."""
    manifest = Path(manifest_path)
    stamp = generated_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = manifest.with_name(f"{manifest.stem}.moves.{stamp}.log")
    n = 2
    while candidate.exists():
        candidate = manifest.with_name(f"{manifest.stem}.moves.{stamp}-{n}.log")
        n += 1
    return candidate


def _completed_moves(db_path: str | Path, run_id: str) -> list[tuple[str, str]]:
    with db.session(db_path) as conn:
        rows = conn.execute(
            """
            SELECT from_path, to_path FROM moves
             WHERE run_id = ? AND status = 'done'
             ORDER BY id
            """,
            (run_id,),
        ).fetchall()
    return [(r["from_path"], r["to_path"]) for r in rows]


def write_run_log(
    log_path: str | Path | None,
    *,
    db_path: str | Path,
    manifest_path: str | Path,
    result: dict,
    generated_at: datetime | None = None,
) -> Path:
    """Write the move log for one apply run. Returns the path written."""
    when = generated_at or datetime.now(timezone.utc)
    path = Path(log_path) if log_path else default_log_path(manifest_path, when)
    header = {
        "generated_at": when.astimezone(timezone.utc).isoformat(),
        "manifest": str(Path(manifest_path).resolve()),
        "db": str(Path(db_path).resolve()),
        "run_id": result.get("run_id") or "",
        "on_error": result.get("on_error") or "",
        "planned": result.get("planned", 0),
        "moved": result.get("moved", 0),
        "already_applied": result.get("already_applied", 0),
        "rolled_back": result.get("rolled_back", 0),
        "errors": result.get("errors", 0),
        "ok": "true" if result.get("ok") else "false",
        "tool": "yt-id",
        "tool_version": _tool_version(),
    }
    moves = _completed_moves(db_path, header["run_id"])
    lines = [
        "# yt-id move log",
        "# format: one move per line; from_path, a tab, to_path",
        "# escape: \\\\ \\t \\n \\r",
    ]
    for key in HEADER_KEYS:
        lines.append(f"# {key}: {header[key]}")
    lines.append("")
    lines.extend(format_record(src, dst) for src, dst in moves)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_move_log(path: str | Path) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Read a move log back into a header mapping and ``(from, to)`` pairs."""
    header: dict[str, str] = {}
    moves: list[tuple[str, str]] = []
    in_body = False
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not in_body:
            if line == "":
                in_body = True
            elif line.startswith("# ") and ": " in line:
                key, value = line[2:].split(": ", 1)
                if key in HEADER_KEYS:
                    header[key] = value
            continue
        if line:
            moves.append(parse_record(line))
    return header, moves
