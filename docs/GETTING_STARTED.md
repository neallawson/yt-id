# Getting Started with `yt-id`

`yt-id` identifies the artist/genre of a folder full of YouTube-sourced music
videos (using each file's embedded 11-char YouTube ID) and organizes them into
`<target>/<genre>/<Artist>/` folders. It never moves anything until you
explicitly tell it to.

This guide covers how the tool is invoked, where it keeps its files, how the
config works, and the end-to-end workflow.

---

## 1. Prerequisites

- **Python 3.10+**
- **`yt-dlp` on your PATH** — used to fetch video metadata. It is intentionally
  a *separate* program (not a bundled library) so you can update it often, since
  YouTube changes break older versions frequently.
  ```bash
  pipx install yt-dlp        # recommended
  # or: python -m pip install --user yt-dlp
  yt-dlp --version           # confirm it's on PATH
  ```

---

## 2. How the `yt-id` command actually works

`yt-id` is a Python package with a **console-script entry point** declared in
`pyproject.toml`:

```toml
[project.scripts]
yt-id = "ytid.cli:main"
```

When the package is installed, pip generates a small launcher named `yt-id` in
the environment's `bin/` directory. Running `yt-id scan ...` just runs that
launcher, which calls `ytid.cli:main`.

> **Why you can't just add the source folder to PATH:** `PATH` finds
> *executables*, but the source folder only has Python *modules*. And
> `ytid/cli.py` uses package-relative imports (`from . import db`), so it can't
> be run as a standalone script. The `yt-id` command only exists once the package
> is installed (or via `python -m ytid.cli`).

### Ways to run it

Pick whichever fits how often you use the tool:

| Approach | Command | Notes |
|----------|---------|-------|
| **Dev / editable venv** | `python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"` | Edits to the source take effect immediately. `yt-id` works while the venv is active. |
| **No install** | `python -m ytid.cli scan --source ...` | Run from the repo root. |
| **Global, isolated (recommended)** | `pipx install /path/to/yt-id` | Puts a `yt-id` symlink on your PATH (`~/.local/bin`) in its own env. Add `-e` for editable. |
| **From Git** | `pipx install git+https://github.com/jneallawson/yt-id.git` | No local checkout needed. See §7. |

If you already have a `.venv` with an editable install, the launcher lives at
`.venv/bin/yt-id`. To use `yt-id` without activating the venv, add that `bin`
directory to your PATH (not the source folder):

```bash
export PATH="/path/to/yt-id/.venv/bin:$PATH"
```

---

## 3. Where files live

`yt-id` reads and writes a few things. Knowing where they land avoids surprises.

### The database (`ytid.db`)
All state — scanned files, fetched metadata, and classification decisions —
lives in a single SQLite file. **By default it is created as `ytid.db` in your
current working directory.**

- Because the default is CWD-relative, **run every command from the same
  directory**, or always pass an absolute path with `--db`:
  ```bash
  yt-id --db ~/ytid/library.db scan --source ~/Videos
  ```

The intended workflow is folder-centric: `cd` into the folder that holds the
videos you want to organize and run everything from there. `--source` defaults
to `.` (the current directory), so a bare `yt-id scan` indexes the folder you
are standing in, and both `ytid.db` and `ytid.yaml` (below) are written there.

### The file of record (`ytid.yaml`)
Anything the pipeline can't resolve on its own is collected into a single
working-directory file, **`ytid.yaml`**, created next to `ytid.db`. It is both
an auto-generated worklist and the authoritative source of your manual answers:

- **`videos:`** — a known YouTube ID whose metadata came back empty
  (`unavailable`/`error`) or that classify sent to review. Fill in `artist` and
  `title` (`genre` optional; `action` = move|review|skip).
- **`unidentified:`** — a file with *no* detectable ID. The full filename is
  shown; you supply `youtube_id` plus metadata, and `classify` promotes the row
  to resolved.
- **`artists:`** — optional `artist -> genre` shortcuts applied to all their
  videos.

`scan` and `fetch` refresh this file automatically (non-destructively: your
edits are preserved, new problems are appended). Regenerate or inspect it
anytime:

```bash
yt-id worklist          # (re)write ytid.yaml with anything still pending
yt-id worklist --list   # just print pending items (with line numbers), don't write
```

`--list` prints each entry with its line number plus a progress summary, and
accepts filters so a big file stays manageable:

```bash
yt-id worklist --list --missing-genre           # only entries lacking a genre
yt-id worklist --list --missing-artist --missing-title  # OR: missing either
yt-id worklist --list --action review            # what will land in review
yt-id worklist --list --action blank             # empty action field in the file
yt-id worklist --list --missing-genre --compact  # LINE<tab>action<tab>id<tab>file
```

`--compact` output is ideal for jumping straight to a line
(`$EDITOR +LINE ytid.yaml`) or piping to other tools. Refreshes are
**append-only** — new problems are added and your existing answers are never
rewritten or removed.

After filling in the blanks, run `yt-id classify` — it applies `ytid.yaml`
before deciding, so your answers (including titles) win over any guess.

### Config files (`genre_map.yaml`, `overrides.yaml`)
Two YAML files drive classification. Each is resolved **independently**, with
the first match winning (per file):

1. an explicit `--config DIR`
2. `./config` in the current directory (handy while developing)
3. your user config dir: `$XDG_CONFIG_HOME/ytid`, else `~/.config/ytid`
4. the **defaults bundled inside the installed package** (always present)

This means the tool works out of the box (packaged defaults), while letting you
override settings without touching the installation. To see exactly which files
are active on your system (and everything that was searched), run:

```bash
yt-id config path              # add --config DIR to preview a specific directory
```
The `*` marks the file in effect for each of `genre_map.yaml` and
`overrides.yaml`.

- **`genre_map.yaml`** — the coarse genre folders (`buckets`) you want on disk,
  plus a `normalize` map from fine-grained genre tags to those buckets.
- **`overrides.yaml`** — your ground truth: `artist -> genre` mappings and
  per-video decisions keyed by YouTube ID. Anything here wins over automated
  guesses and persists across runs.

To customize, copy the packaged defaults into `~/.config/ytid/` and edit:

```bash
mkdir -p ~/.config/ytid
# copy the two files from the repo's ./config as a starting point
cp config/genre_map.yaml config/overrides.yaml ~/.config/ytid/
```

### The move manifest (`manifest.json` / `manifest.csv`)
`yt-id plan` writes a dry-run manifest describing every intended move. By default
it writes `manifest.json` and `manifest.csv` in the current directory; change
the prefix with `--out`:

```bash
yt-id plan --target "/Music Videos" --out /tmp/ytid-manifest
```

---

## 4. The workflow

Everything is safe/dry-run by default. Only `apply --apply` moves files.

```
scan → fetch → classify → (fix ytid.yaml) → classify → plan → apply
                   ↑___________________________|
```

`scan`, `fetch`, **and `classify`** all refresh `ytid.yaml`. The big bucket —
low-confidence files that classify couldn't confidently place — only exists
*after* `classify`, so it's `classify` that lists them (pre-filled with its best
guess). The normal loop is therefore: `classify`, open `ytid.yaml`, correct the
guesses / add genres, `classify` again.

