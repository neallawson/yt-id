# Known Issues / Backlog

Lightweight tracker for bugs and enhancements to address later.

## Open

### Auto-resolve ~90% of manual artist/title work by parsing saved filenames (HIGH — revisit next)
- **Reported:** 2026-09-14 (from a large real-world dataset run)
- **Severity:** high-value enhancement — targets the biggest remaining source of
  manual effort.
- **Observation (field notes):** On a large test set, ~300 entries landed in
  `ytid.yaml` because YouTube lookup couldn't resolve them and they needed manual
  intervention. Working through them by hand revealed:
  - In **~90%** of entries the parsed `artist`/`title` were already essentially
    correct — the only manual work was cosmetic: stripping extraneous punctuation
    down to a small allowed set (`-`, space, `(`, `)`, and a few more), and
    substituting `[]` -> `()` in titles. The *saved filename* alone was a
    reliable source.
  - In the remaining **~5–10%** the parse was genuinely wrong, and the failures
    had a recognizable shape:
    - one of `artist`/`title` is **blank** while the other holds the bulk of the
      string,
    - both fields hold the **same (or nearly the same)** value, or
    - one field contains **most of the original filename** string.
- **Upshot:** yt-id can likely solve 90%+ of artist/title strings purely from the
  filename, and — crucially — the *problem* cases are self-identifying. That means
  the bulk of the manual work this app currently REQUIRES can be automated: parse
  confidently, auto-accept clean parses, and route only the detectable-bad ones to
  review.
- **Why this isn't happening today:** `classify.decide()` only heuristically
  parses from the **fetched** `meta["title"]`/channel (see
  `ytid/classify.py::_split_title`, `_channel_artist`). When fetch returns nothing
  (unavailable/error) there is no title to parse, so these fall to review with
  empty guesses — even though the on-disk filename usually carries a clean
  `Artist - Title [id]` string.
- **Proposed direction (revisit ASAP):**
  1. **Filename as a first-class parse source.** Add a filename parser (reuse the
     ID-token stripping in `plan._split_id`/`_id_span` to isolate the human part,
     then split on the existing `_SEPARATORS`). Use it when structured/fetched
     fields are absent, not just the fetched title.
  2. **Normalize to the allowed charset.** Fold the manual cleanup into code:
     collapse to the allowed set, map `[]`->`()`, trim/quote — so a clean parse
     needs no hand editing.
  3. **Confidence + self-diagnosing guardrails.** Auto-accept a parse only when it
     passes sanity checks; force review when any "bad-parse signature" fires:
     - one field empty while the other is long,
     - `artist` ~= `title` (normalized equality / high similarity),
     - a field retains most of the original stem (length ratio near 1),
     - no separator found at all.
  4. **Surface, don't hide.** Keep flagged items in the existing worklist/review
     flow with the reason, so the human only sees the ~10% that actually need eyes.
- **Note:** This dovetails with the existing `--clean-names`/`enhance_filename`
  sanitizer (`ytid/plan.py`), which already knows the OS-safe/allowed-char rules —
  the parser's normalization step can share that machinery.

### User-supplied titles should be authoritative in `plan` — DONE 2026-09-22
- **Resolution:** every move is renamed `Artist - Title [id].ext` (original
  extension kept), whether the artist and title came from a lookup or from
  `ytid.yaml`. `--omit-artist-from-filename` drops the artist prefix only when
  an artist folder is created (`Title [id].ext`); a flattened file still keeps
  the artist in the name. A video override (`reason == "video override"` with a
  non-empty title) skips `--clean-names` and is only passed through
  `sanitize_component`. An override with an artist but no title stays in review.
  `--enhance-names` no longer changes the destination name.

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

### Make genre optional everywhere (default artist-only moves) — DONE 2026-09-13
- **Resolution:** genre is now optional by default. `decide()`/`classify_all()`
  default `allow_missing_genre=True`, and the per-video override branch honors it
  too (an override with an artist but no genre moves; genre stays `None` instead
  of being forced to `other`). The `--allow-missing-genre` flag is replaced by an
  inverse `--require-genre` (old flag kept as a hidden no-op). Low-confidence
  heuristic parses still go to review. Covered by tests in
  `tests/test_classify.py`.

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
