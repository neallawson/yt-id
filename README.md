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
| `classify`| Derive artist/title/genre + action; `review` lists it| fast, local  |
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
ytid review

# List a specific bucket, or everything
ytid review --action move
ytid review --action skip
ytid review --action all
```

Each entry shows the ID, decision, confidence, fetch status, derived artist,
filename, and the reason it landed in review — so you can curate
`config/overrides.yaml` (e.g. add an artist→genre mapping) and re-run
`classify` to promote items to `move`.

**Artist-only moves.** By default a `move` needs both a confident artist and a
genre. Pass `classify --allow-missing-genre` to also move files that have a
confident artist but no genre — these are filed directly under
`<target_root>/<Artist>/` (genre folder omitted). The confidence gate is
unchanged, so only reliably-identified artists are promoted; low-confidence
heuristic parses stay in `review`.

```bash
ytid classify --allow-missing-genre
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
ytid plan --target "/Music Videos" --clean-names conservative
ytid plan --target "/Music Videos" --clean-names moderate
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
ytid plan --target "/Music Videos" --enhance-names
ytid plan --target "/Music Videos" --enhance-names --clean-names moderate
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

**Planned: `ytid relink`.** For the mid-pipeline case (e.g. you move the source
tree before applying), a future command will re-point stored paths in bulk:

```bash
# planned, not yet implemented
ytid relink --from /old/source/root --to /new/source/root
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