### Step 1 — `scan`: index your source folder
Walks a folder recursively and records each file, extracting its YouTube ID.
`--source` defaults to `.`, so from inside the folder you're organizing just run:
```bash
yt-id scan                        # index the current directory
yt-id scan --source ~/Videos/YouTube   # or point somewhere else
```
Files whose ID can't be confidently detected are added to `ytid.yaml` under
`unidentified:` for you to complete (see Step 2a).

### Step 2 — `fetch`: get metadata via yt-dlp (slow, network)
Politely rate-limited; results are cached in the DB so you only fetch once.
```bash
yt-id fetch                       # fetch everything pending
yt-id fetch --limit 20            # do a batch at a time
yt-id fetch --retry-errors        # retry previous transient failures
```
Videos that come back `unavailable`/`error` are added to `ytid.yaml` under
`videos:` so you can supply their details.

### Step 2a — fix `ytid.yaml`: confirm/correct what the system guessed
Open `ytid.yaml` (auto-created in the working dir). Entries added by `classify`
come **pre-filled with its best-guess** `artist`/`title` — you only correct what's
wrong and add a `genre` (YouTube rarely provides one). A completed `videos:`
entry wins unconditionally and moves:
```yaml
videos:
  8R5El2HWMIo:
    file: "Atomic Rooster - The Devils Answer [8R5El2HWMIo].webm"
    artist: Atomic Rooster
    title: The Devils Answer
    genre: rock          # optional
    action: move         # optional: move|review|skip
                         # a title alone is enough to move; artist is optional
```
For a file with no detectable ID, add the id yourself (the full filename is
shown so you know which one it is):
```yaml
unidentified:
- file: "some ambiguous name.webm"
  youtube_id: dQw4w9WgXcQ
  artist: Rick Astley
  title: Never Gonna Give You Up
  genre: pop
```
Regenerate or review the list anytime with `yt-id worklist` /
`yt-id worklist --list`. The next `classify` applies everything here first.

Still need the low-level id fixer? `yt-id unresolved` / `yt-id resolve --id N
--youtube-id …` remain available for one-off DB edits.

### Step 3 — `classify`: decide artist / genre / action
Applies your config + heuristics and stores a decision per video.
```bash
yt-id classify
yt-id classify --require-artist        # also require an artist
yt-id classify --require-genre         # also require a genre
yt-id classify --config ~/my-config    # use a specific config dir
```
Each video gets an action: **move** (confident), **review** (needs a human), or
**skip**.

### Step 3a — `review`: inspect the buckets (optional)
```bash
yt-id review                 # the review queue (default)
yt-id review --action move   # what will be moved
yt-id review --action skip
```
Curate `ytid.yaml` (or `overrides.yaml`) from what you see here, then re-run
`classify`.

