# yt-id

Identify the artist and genre for a folder of YouTube-sourced music videos and
organize them into a `/<genre>/<Artist>/` tree.

The stable key for every file is the 11-character YouTube ID embedded in the
filename (e.g. `... [8R5El2HWMIo].webm`). The filename itself is treated as a
hint only. Metadata is resolved from YouTube via `yt-dlp`, cached durably in
SQLite, and turned into a reviewed move manifest before anything is touched on
disk.

> **New here?** See the [Getting Started guide](docs/GETTING_STARTED.md) for
> installation, how the `yt-id` command is invoked, where files live, and a
> full end-to-end walkthrough.

## Design

The pipeline is split into independently re-runnable stages so the slow,
network-bound work is isolated from the fast, local work:

| Command   | What it does                                        | Speed        |
|-----------|-----------------------------------------------------|--------------|
| `scan`    | Track every video file; parse IDs; set resolve status| fast, local  |
| `fetch`   | Resolve uncached IDs via yt-dlp (resumable)         | slow, network|
| `classify`| Derive artist/title/genre + action; `review` lists it| fast, local  |
| `plan`    | Build a dry-run move manifest (CSV/JSON)            | fast, local  |
| `apply`   | Execute the manifest; `--undo` reverses it          | fast, local  |
| `export`  | Dump a denormalized audit ledger (CSV/JSON)         | fast, local  |
| `validate`| Verify the manifest was executed correctly (planned)| fast, local  |
| `config`  | Show which config files are in effect (`config path`)| fast, local  |

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
yt-id unresolved

# Also audit the lower-confidence dash-suffix matches
yt-id unresolved --include-dash

# List everything, regardless of status
yt-id unresolved --all

# Manually resolve by row id or exact path
yt-id resolve --id 42 --youtube-id dQw4w9WgXcQ
yt-id resolve --path "/videos/A Place With No Id.mkv" --youtube-id dQw4w9WgXcQ

# Track but never process a file
yt-id resolve --id 42 --ignore
```

Manual resolutions (`id_source = 'manual'`) and `ignored` rows are preserved
across rescans.

### The working-dir file of record (`ytid.yaml`)

For the common folder-centric workflow — `cd` into a folder and run everything
there (`--source` defaults to `.`) — everything the pipeline can't resolve on
its own is collected into a single **`ytid.yaml`** written next to `ytid.db`. It
is both an auto-generated worklist and the authoritative source of your manual
answers, so you edit one file instead of juggling `resolve` and `overrides.yaml`:

- **`videos:`** — keyed by YouTube ID; a fetch that came back `unavailable`/
  `error`, or a decision that landed in `review`. Supply `artist`/`title`
  (`genre` optional, `action` = move|review|skip).
- **`unidentified:`** — files with **no** detectable ID; the full filename is
  shown and you add the `youtube_id` (plus metadata). `classify` then promotes
  the row to `resolved`.
- **`artists:`** — optional `artist → genre` shortcuts.

`scan` and `fetch` refresh it **non-destructively** (your edits are preserved,
new problems appended). Regenerate or inspect it anytime:

```bash
yt-id worklist          # (re)write ytid.yaml with anything still pending
yt-id worklist --list   # print pending items without writing
```

`--list` reports each entry with its **line number** in `ytid.yaml` and a
progress summary, and takes filters so a large file stays scannable:

```bash
yt-id worklist --list --missing-genre          # entries lacking a genre
yt-id worklist --list --missing-artist --missing-title  # OR-combined
yt-id worklist --list --action review          # entries that will land in review
yt-id worklist --list --action blank           # entries whose action field is empty
yt-id worklist --list --missing-genre --compact # one tab-separated line each
```

- **`--missing-artist` / `--missing-title` / `--missing-genre`** keep only
  entries with that field empty; combining them is an **OR** (missing *any*).
- **`--action {move,review,skip,blank,all}`** — `move`/`review`/`skip` match the
  *resolved* decision (what will actually happen); `blank` matches an empty
  `action` field in the file; `all` (default) applies no action filter.
- **`--compact`** prints `LINE⇥action⇥id⇥filename`, handy for
  `$EDITOR +LINE ytid.yaml` and piping.

Refreshes are **append-only**: new problems are added and your existing entries
are never rewritten or removed, so anything you've already answered stays put.

`classify` applies `ytid.yaml` first — assigning any supplied IDs and layering
its per-video/artist overrides on top of `overrides.yaml` (the working-dir file
wins), so a manually-supplied `title` flows straight through to `plan`.

### Review bucket (manual handling)

`classify` assigns every tracked video an `action` of `move`, `review`, or
`skip`, but it **never auto-skips**: anything it cannot confidently route to a
genre folder — non-music videos, unavailable/unfetched IDs, unknown artists — is
sent to **`review`**, not dropped. `skip` only ever happens when *you* set it
explicitly in `config/overrides.yaml`. Every file therefore stays tracked in the
`decisions` table.

Like the ID worklist, the review bucket is just a query:

```bash
# List everything needing manual handling (action = 'review')
yt-id review

