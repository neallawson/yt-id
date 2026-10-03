"""Top-level help for ``yt-id`` and ``yt-id --help``.

``yt-id`` prints the command reference. ``yt-id --help`` prints the manual
sections and then that same reference. ``yt-id <command> --help`` stays on
argparse.
"""

from __future__ import annotations

import textwrap

_SYNOPSIS = "yt-id [--db PATH] [-h | --help] [-v | --version] COMMAND [FLAGS]"

_WIDTH = 80
# Command text starts just past the longest command name. Flag text starts
# further right, after the flag's own indent, so the two columns stay distinct.
_COMMAND_COLUMN = 16
_FLAG_COLUMN = 24


def _entry(term: str, text: str, *, indent: int = 0, column: int = _COMMAND_COLUMN) -> str:
    """One term and its description, description starting on that same line."""
    head = (" " * indent) + term
    if len(head) + 2 >= column:
        gap = "  "
        hang = " " * (len(head) + 2)
    else:
        gap = " " * (column - len(head))
        hang = " " * column
    words = text.split()
    lines: list[str] = []
    buf = head
    for word in words:
        trial = (head + gap + word) if buf == head else buf + " " + word
        if len(trial) <= _WIDTH or buf == head:
            buf = trial
        else:
            lines.append(buf.rstrip())
            buf = hang + word
    lines.append(buf.rstrip())
    return "\n".join(lines)


def _example(term: str, example: str, *, indent: int = 0, column: int = _COMMAND_COLUMN) -> str:
    head = (" " * indent) + term
    col = max(column, len(head) + 2)
    return " " * col + example


def _command(name: str, summary: str, flags: list[tuple[str, str, str | None]]) -> str:
    lines = [_entry(name, summary, column=_COMMAND_COLUMN)]
    for flag, text, example in flags:
        lines.append(_entry(flag, text, indent=4, column=_FLAG_COLUMN))
        if example:
            lines.append(_example(flag, example, indent=4, column=_FLAG_COLUMN))
    return "\n".join(lines)


def _section(title: str, body: str) -> str:
    return title + "\n" + body.rstrip()


def _prose(*paragraphs: str) -> str:
    return "\n\n".join(
        textwrap.fill(
            paragraph,
            width=_WIDTH,
            initial_indent="    ",
            subsequent_indent="    ",
            break_on_hyphens=False,
        )
        for paragraph in paragraphs
    )


USAGE_SUMMARY = "\n\n".join([
    _section(
        "NAME",
        "    yt-id — organize YouTube-sourced videos into genre and artist folders",
    ),
    _section(
        "SYNOPSIS",
        "    " + _SYNOPSIS,
    ),
    _section(
        "DESCRIPTION",
        _prose(
            "yt-id reads the 11-character YouTube id in each filename, fetches the title and artist with yt-dlp, and renames the file Artist - Title [id].ext. A file with no artist is Title [id].ext. Nothing is moved until you pass --apply.",
            "A move needs a title. Artist and genre are optional. Pass --require-artist or --require-genre to classify when a missing value should block the move.",
            "Run from the folder you are organizing. Pass --db before the command when the database lives somewhere else. yt-dlp must be on your PATH. It is a separate program so you can update it when YouTube changes.",
        ),
    ),
    _section(
        "FILES",
        "\n".join([
            _entry("ytid.db", "Scans, fetches, and decisions. Default: the current directory.", indent=4, column=20),
            _entry("ytid.yaml", "The file you edit: corrections, blanks, and skip or review.", indent=4, column=20),
        ]),
    ),
    _section(
        "WORKFLOW",
        "\n".join([
            "    yt-id scan",
            "    yt-id fetch",
            "    yt-id classify",
            "    # edit ytid.yaml, then:",
            "    yt-id classify",
            "    yt-id plan --target \"/Music Videos\"",
            "    yt-id apply --manifest manifest.json",
            "    yt-id apply --manifest manifest.json --apply",
        ]),
    ),
    _section(
        "FILENAME SUGGESTIONS",
        _prose(
            "The first classify fills an empty artist and title in ytid.yaml when YouTube returned neither. The id is removed, Artist - Title is split, and each side is cleaned the same way as a destination name ([Live] becomes (Live), AC/DC becomes AC-DC). A clean suggestion leaves action blank, so the second classify moves it. A bad split is stored with action: review. --raw-artist-title keeps the split text as it appeared in the filename.",
        ),
    ),
    _section(
        "DESTINATION NAMES",
        _prose(
            "plan never moves files. The id is appended after cleanup, so its brackets stay. apply checks the manifest and moves only with --apply. On failure the default is to roll the run back. A real apply also writes a move log: a header, then one line per move (original path, a tab, destination path). export writes a ledger of the whole corpus.",
            "--strict-names drops shell-hostile punctuation. Parentheses stay, so (Live) is still (Live). --spaces-to-underscores turns spaces into _.",
        ),
    ),
])


