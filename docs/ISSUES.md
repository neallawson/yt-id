# Known Issues / Backlog

Lightweight tracker for bugs and enhancements to address later.

## Open

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

### Make genre optional everywhere (default artist-only moves)
- **Reported:** 2026-09-08
- **Severity:** low (enhancement; deferred by request)
- **Goal:** treat genre as optional throughout so a confident artist with no
  genre **moves** into `<target>/<Artist>/` by default, instead of landing in
  review. Genre probing remains a separate future feature.
- **Implementation notes (ready to pick up):**
  - `decide()` / `classify_all()` in `ytid/classify.py`: flip
    `allow_missing_genre` default to `True`.
  - CLI `ytid/cli.py`: replace the `--allow-missing-genre` flag with an inverse
    `--require-genre` (keep `--allow-missing-genre` as a hidden no-op for
    backward compat); pass `allow_missing_genre=not args.require_genre`.
  - **Also fix the per-video override branch** (`decide()` step 1): today it
    forces `genre = ov.genre or artist_genre or "other"` and reviews when
    `genre == "other"`. Change so an override with an artist but no genre
    **moves** (genre stays `None`) under the same `allow_missing_genre` rule,
    instead of being forced to `other`/review.
  - Tests to update in `tests/test_classify.py`:
    `test_confident_artist_no_genre_is_review_by_default` (new default is
    `move`), and keep `test_allow_missing_genre_*` behavior via the new
    `--require-genre` path. Low-confidence heuristic parses must still review.

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