# List a specific bucket, or everything
yt-id review --action move
yt-id review --action skip
yt-id review --action all
```

Each entry shows the ID, decision, confidence, fetch status, derived artist,
filename, and the reason it landed in review — so you can curate
`config/overrides.yaml` (e.g. add an artist→genre mapping) and re-run
`classify` to promote items to `move`.

**Artist-only moves.** Genre is optional: a `move` needs a confident artist, and
a file with no genre is filed directly under `<target_root>/<Artist>/` (genre
folder omitted). The confidence gate is unchanged, so only reliably-identified
artists are promoted; low-confidence heuristic parses stay in `review`. Pass
`classify --require-genre` for the stricter behavior where a resolved genre is
mandatory before moving.

```bash
yt-id classify                 # genre optional (default)
yt-id classify --require-genre # only move when a genre resolves
```

### Configuration

Two YAML files drive classification: `genre_map.yaml` (the coarse genre folders
and a fine→coarse `normalize` map) and `overrides.yaml` (your ground-truth
`artists:` map plus per-video decisions under `videos:`, each accepting
`artist`, `title`, `genre`, and `action`). Sensible defaults ship **inside the
package**, so the tool works out of the box after install. For a single working
folder, prefer editing `ytid.yaml` (above), which layers on top of these.

Each file is resolved **independently**, first match wins:

1. an explicit `--config DIR` (on `classify`)
2. `./config` in the current directory (handy while developing)
3. your user config dir: `$XDG_CONFIG_HOME/ytid`, else `~/.config/ytid`
4. the packaged defaults bundled in the install (always present)

To customize without touching the install, drop your own `genre_map.yaml` /
`overrides.yaml` into `~/.config/ytid/`. To see exactly which files are active
(and everything that was searched), use the `config` command — a `*` marks the
file in effect:

```bash
yt-id config path                 # resolve against the default search path
yt-id config path --config ./cfg  # preview a specific directory
```

### Validated, transactional `apply` (implemented)

> Status: **implemented.** `apply` pre-flights the whole manifest, journals
> intent write-ahead, verifies each move as it completes, and defaults to
> rolling back the entire run on the first failure. Select the policy with
> `--on-error {rollback,stop,skip}` (default `rollback`). Cross-device moves are
> still rejected at pre-flight (see the planned update below).

**Decision:** validation happens *inline, per move, as each step completes* —
not as a separate after-the-fact pass. After-the-fact validation only reassures
you when everything already worked; when something fails mid-run it reports
damage too late, with the tree already in a mixed state. Instead, a failure must
**stop the action immediately**, and by default **roll back** everything this run
did so the tree is returned to exactly how it started.

The standalone `validate` stage is **not** dropped, but **demoted** to a
secondary, independent audit (see the end of this section).

#### 1. Pre-flight validation (before touching anything)

Walk the entire manifest first and hard-fail *before* the first mutation if any
of these hold. Most failures are structural and caught here, so a doomed run
rarely begins:

- a **source path is missing**, or a **destination already exists** (a collision
  that should have been resolved at plan time),
- the **destination volume differs from the source** (see atomicity below),
- **insufficient free space** on the target volume,
- **duplicate `to_path`s** within the manifest itself.

#### 2. Per-move verification (validate each step)

For each move: attempt it, then immediately assert the invariant before
considering it done:

- the **source path no longer exists**, AND
- the **destination path exists**,
- (optionally) the **size matches** — later, an optional checksum.

#### 3. Atomicity of a single move

- **Same filesystem (current scope):** use `os.rename`, which is atomic and
  instantaneous. Cross-device moves are treated as a **pre-flight error** for
  now.
- **Cross-device (planned update):** copy to a temporary name → verify
  size/checksum → atomically rename into place → only then delete the source.
  The source is removed only once the destination is proven complete. Detected
  and routed via the pre-flight volume check.

#### 4. Journal intent *before* the move (write-ahead)

The `moves` row must be written **before** the move, not after, so a crash is
always discoverable and reversible:

1. insert a `pending` moves row (intent recorded),
2. perform the move,
3. verify the invariant,
4. mark the row `done`.

A crash between steps leaves an in-flight `pending` record that can be
reconciled or rolled back. (This requires adding a status column to the `moves`
table.)

#### 5. Failure policy (default: `rollback`)

Exposed as a policy because a durable, write-ahead `moves` log makes true
transaction semantics possible:

- **`rollback`** *(default)* — on the first failure, halt **and reverse every
  move made in this run**, returning the tree to its starting state. Either the
  whole run lands cleanly or nothing changes.
- **`stop`** — halt at once, exit non-zero, leaving already-completed moves in
  place and recorded (resume later).
- **`skip`** — best-effort: collect errors and continue (today's behavior, but
  opt-in only).

Any run that hits a hard failure exits non-zero.

#### 6. State classification (benign vs. fatal, for idempotent resume)

Verification distinguishes benign states from fatal ones so re-running is safe:

- source **missing** + destination **exists** → **already applied** → treated as
  success (idempotent / resumable),
- source **exists** + destination **exists** → **unexpected collision** → fatal,
- move raised, or destination **missing**, or **size mismatch** → partial/corrupt
  → **fatal, triggers the failure policy**.

#### Demoted: standalone `validate` as an independent audit

`validate` remains useful as a *secondary* safety net, decoupled from the run
that produced the moves:

- **Simple** — compare expected vs. actual file counts per target folder against
  the `move` rows.
- **Detailed** — for every `done` row in the `moves` table, re-assert
  source-gone + destination-exists (+ optional size/checksum), to catch **drift**
  when other tools touch the tree, or to audit an old `apply` long after the
  fact.
- Emits a report (CSV/JSON) and a non-zero exit code on any hard failure, so it
  is CI/cron friendly. It reads ground truth from the `moves` table and never
  acts blindly — ambiguous findings go to a review list.

In short: **inline verification + rollback is the guarantee; `validate` is the
after-the-fact audit.**

### Filename cleaning (`plan --clean-names`)

Source filenames often carry characters that trip other software. `plan` can
tidy the *destination* filename while staying as close to the original as
possible. It is **off by default** — omit the flag and names are preserved
verbatim.

```bash
yt-id plan --target "/Music Videos" --clean-names conservative
yt-id plan --target "/Music Videos" --clean-names moderate
```

Both levels share the same rules:

- The **YouTube-ID token** (`[id]` or trailing `-id`) and the **file extension**
  are preserved verbatim.
- **Whitespace runs** collapse to a single `_`.
- **Removed characters** collapse to nothing, except a single `-` seam is
  inserted when removal would otherwise concatenate two kept characters
  (`AC/DC` → `AC-DC`, but a trailing `What?` → `What`).
- Repeated separators collapse; leading/trailing separators are trimmed.
- OS-safety tail: trailing dots/spaces stripped, reserved names (`CON`, `NUL`,
  …) prefixed with `_`, stem capped at 200 chars. Empty results fall back to the
  YouTube ID (or `Unknown`). The transform is **idempotent**.

Levels differ only in *which* characters are treated as bad:

| Level          | Removes                                                        |
|----------------|---------------------------------------------------------------|
| `conservative` | Filesystem-illegal + control chars: `< > : " / \ \| ? *`      |
| `moderate`     | The above **plus** shell-hostile chars: `' " \` ; $ ( ) { } ! # @ ~ %` and commas (ASCII `,`, fullwidth `，`, ideographic `、`) |

