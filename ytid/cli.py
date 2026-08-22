"""Command-line entry point wiring the five pipeline stages together.

    ytid scan     --source DIR
    ytid fetch    [--sleep S --jitter J --limit N --retry-errors ...]
    ytid classify
    ytid plan     --target DIR [--out PREFIX]
    ytid apply    --manifest FILE [--undo] [--apply]

Everything defaults to safe/dry-run behavior; `apply` requires an explicit
--apply flag to actually move files.
"""

from __future__ import annotations

import argparse
import sys

from . import apply as apply_mod
from . import classify as classify_mod
from . import db
from . import fetch as fetch_mod
from . import plan as plan_mod
from . import resolve as resolve_mod
from . import scan as scan_mod
from .ytdlp_client import DEFAULT_BINARY, YtDlpNotFound, get_version


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
    need = sum(tally.get(s, 0) for s in resolve_mod.NEEDS_ATTENTION)
    if need:
        print(f"scan: {need} file(s) need manual attention -> run `ytid unresolved`")
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
    return 0


def _cmd_classify(args) -> int:
    counts = classify_mod.classify_all(
        db_path=args.db, config_dir=args.config,
        allow_missing_genre=args.allow_missing_genre,
    )
    print(
        f"classify: total={counts['total']} move={counts.get('move', 0)} "
        f"review={counts.get('review', 0)} skip={counts.get('skip', 0)}"
    )
    return 0


def _cmd_review(args) -> int:
    rows = classify_mod.list_decisions(db_path=args.db, action=args.action)
    if not rows:
        print(f"review: no decisions with action={args.action}")
        return 0
    for r in rows:
        artist = r["artist"] or "-"
        print(
            f"{r['youtube_id']}  [{r['action']}] conf={r['confidence']:.2f} "
            f"fetch={r['fetch_status']}  {artist}"
        )
        print(f"    {r['filename']}")
        print(f"    reason: {r['reason']}")
    print(f"review: {len(rows)} item(s) with action={args.action}")
    return 0


def _cmd_plan(args) -> int:
    planned = plan_mod.build_plan(
        args.target, db_path=args.db, clean_names=args.clean_names
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
    parser = argparse.ArgumentParser(prog="ytid", description=__doc__)
    parser.add_argument("--db", default=db.DEFAULT_DB_PATH, help="SQLite DB path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="index source folder by YouTube ID")
    p_scan.add_argument("--source", required=True, help="folder to scan recursively")
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
    p_fetch.set_defaults(func=_cmd_fetch)

    p_classify = sub.add_parser("classify", help="decide artist/genre/action")
    p_classify.add_argument("--config", default="config", help="config directory")
    p_classify.add_argument(
        "--allow-missing-genre", dest="allow_missing_genre", action="store_true",
        help="move confident artist-only files into /Artist (no genre folder)",
    )
    p_classify.set_defaults(func=_cmd_classify)

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
