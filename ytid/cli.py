"""Command-line entry point wiring the five pipeline stages together.

    yt-id scan     --source DIR
    yt-id fetch    [--sleep S --jitter J --limit N --retry-errors ...]
    yt-id classify
    yt-id plan     --target DIR [--out PREFIX]
    yt-id apply    --manifest FILE [--undo] [--apply]

Everything defaults to safe/dry-run behavior; `apply` requires an explicit
--apply flag to actually move files.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import apply as apply_mod
from . import classify as classify_mod
from . import config as config_mod
from . import db
from . import fetch as fetch_mod
from . import plan as plan_mod
from . import resolve as resolve_mod
from . import scan as scan_mod
from . import worklist as worklist_mod
from .ytdlp_client import DEFAULT_BINARY, YtDlpNotFound, get_version


def _sync_worklist(args) -> None:
    """Refresh the working-dir ytid.yaml and point the user at anything pending."""
    stats = worklist_mod.sync_worklist(db_path=args.db, path=args.worklist)
    pending = stats["pending_videos"] + stats["pending_unidentified"]
    if not pending:
        return
    added = stats["added_videos"] + stats["added_unidentified"]
    note = f" (+{added} new)" if added else ""
    print(
        f"worklist: {pending} item(s) need details{note} -> edit {args.worklist} "
        f"then run `yt-id classify`"
    )


def _cmd_scan(args) -> int:
    counts = scan_mod.scan(args.source, db_path=args.db)
    print(
        f"scan: seen={counts['seen']} new={counts['new']} updated={counts['updated']}"
    )
    tally = resolve_mod.counts_by_status(db_path=args.db)
    print(
        "scan: status "
        + " ".join(f"{k}={v}" for k, v in sorted(tally.items()))
    )
    _sync_worklist(args)
    return 0


def _cmd_unresolved(args) -> int:
    statuses = resolve_mod.NEEDS_ATTENTION if not args.all else ()
    rows = resolve_mod.list_videos(
        db_path=args.db, statuses=statuses, include_dash=args.include_dash
    )
    if not rows:
        print("unresolved: nothing needs attention")
        return 0
    for r in rows:
        yid = r["youtube_id"] or r["detected_id"] or "-"
        print(
            f"[{r['id']:>5}] {r['resolve_status']:<10} src={r['id_source']:<7} "
            f"id={yid:<12} {r['filename']}"
        )
    print(f"unresolved: {len(rows)} file(s) listed")
    return 0


def _cmd_resolve(args) -> int:
    try:
        if args.ignore:
            row = resolve_mod.set_ignored(row_id=args.id, path=args.path, db_path=args.db)
            print(f"resolve: row {row['id']} marked ignored ({row['filename']})")
        else:
            row = resolve_mod.assign_id(
                args.youtube_id, row_id=args.id, path=args.path, db_path=args.db
            )
            print(
                f"resolve: row {row['id']} -> youtube_id={row['youtube_id']} "
                f"(resolved) ({row['filename']})"
            )
    except (ValueError, LookupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def _cmd_fetch(args) -> int:
    try:
        version = get_version(args.binary)
    except YtDlpNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"fetch: using yt-dlp {version}")

    def progress(idx, total, yid, status):
        print(f"  [{idx}/{total}] {yid} -> {status}")

    counts = fetch_mod.fetch_pending(
        db_path=args.db,
        binary=args.binary,
        sleep=args.sleep,
        jitter=args.jitter,
        limit=args.limit,
        retry_errors=args.retry_errors,
        retry_unavailable=args.retry_unavailable,
        timeout=args.timeout,
        progress=progress,
    )
    print(
        f"fetch: attempted={counts['attempted']} ok={counts['ok']} "
        f"error={counts['error']} unavailable={counts['unavailable']}"
    )
    _sync_worklist(args)
    return 0


def _cmd_classify(args) -> int:
    counts = classify_mod.classify_all(
        db_path=args.db, config_dir=args.config,
        allow_missing_genre=not args.require_genre,
        worklist_path=args.worklist,
    )
    print(
        f"classify: total={counts['total']} move={counts.get('move', 0)} "
        f"review={counts.get('review', 0)} skip={counts.get('skip', 0)}"
    )
    # Fold the freshly-created review bucket into the file of record so the
    # low-confidence items are visible and fixable (they only exist after this
    # step, so scan/fetch could not have listed them).
    _sync_worklist(args)
    return 0


def _cmd_config(args) -> int:
    sources = config_mod.resolve_sources(args.config)
    print(
        "config: search order per file (first existing wins; "
        "packaged default is the fallback)"
    )
    for src in sources:
        origin = "packaged default" if src.from_packaged else "in effect"
        print(f"\n{src.filename} -> {src.in_effect}  [{origin}]")
        for path, exists in src.candidates:
            active = not src.from_packaged and str(path) == src.in_effect
            mark = "*" if active else ("x" if exists else " ")
            print(f"    [{mark}] {path}")
        pkg_mark = "*" if src.from_packaged else " "
        print(f"    [{pkg_mark}] {src.packaged}  (packaged default)")
    return 0


def _cmd_worklist(args) -> int:
    if args.list:
        items = worklist_mod.pending_items(db_path=args.db)
        for r in items["unidentified"]:
            print(f"[no-id]  {r['resolve_status']:<10} {r['filename']}")
        for r in items["videos"]:
            print(f"{r['youtube_id']}  fetch={r['fetch_status']:<11} {r['filename']}")
        total = len(items["videos"]) + len(items["unidentified"])
        print(
            f"worklist: {len(items['unidentified'])} unidentified, "
            f"{len(items['videos'])} need details ({total} total)"
        )
        return 0

    stats = worklist_mod.sync_worklist(db_path=args.db, path=args.worklist)
    pending = stats["pending_videos"] + stats["pending_unidentified"]
    if not stats["wrote"]:
        print("worklist: nothing needs attention (no file written)")
        return 0
    added = stats["added_videos"] + stats["added_unidentified"]
    print(
        f"worklist: wrote {args.worklist} -- {pending} item(s) need details "
        f"(+{added} new). Fill in the blanks, then run `yt-id classify`."
    )
    return 0


def _cmd_review(args) -> int:
    rows = classify_mod.list_decisions(db_path=args.db, action=args.action)
    if not rows:
        print(f"review: no decisions with action={args.action}")
        return 0
    for r in rows:
        meta = {}
        if r["raw_json"]:
            try:
                meta = json.loads(r["raw_json"])
            except (ValueError, TypeError):
                meta = {}
        print(
            f"{r['youtube_id']}  [{r['action']}] conf={r['confidence']:.2f} "
            f"fetch={r['fetch_status']}"
        )
        print(f"    file:    {r['filename']}")
        if meta:
            fetched_title = meta.get("title") or "-"
            channel = meta.get("channel") or meta.get("uploader") or "-"
            struct = []
            if meta.get("artist"):
                struct.append(f"artist={meta['artist']!r}")
            if meta.get("track"):
                struct.append(f"track={meta['track']!r}")
            if meta.get("genre"):
                struct.append(f"genre={meta['genre']!r}")
            struct_str = " ".join(struct) if struct else "(none from YouTube)"
            print(f"    fetched: title={fetched_title!r}  channel={channel!r}")
            print(f"    fetched: structured {struct_str}")
        else:
            print("    fetched: (no metadata)")
        print(
            f"    parsed:  artist={r['artist'] or '-'!r}  "
            f"title={r['title'] or '-'!r}  genre={r['genre'] or '-'}"
        )
        print(f"    reason:  {r['reason']}")
    print(f"review: {len(rows)} item(s) with action={args.action}")
    return 0


def _cmd_plan(args) -> int:
    planned = plan_mod.build_plan(
        args.target, db_path=args.db, clean_names=args.clean_names,
        enhance_names=args.enhance_names, min_artist_files=args.min_artist_files,
    )
    paths = plan_mod.write_manifest(planned, args.out)
    summary = plan_mod.summarize(planned)
    print(
        f"plan: total={summary['total']} move={summary.get('move', 0)} "
        f"review={summary.get('review', 0)} skip={summary.get('skip', 0)}"
    )
    print(f"plan: wrote {paths['json']} and {paths['csv']} (dry-run, nothing moved)")
    return 0


def _cmd_apply(args) -> int:
    if args.undo:
        counts = apply_mod.undo_manifest(args.manifest, db_path=args.db, dry_run=not args.apply)
        mode = "APPLY" if args.apply else "dry-run"
        print(
            f"undo ({mode}): candidates={counts['candidates']} "
            f"restored={counts['restored']} skipped={counts['skipped']} "
            f"errors={counts['errors']}"
        )
        return 0

    result = apply_mod.apply_manifest(
        args.manifest, db_path=args.db, dry_run=not args.apply, on_error=args.on_error,
    )
    mode = "APPLY" if args.apply else "dry-run"
    print(
        f"apply ({mode}, on-error={result['on_error']}): "
        f"planned={result['planned']} moved={result['moved']} "
        f"already_applied={result['already_applied']} "
        f"rolled_back={result['rolled_back']} errors={result['errors']}"
    )
    for problem in result["problems"]:
        print(f"  ! {problem}", file=sys.stderr)
    if not args.apply and result["ok"]:
        print("apply: pre-flight clean; pass --apply to actually move files")
    return 0 if result["ok"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yt-id", description=__doc__)
    parser.add_argument("--db", default=db.DEFAULT_DB_PATH, help="SQLite DB path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="index source folder by YouTube ID")
    p_scan.add_argument(
        "--source", default=".",
        help="folder to scan recursively (default: the current directory)",
    )
    p_scan.add_argument(
        "--worklist", default=worklist_mod.WORKLIST_FILE,
        help="path to the working-dir file of record (default: ytid.yaml)",
    )
    p_scan.set_defaults(func=_cmd_scan)

    p_unres = sub.add_parser(
        "unresolved", help="list tracked files needing manual attention"
    )
    p_unres.add_argument(
        "--all", action="store_true", help="list every tracked file, not just unresolved"
    )
    p_unres.add_argument(
        "--include-dash", action="store_true",
        help="also list lower-confidence dash-suffix matches for auditing",
    )
    p_unres.set_defaults(func=_cmd_unresolved)

    p_res = sub.add_parser("resolve", help="manually resolve a tracked file")
    target = p_res.add_mutually_exclusive_group(required=True)
    target.add_argument("--id", type=int, help="videos.id row identifier")
    target.add_argument("--path", help="exact src_path of the file")
    action = p_res.add_mutually_exclusive_group(required=True)
    action.add_argument("--youtube-id", dest="youtube_id", help="assign this 11-char ID")
    action.add_argument("--ignore", action="store_true", help="mark as ignored")
    p_res.set_defaults(func=_cmd_resolve)

    p_fetch = sub.add_parser("fetch", help="resolve metadata via yt-dlp (slow)")
    p_fetch.add_argument("--binary", default=DEFAULT_BINARY, help="yt-dlp binary")
    p_fetch.add_argument("--sleep", type=float, default=2.0, help="base delay between requests")
    p_fetch.add_argument("--jitter", type=float, default=1.0, help="random extra delay 0..J")
    p_fetch.add_argument("--limit", type=int, default=None, help="max videos this run")
    p_fetch.add_argument("--timeout", type=float, default=60.0, help="per-request timeout")
    p_fetch.add_argument("--retry-errors", action="store_true", help="also retry prior errors")
    p_fetch.add_argument(
        "--retry-unavailable", action="store_true",
        help="also retry deleted/blocked videos",
    )
    p_fetch.add_argument(
        "--worklist", default=worklist_mod.WORKLIST_FILE,
        help="path to the working-dir file of record (default: ytid.yaml)",
    )
    p_fetch.set_defaults(func=_cmd_fetch)

    p_classify = sub.add_parser("classify", help="decide artist/genre/action")
    p_classify.add_argument(
        "--config", default=None,
        help="config directory (overrides ./config, the user config dir, and "
             "the packaged defaults, resolved per file)",
    )
    p_classify.add_argument(
        "--require-genre", dest="require_genre", action="store_true",
        help="only move files that resolve to a genre; a confident artist with "
             "no genre stays in review (default: genre optional -- artist-only "
             "files move to /Artist)",
    )
    # Deprecated: genre is optional by default now, so this flag is a no-op kept
    # for backward compatibility with existing scripts.
    p_classify.add_argument(
        "--allow-missing-genre", dest="allow_missing_genre", action="store_true",
        help=argparse.SUPPRESS,
    )
    p_classify.add_argument(
        "--worklist", default=worklist_mod.WORKLIST_FILE,
        help="working-dir file of record applied before deciding (default: "
             "ytid.yaml); pass an empty string to ignore it",
    )
    p_classify.set_defaults(func=_cmd_classify)

    p_work = sub.add_parser(
        "worklist",
        help="regenerate ytid.yaml (the fill-in file of record) or list what's pending",
    )
    p_work.add_argument(
        "--worklist", default=worklist_mod.WORKLIST_FILE,
        help="path to write/update (default: ytid.yaml)",
    )
    p_work.add_argument(
        "--list", action="store_true",
        help="just print pending items to stdout instead of writing the file",
    )
    p_work.set_defaults(func=_cmd_worklist)

    p_config = sub.add_parser(
        "config", help="show which config files are in effect"
    )
    p_config.add_argument(
        "action", nargs="?", choices=["path"], default="path",
        help="what to show (default: path)",
    )
    p_config.add_argument(
        "--config", default=None,
        help="config directory to resolve against (same precedence as classify)",
    )
    p_config.set_defaults(func=_cmd_config)

    p_review = sub.add_parser(
        "review", help="list the manual-review bucket (classify decisions)"
    )
    p_review.add_argument(
        "--action", choices=["review", "skip", "move", "all"], default="review",
        help="which decisions to list (default: review)",
    )
    p_review.set_defaults(func=_cmd_review)

    p_plan = sub.add_parser("plan", help="build a dry-run move manifest")
    p_plan.add_argument("--target", required=True, help="target root, e.g. '/Music Videos'")
    p_plan.add_argument("--out", default="manifest", help="output prefix for .json/.csv")
    p_plan.add_argument(
        "--clean-names", dest="clean_names", choices=plan_mod.CLEAN_LEVELS, default=None,
        help="clean destination filenames: 'conservative' (remove only "
             "filesystem-illegal/control chars) or 'moderate' (also neutralize "
             "shell-hostile chars). Default: off (preserve original names).",
    )
    p_plan.add_argument(
        "--enhance-names", dest="enhance_names", action="store_true",
        help="prepend the known artist/title to each destination filename "
             "(Artist_Title_<rest-including-youtubeid>.ext), skipping any part "
             "already present. Injected text is always sanitized; combine with "
             "--clean-names to also scrub the original tail. Default: off.",
    )
    p_plan.add_argument(
        "--min-artist-files", dest="min_artist_files", type=int, default=1,
        metavar="N",
        help="minimum number of moved files an artist needs before an "
             "<Artist>/ folder is created. Artists below N are flattened one "
             "level up: files land in <target>/<genre>/ (or <target>/ when "
             "there is no genre). Default: 1 (always create the artist folder).",
    )
    p_plan.set_defaults(func=_cmd_plan)

    p_apply = sub.add_parser("apply", help="execute (or undo) a manifest")
    p_apply.add_argument("--manifest", required=True, help="manifest .json from plan")
    p_apply.add_argument("--apply", action="store_true", help="actually move files")
    p_apply.add_argument("--undo", action="store_true", help="reverse a prior apply")
    p_apply.add_argument(
        "--on-error", dest="on_error", choices=apply_mod.ON_ERROR_POLICIES,
        default="rollback",
        help="failure policy: rollback (default) reverses this run, stop halts, skip continues",
    )
    p_apply.set_defaults(func=_cmd_apply)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