Under `moderate`, `&` is a special case: instead of being dropped it is
rewritten to `_and_` to keep the meaning (so `A & B` and `A&B` both become
`A_and_B`). Note this is a blunt substitution — `R&B` becomes `R_and_B`.
`conservative` leaves a literal `&` untouched.

Example (`moderate`): `Song (Live) & More [abcdefghijk].mp4` →
`Song_Live_and_More_[abcdefghijk].mp4`.

### Filename enhancing (`plan --enhance-names`)

Some source files carry no identifying text at all — the only clue is the
YouTube ID. Since `classify` has already resolved the **artist** and **title**,
`plan --enhance-names` can fold them back into the destination filename:

```bash
yt-id plan --target "/Music Videos" --enhance-names
yt-id plan --target "/Music Videos" --enhance-names --clean-names moderate
```

Shape produced:

```
Artist_Title_<original-rest-including-youtubeid>.ext
# ID-only source:
[dQw4w9WgXcQ].mp4          ->  Nazz_Open_My_Eyes_[dQw4w9WgXcQ].mp4
just-abcdefghijk.webm     ->  Nazz_Open_My_Eyes_just-abcdefghijk.webm
```

Behavior:

- **Always prepends** the known artist/title to every moved file, but with a
  **duplication guard**: each fragment is skipped when it is already present in
  the name (tolerant, case-insensitive match), so `Open My Eyes [id].mp4`
  becomes `Nazz_Open My Eyes [id].mp4`, not a doubled title. If both are already
  present, the name is left unchanged.