### Step 4 — `plan`: build a dry-run manifest
Computes destination paths. **Nothing is moved.**
```bash
yt-id plan --target "/Music Videos"
# destination names are "Artist - Title [id].ext"
yt-id plan --target "/Music Videos" --omit-artist-from-filename
yt-id plan --target "/Music Videos" --strict-names --spaces-to-underscores
yt-id plan --target "/Music Videos" --min-artist-files 2
```

### Step 5 — `apply`: execute the manifest
```bash
yt-id apply --manifest manifest.json            # dry-run pre-flight (default)
yt-id apply --manifest manifest.json --apply    # actually move files
yt-id apply --manifest manifest.json --undo --apply   # reverse a prior apply
```
On failure the default policy rolls back the current run; `--on-error stop` or
`skip` change that behavior.

### Step 6 — `export`: archive an audit ledger (optional but recommended)
After the files are moved, dump a **denormalized ledger** — one row per file,
joining what was scanned, decided, and where it landed — so you have a
human-readable master record independent of the DB:
```bash
yt-id export                          # writes ledger.json + ledger.csv
yt-id export --format csv --out audit # just audit.csv
```
Then archive it alongside the DB and your manual answers, next to the videos:
```bash
tar czf ~/archive/2026-05-01-batch.tar.gz ytid.db ytid.yaml ledger.json ledger.csv
```
The ledger is a **view** for auditing, not a restore format: `ytid.db` remains
the queryable truth and `ytid.yaml` the re-appliable record of your hand edits.

---

## 5. Safety model

- **Dry-run by default.** `plan` never moves anything, and `apply` only moves
  files when you add `--apply`.
- **Reversible.** `apply --undo` restores files from a prior manifest.
- **Idempotent state.** Re-running `scan`/`fetch`/`classify` updates the same DB
  rather than duplicating work.
- **Your ground truth wins.** Entries in `overrides.yaml` always beat automated
  guesses and survive re-classification.

---

## 6. Quick end-to-end example

```bash
# from inside the folder you're organizing (ytid.db + ytid.yaml live here)
yt-id scan
yt-id fetch
# open ytid.yaml and fill in anything flagged, then:
yt-id classify
yt-id review                      # sanity-check the queue
yt-id plan     --target "/Music Videos"
yt-id apply    --manifest manifest.json          # dry-run
yt-id apply    --manifest manifest.json --apply  # go
yt-id export                                      # ledger.json + ledger.csv (audit)
```

---

## 7. Sharing & publishing your own copy

There are two levels here. **Most people only need Path A.**

### Path A — Install straight from GitHub (recommended, no publishing)

Because the repo is public and `pyproject.toml` is complete, anyone (including
future-you on a new machine) can install `yt-id` with a single command — no
account, no build step, nothing to upload:

```bash
pipx install git+https://github.com/jneallawson/yt-id.git
```

- **Updating to the latest commit:**
  ```bash
  pipx install --force git+https://github.com/jneallawson/yt-id.git
  ```
- **Pinning to a specific tag or commit** (reproducible):
  ```bash
  pipx install "git+https://github.com/jneallawson/yt-id.git@v0.1.0"
  ```
- Remember `yt-dlp` is still a separate prerequisite (see §1).

That's the whole story for Path A. Cutting a GitHub "release" is optional — it
just creates a downloadable tag people can pin to.

### Path B — Publish to PyPI (optional, enables `pip install yt-id`)

Do this only when you want the short, discoverable command `pip install yt-id` /
`pipx install yt-id` to work for everyone. It adds a free account, an API token,
and a build+upload step per release.

**One-time setup**

- Create accounts on [PyPI](https://pypi.org) and, for practice,
  [TestPyPI](https://test.pypi.org).
- Generate an API token on each and store it (e.g. in `~/.pypirc`). Use the
  token as the password with username `__token__`; never your account password.
- The distribution name `yt-id` must be unused on PyPI (check the URL
  `https://pypi.org/project/yt-id/`). Pick another `name` in `pyproject.toml` if
  it's taken.

**Build the artifacts** (the `dev` extra installs `build` and `twine`; get them
with `pip install -e ".[dev]"`):

```bash
rm -rf dist/ build/ *.egg-info          # start clean
python -m build                         # writes dist/yt_id-<ver>.tar.gz + .whl
python -m twine check dist/*            # validate metadata / README rendering
```

- `dist/*.tar.gz` is the **sdist** (source), `dist/*.whl` is the **wheel**
  (pre-built). Both carry the packaged `ytid/data/*.yaml` and `LICENSE`.

**Rehearse on TestPyPI, then go live on PyPI:**

```bash
# dry run against the sandbox index
python -m twine upload --repository testpypi dist/*
pipx install --index-url https://test.pypi.org/simple/ yt-id   # verify it installs

# the real thing
python -m twine upload dist/*
```

**Release checklist (every time)**

1. Bump `version` in `pyproject.toml` — PyPI refuses to overwrite an existing
   version.
2. `rm -rf dist/ build/ *.egg-info` and rebuild.
3. `python -m twine check dist/*`.
4. Upload (TestPyPI first if unsure).
5. Optionally tag the release in git: `git tag v0.1.0 && git push --tags`.
