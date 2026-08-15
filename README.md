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
| `scan`    | Walk the source tree, extract IDs, upsert to DB     | fast, local  |
| `fetch`   | Resolve uncached IDs via yt-dlp (resumable)         | slow, network|
| `classify`| Derive artist/title/genre from cache + overrides    | fast, local  |
| `plan`    | Build a dry-run move manifest (CSV/JSON)            | fast, local  |
| `apply`   | Execute the manifest; `--undo` reverses it          | fast, local  |

State lives in SQLite (`ytid.db` by default): `videos`, `artists`,
`decisions`, and `moves` tables. Nothing is re-fetched or re-guessed
unnecessarily, so runs are idempotent and safe to interrupt.

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