def command_reference() -> str:
    commands = [
        _command(
            "scan",
            "Index the current directory and record each video's YouTube id. Files with no id are listed in ytid.yaml under unidentified.",
            [
                ("--source DIR", "Folder to scan recursively. Default: the current directory.", "yt-id scan --source ~/Videos"),
                ("--worklist PATH", "Where to append new unidentified files. Default: ytid.yaml.", None),
            ],
        ),
        _command(
            "fetch",
            "Ask yt-dlp for metadata. Slow and resumable. Only pending rows are fetched. The database keeps the fields classify uses, not the full yt-dlp dump. An unavailable or error row is added to ytid.yaml.",
            [
                ("--sleep SECONDS", "Pause between requests. Default: 2.", None),
                ("--jitter SECONDS", "Extra random delay, from 0 up to this many seconds. Default: 1.", None),
                ("--limit N", "Fetch at most N videos this run.", "yt-id fetch --limit 20"),
                ("--timeout SECONDS", "Give up on one request after this long. Default: 60.", None),
                ("--retry-errors", "Also retry rows whose last fetch was a transient error.", None),
                ("--retry-unavailable", "Also retry deleted, private, or blocked videos.", None),
                ("--binary PATH", "yt-dlp program to run. Default: yt-dlp on PATH.", None),
                ("--worklist PATH", "Where to append fetch failures. Default: ytid.yaml.", None),
            ],
        ),
        _command(
            "classify",
            "Decide move, review, or skip. A title is required. Artist and genre are optional unless you require them. ytid.yaml wins over a guess. Empty artist and title are then suggested from the filename. Run classify again after you edit the file.",
            [
                ("--require-artist", "Leave a file in review when it has no artist.", "yt-id classify --require-artist"),
                ("--require-genre", "Leave a file in review when it has no genre.", None),
                ("--raw-artist-title", "Keep a filename suggestion as it appeared in the name. The default cleans it ([Live] becomes (Live), AC/DC becomes AC-DC).", None),
                ("--config DIR", "Config directory. Overrides ./config, the user config dir, and the packaged defaults, one file at a time.", None),
                ("--worklist PATH", "File of record applied before deciding. Default: ytid.yaml. An empty string ignores it.", None),
            ],
        ),
        _command(
            "worklist",
            "Print ytid.yaml. This does not write the file. --list is the same print and is optional.",
            [
                ("--sync", "Append new pending rows and fill empty artist and title from the filename. Answers you typed stay. Comments in the file can be dropped.", "yt-id worklist --sync"),
                ("--raw-artist-title", "With --sync, skip character cleanup on filename suggestions.", None),
                ("--missing-artist", "Print entries with an empty artist. OR-combined with the other --missing-* flags.", None),
                ("--missing-title", "Print entries with an empty title. OR-combined with the other --missing-* flags.", "yt-id worklist --missing-artist --missing-title"),
                ("--missing-genre", "Print entries with an empty genre. OR-combined with the other --missing-* flags.", None),
                ("--action ACTION", "Filter the printout. move, review, and skip match the decision. blank matches an empty action field. all (default) does not filter.", None),
                ("--compact", "One tab-separated line: line number, action, id, filename.", "yt-id worklist --missing-genre --compact"),
                ("--worklist PATH", "File to print or sync. Default: ytid.yaml.", None),
            ],
        ),
        _command(
            "review",
            "Print classify decisions. The default is the review bucket.",
            [
                ("--action ACTION", "review (default), move, skip, or all.", "yt-id review --action move"),
            ],
        ),
        _command(
            "plan",
            "Write a dry-run manifest. Nothing is moved. Destination: <target>/<genre>/<Artist>/Artist - Title [id].ext.",
            [
                ("--target DIR", "Required. Root folder the manifest will move files into.", "yt-id plan --target \"/Music Videos\""),
                ("--out PREFIX", "Write PREFIX.json and PREFIX.csv. Default: manifest.", None),
                ("--omit-artist-from-filename", "Inside an artist folder, name the file Title [id].ext. A file with no artist folder still includes the artist.", None),
                ("--min-artist-files N", "Create an artist folder only when at least N files share that artist. Default: 1. Smaller groups move up one level.", None),
                ("--strict-names", "Also drop shell-hostile punctuation. & is written as \"and\". Parentheses stay, so (Live) is still (Live).", None),
                ("--spaces-to-underscores", "Replace each space with _, including the space before [id].", None),
            ],
        ),
        _command(
            "apply",
            "Check a manifest, and move files only with --apply. Without it, this is a dry run. A real apply writes a move log beside the manifest.",
            [
                ("--manifest FILE", "Required. The .json file from plan.", "yt-id apply --manifest manifest.json --apply"),
                ("--apply", "Actually move, or with --undo actually move back.", None),
                ("--undo", "Reverse moves recorded for this manifest. Still a dry run until you add --apply.", "yt-id apply --manifest manifest.json --undo --apply"),
                ("--on-error POLICY", "rollback (default) undoes this run. stop halts and keeps completed moves. skip records the error and continues.", None),
                ("--log PATH", "Move-log path. Default: <manifest>.moves.<timestamp>.log beside the manifest.", None),
            ],
        ),
        _command(
            "export",
            "Write a ledger of every tracked file. An archive view, not a script and not a restore format.",
            [
                ("--out PREFIX", "Output prefix. Default: ledger (ledger.json and ledger.csv).", "yt-id export --format csv --out audit"),
                ("--format FORMAT", "csv, json, or both (default).", None),
            ],
        ),
        _command(
            "unresolved",
            "List files that still need an id, or that were flagged duplicate.",
            [
                ("--all", "List every tracked file.", None),
                ("--include-dash", "Also list dash-suffix ids, which are less certain than [id].", None),
            ],
        ),
        _command(
            "resolve",
            "Set an id, or ignore a file, without editing ytid.yaml. Pick one of --id or --path, and one of --youtube-id or --ignore.",
            [
                ("--id N", "videos.id from the unresolved listing.", None),
                ("--path PATH", "Exact source path of the file.", None),
                ("--youtube-id ID", "Assign this 11-character id and mark the row resolved.", "yt-id resolve --id 12 --youtube-id dQw4w9WgXcQ"),
                ("--ignore", "Leave the row tracked and never fetch or move it.", "yt-id resolve --path /videos/skip-me.webm --ignore"),
            ],
        ),
        _command(
            "config",
            "Show which genre map and overrides files are in effect.",
            [
                ("path", "The only action, and the default, so `yt-id config` is enough.", None),
                ("--config DIR", "Look in this directory first, same as classify.", None),
            ],
        ),
    ]
    usage = "\n".join([
        "USAGE",
        "",
        "    " + _SYNOPSIS,
        "",
        "where",
        _entry(
            "--db PATH",
            "SQLite database, placed before the command. Default: ytid.db in the current directory.",
        ),
        _example("--db PATH", "yt-id --db ~/library.db scan"),
        _entry(
            "-h, --help",
            "Detailed help. Alone, print the manual and this reference. After a command, print that command's flags.",
        ),
        _example("-h, --help", "yt-id -h"),
        _example("-h, --help", "yt-id scan -h"),
        _entry("-v, --version", "Print the version and exit."),
        _example("-v, --version", "yt-id --version"),
    ])
    body = "COMMANDS\n\n" + "\n\n".join(commands)
    return usage + "\n\n" + body + "\n"


def usage_help() -> str:
    return USAGE_SUMMARY.rstrip() + "\n\n" + command_reference()
