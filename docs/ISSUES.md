# Known Issues / Backlog

Lightweight tracker for bugs and enhancements to address later.

## Open

### Auto-fill artist/title from the filename — DONE 2026-10-02
- **Reported:** 2026-09-14 (from a large real-world dataset run)
- **Resolution:** `classify` and `yt-id worklist --sync` fill an entry whose
  `artist` and `title` are both still empty. A classify decision that already
  has either name wins. Otherwise the filename is parsed (`ytid/filename_parse.py`):
  the YouTube id is removed, then a spaced dash, bar, tilde, or fullwidth colon
  splits artist from title. A slash is not a split. Each side is then passed
  through `sanitize_component`, so `AC/DC` is stored as `AC-DC`.
  No separator means a title-only suggestion (artist blank), which is confident
  when the title is non-empty.
- A clean suggestion leaves `action` blank, so the next `classify` moves it.
  A bad split (one side empty, both sides the same or nearly the same, or one
  side almost the whole name) is still written, with `action: review`.
- The suggestion uses `sanitize_component`: square brackets become
  parentheses, curly quotes become `'`, unicode dashes become `-`, and illegal
  filename characters are rewritten the same way as a destination path.
  `(Live)`, `(Audio)`, and `(Lyric Video)` stay. `--strict-names` also leaves
  parentheses in place. `--raw-artist-title` on
  `classify` and `worklist --sync` skips that pass. An entry you have already
  filled in is left alone. `plan` still shapes the destination later.

### User-supplied titles should be authoritative in `plan` — DONE 2026-09-22
- **Resolution:** every move is renamed `Artist - Title [id].ext` (original
  extension kept), whether the artist and title came from a lookup or from
  `ytid.yaml`. `--omit-artist-from-filename` drops the artist prefix only when
  an artist folder is created (`Title [id].ext`); a flattened file still keeps
  the artist in the name. Supplied and parsed names go through the same
  shaper. `--strict-names` and `--spaces-to-underscores` are optional and
  apply to every folder and filename. An override with no title stays in review.

### No way to prune DB rows for files removed from disk
- **Reported:** 2026-09-07
- **Severity:** low (cosmetic; stale rows are inert downstream)
- **Symptom:** After `scan` flags a file as `duplicate` (or `unresolved`) and the
  file is later deleted from disk, its row remains in `videos`. `scan` is
  upsert-only (keyed by `src_path`) and never deletes rows for missing files, so
  the stale row keeps appearing in `yt-id unresolved`.
- **Impact:** Rows with `youtube_id = NULL` (e.g. duplicates) are skipped by
  `fetch`/`classify`/`plan`, so this is worklist clutter only, not a data-safety
  issue.
- **Workarounds today:** `yt-id resolve --id N --ignore`, or delete the DB and
  re-scan, or `sqlite3 ytid.db "DELETE FROM videos WHERE id=N;"`.
- **Proposed fix:** add a `yt-id prune` command, e.g.
  - drop rows whose `src_path` no longer exists on disk, and/or
  - clear `duplicate` rows explicitly.
  Keep it dry-run by default (print what would be removed; require `--apply`).

### Title required, artist optional — DONE 2026-09-22
- **Resolution:** a move requires a title. Artist and genre are optional unless
  `classify --require-artist` or `--require-genre` is set. An override with no
  title stays in review, even with `action: move`. A title with no artist is
  filed with no `<Artist>/` folder. Covered by tests in `tests/test_classify.py`
  and `tests/test_plan.py`.

### Make genre optional everywhere (default artist-only moves) — DONE 2026-09-13
- **Resolution:** genre is now optional by default. `decide()`/`classify_all()`
  default `allow_missing_genre=True`, and the per-video override branch honors it
  too (an override with an artist but no genre moves; genre stays `None` instead
  of being forced to `other`). The `--allow-missing-genre` flag is replaced by an
  inverse `--require-genre` (old flag kept as a hidden no-op). Low-confidence
  heuristic parses still go to review. Covered by tests in
  `tests/test_classify.py`. Superseded in part by "Title required, artist
  optional" above: an artist alone no longer moves.

### Per-folder genre taxonomy override in ytid.yaml
- **Reported:** 2026-09-07
- **Severity:** low (enhancement; not blocking)
- **Context:** The working-dir `ytid.yaml` is now the file of record for
  per-video/artist ground truth, but the genre *taxonomy* (`buckets` +
  `normalize`) still lives only in the global/packaged `genre_map.yaml`.
- **Proposed:** allow optional `buckets:`/`normalize:` sections inside
  `ytid.yaml` so a folder can override or extend the genre taxonomy locally,
  making `ytid.yaml` the single self-contained config for a working directory.
  Deferred by request; anticipated as a likely future ask.
