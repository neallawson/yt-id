# ytid

Identify the artist and genre for a folder of YouTube-sourced music videos and
organize them into a `/<genre>/<Artist>/` tree.

The stable key for every file is the 11-character YouTube ID embedded in the
filename (e.g. `... [8R5El2HWMIo].webm`). The filename itself is treated as a
hint only. Metadata is resolved from YouTube via `yt-dlp`, cached durably in
SQLite, and turned into a reviewed move manifest before anything is touched on
disk.

## Design

The pipeline is split into independently re-runnable stages so the slow,
network-bound work is isolated from the fast, local work:

| Command   | What it does                                        | Speed        |
|-----------|-----------------------------------------------------|--------------|
| `scan`    | Track every video file; parse IDs; set resolve status| fast, local  |
| `fetch`   | Resolve uncached IDs via yt-dlp (resumable)         | slow, network|
| `classify`| Derive artist/title/genre from cache + overrides    | fast, local  |
| `plan`    | Build a dry-run move manifest (CSV/JSON)            | fast, local  |
| `apply`   | Execute the manifest; `--undo` reverses it          | fast, local  |
| `validate`| Verify the manifest was executed correctly (planned)| fast, local  |

State lives in SQLite (`ytid.db` by default): `videos`, `artists`,
`decisions`, and `moves` tables. Nothing is re-fetched or re-guessed
unnecessarily, so runs are idempotent and safe to interrupt.

### Tracking & ID resolution

`scan` records **every** video file as a row (keyed by `src_path`), whether or
not an ID can be parsed. Two real-world filename conventions are recognized,
both using the 11-char YouTube alphabet `[A-Za-z0-9_-]`:

- **bracket** — `... [w5jwxrTqoEA].webm`
- **dash** — `About the World-kxNehhCWIrs.webm` (yt-dlp's default
  `%(title)s-%(id)s.%(ext)s`). Because IDs may contain hyphens, the dash form is
  matched by anchoring to the end of the stem and taking exactly 11 valid chars.

Each row carries a `resolve_status`, so the worklist of files needing attention
is just a query rather than a CSV to shuffle:

- `resolved` — a usable, unique `youtube_id` was assigned (only these advance to
  `fetch`/`classify`)
- `unresolved` — no ID could be parsed; needs manual attention
- `duplicate` — the parsed ID is already claimed by another tracked file
- `ignored` — you opted the file out; tracked but never fetched/moved

Bracket matches are high-confidence; dash matches are recorded with
`id_source = 'dash'` so they can be audited separately.

```bash
# List files needing attention (unresolved + duplicate)
ytid unresolved

# Also audit the lower-confidence dash-suffix matches
ytid unresolved --include-dash

# List everything, regardless of status
ytid unresolved --all

# Manually resolve by row id or exact path
ytid resolve --id 42 --youtube-id dQw4w9WgXcQ
ytid resolve --path "/videos/A Place With No Id.mkv" --youtube-id dQw4w9WgXcQ

# Track but never process a file
ytid resolve --id 42 --ignore
```

Manual resolutions (`id_source = 'manual'`) and `ignored` rows are preserved
across rescans.

### Planned: `validate` (not yet implemented — high priority)

A sixth stage that confirms an `apply` actually did what the manifest said.
Post-move integrity is just as crucial as the moves themselves: a move that
half-succeeds, collides, or leaves a file behind must be detected, not assumed.

Scope, from simple to detailed:

- **Simple** — compare expected vs. actual file counts per target folder against
  the manifest's `move` rows.
- **Detailed (preferred)** — for every applied change recorded in the `moves`
  table, assert both sides of the move:
  - the **source path no longer exists** (file was removed from its origin), and
  - the **destination path exists** (file landed in `/<genre>/<Artist>/`).
  Optionally verify size (and later, a checksum) matches to catch truncated or
  partial copies.

Error handling is a first-class requirement, not an afterthought:

- Classify each discrepancy: `missing_destination`, `source_still_present`,
  `both_present` (copy-not-move), `neither_present` (lost file),
  `size_mismatch`, `unexpected_extra`.
- Emit a validation report (CSV/JSON, mirroring the manifest) and a non-zero
  exit code when any hard failure is found, so it is CI/cron friendly.
- Offer a `--repair` / reconcile path where safe (e.g. re-run a specific move),
  and route anything ambiguous to a review list rather than acting blindly.
- Read ground truth from the `moves` table so `validate` can run standalone,
  long after the `apply` that produced it.

## Requirements

- Python >= 3.10
- `yt-dlp` installed **independently** so it can be updated frequently
  (YouTube changes often break older versions). Recommended:

  ```bash
  pipx install yt-dlp        # then: pipx upgrade yt-dlp
  # or the standalone binary with self-update: yt-dlp -U
  ```

The yt-dlp version used for each fetch is recorded in the DB so failures can be
selectively re-fetched after an update.

## Install

```bash
pip install -e .
```

## Usage

```bash
# 1. Index the source folder (safe, local)
ytid scan --source "/path/to/videos"

# 2. Resolve metadata (slow; safe to Ctrl-C and resume)
ytid fetch --sleep 2 --jitter 1

# 3. Classify using structured fields + config/overrides.yaml
ytid classify

# 4. Produce a dry-run manifest (writes CSV + JSON, moves nothing)
ytid plan --target "/Music Videos" --out manifest

# 5. Review manifest.csv, curate config/overrides.yaml, re-run classify/plan.
#    When happy, apply:
ytid apply --manifest manifest.json

# Undo the last applied manifest:
ytid apply --manifest manifest.json --undo
```

MusicBrainz-based genre suggestions are deferred to v2. In v1, genre comes from
`config/overrides.yaml` (artist map) plus any structured `genre` field yt-dlp
returns; everything else is routed to the review queue.