- **Partial data** is fine: whichever of artist/title is known gets added.
- The **YouTube-ID token and extension** are always preserved.
- Injected artist/title text is **always sanitized** (OS-safe) regardless of
  `--clean-names`. Combine with `--clean-names` to also scrub the original tail;
  on its own, `--enhance-names` leaves the existing tail untouched.
- The transform is **idempotent** — re-running does not stack prefixes.

### Sparse-artist flattening (`plan --min-artist-files`)

By default every moved file is filed under `<target>/<genre>/<Artist>/`. When an
artist has only a handful of videos, that folder can feel like clutter. Pass
`--min-artist-files N` to require at least **N** moved files for an artist before
its `<Artist>/` folder is created:

```bash
yt-id plan --target "/Music Videos" --min-artist-files 2
```

- The count is the number of **`move`-action files for that artist in this
  plan** (review/skip items don't count).
- Artists **below** the threshold are flattened **one level up** — only the
  artist folder is dropped, genre grouping is kept: `<target>/<genre>/<file>`
  (or `<target>/<file>` when there is no genre, which is the default for a
  confident artist-only file).
- Artists **at or above** the threshold are unchanged
  (`<target>/<genre>/<Artist>/<file>`).
- The default is `1`, which always creates the artist folder (original
  behavior). Same-name collisions from flattening are handled by the existing
  YouTube-ID suffix rule.

### Audit ledger (`export`)

Once a batch is applied, `export` dumps a **denormalized ledger** — one row per
tracked file, joining `videos` + `decisions` + the latest `moves` row — so the
artifact you archive next to the moved videos is a human-readable master record
that stands on its own without the SQLite DB:

```bash
yt-id export                          # writes ledger.json and ledger.csv
yt-id export --format csv --out audit # just audit.csv
yt-id export --format json            # ledger.json only (with a meta header)
```

Each row reads as a story: `youtube_id`, `original_filename`, `src_path`,
`id_source`, `resolve_status`, `fetch_status`, `ytdlp_version`, `artist`,
`title`, `genre`, `action`, `confidence`, `reason`, `target_path`, `moved_to`,
`move_status`, `applied_at`, `undone_at`.

- **Complete account.** Unresolved and unmoved files are included (via left
  joins), so the ledger reflects the whole corpus, not just the successes.
- **Current state.** The latest `moves` row wins, so a move later undone reports
  `rolled_back`.
- **CSV vs JSON.** CSV is a flat, spreadsheet-friendly table of the rows; JSON
  carries a self-describing `meta` header (generation time, tool/yt-dlp
  versions, counts) alongside the rows.

This is a **view** of the DB for auditing — not a restore format. A durable
archive pairs it with the raw `ytid.db` (queryable truth) and `ytid.yaml` (your
re-appliable manual layer):

```bash
tar czf archive/2026-05-01-batch.tar.gz ytid.db ytid.yaml ledger.json ledger.csv
```

### Path storage & relocation (planned)

Today paths are stored **as given**: the DB records whatever `--source` you pass
(absolute or relative) and, after a move, the file's new absolute location under
`--target`. The DB itself defaults to `ytid.db` in the current directory. For
robustness, prefer an **absolute** `--source` so stored paths don't depend on
the working directory of later stages.

**Why in-DB path portability is deferred.** The deliverable of this tool is the
*organized tree*, not a self-describing bundle. Once `apply` completes and the
moves are validated, the target is an ordinary directory tree you can `mv`,
`rsync`, or `ssh` anywhere — no database required to relocate it. In-DB
relative/anchored paths would only help while a DB is still *mid-pipeline*
(scanned but not yet applied), which is the lower-value window. So it is
intentionally postponed.

**Planned: `yt-id relink`.** For the mid-pipeline case (e.g. you move the source
tree before applying), a future command will re-point stored paths in bulk:

```bash
# planned, not yet implemented
yt-id relink --from /old/source/root --to /new/source/root
```

This would update stored paths against recorded source/target roots, so an
interrupted, half-organized job can survive a relocation without a rescan.

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
yt-id scan --source "/path/to/videos"

# 2. Resolve metadata (slow; safe to Ctrl-C and resume)
yt-id fetch --sleep 2 --jitter 1

# 3. Classify using structured fields + overrides.yaml
#    (see which config files are active with `yt-id config path`)
yt-id classify

# 4. Produce a dry-run manifest (writes CSV + JSON, moves nothing)
yt-id plan --target "/Music Videos" --out manifest

# 5. Review manifest.csv, curate config/overrides.yaml, re-run classify/plan.
#    When happy, apply:
yt-id apply --manifest manifest.json

# Undo the last applied manifest:
yt-id apply --manifest manifest.json --undo
```

MusicBrainz-based genre suggestions are deferred to v2. In v1, genre comes from
`config/overrides.yaml` (artist map) plus any structured `genre` field yt-dlp
returns; everything else is routed to the review queue.
