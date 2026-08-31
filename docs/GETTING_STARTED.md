# Getting Started with `ytid`

`ytid` identifies the artist/genre of a folder full of YouTube-sourced music
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

## 2. How the `ytid` command actually works

`ytid` is a Python package with a **console-script entry point** declared in
`pyproject.toml`:

```toml
[project.scripts]
ytid = "ytid.cli:main"
```

When the package is installed, pip generates a small launcher named `ytid` in
the environment's `bin/` directory. Running `ytid scan ...` just runs that
launcher, which calls `ytid.cli:main`.

> **Why you can't just add the source folder to PATH:** `PATH` finds
> *executables*, but the source folder only has Python *modules*. And
> `ytid/cli.py` uses package-relative imports (`from . import db`), so it can't
> be run as a standalone script. The `ytid` command only exists once the package
> is installed (or via `python -m ytid.cli`).

### Ways to run it

Pick whichever fits how often you use the tool:

| Approach | Command | Notes |
|----------|---------|-------|
| **Dev / editable venv** | `python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"` | Edits to the source take effect immediately. `ytid` works while the venv is active. |
| **No install** | `python -m ytid.cli scan --source ...` | Run from the repo root. |
| **Global, isolated (recommended)** | `pipx install /path/to/yt-id` | Puts a `ytid` symlink on your PATH (`~/.local/bin`) in its own env. Add `-e` for editable. |
| **From Git** | `pipx install git+https://…/yt-id.git` | No local checkout needed. |

If you already have a `.venv` with an editable install, the launcher lives at
`.venv/bin/ytid`. To use `ytid` without activating the venv, add that `bin`
directory to your PATH (not the source folder):

```bash
export PATH="/path/to/yt-id/.venv/bin:$PATH"
```

---

## 3. Where files live

`ytid` reads and writes a few things. Knowing where they land avoids surprises.

### The database (`ytid.db`)
All state — scanned files, fetched metadata, and classification decisions —
lives in a single SQLite file. **By default it is created as `ytid.db` in your
current working directory.**

- Because the default is CWD-relative, **run every command from the same
  directory**, or always pass an absolute path with `--db`:
  ```bash
  ytid --db ~/ytid/library.db scan --source ~/Videos
  ```

### Config files (`genre_map.yaml`, `overrides.yaml`)
Two YAML files drive classification. Each is resolved **independently**, with
the first match winning (per file):

1. an explicit `--config DIR`
2. `./config` in the current directory (handy while developing)
3. your user config dir: `$XDG_CONFIG_HOME/ytid`, else `~/.config/ytid`
4. the **defaults bundled inside the installed package** (always present)

This means the tool works out of the box (packaged defaults), while letting you
override settings without touching the installation.

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
`ytid plan` writes a dry-run manifest describing every intended move. By default
it writes `manifest.json` and `manifest.csv` in the current directory; change
the prefix with `--out`:

```bash
ytid plan --target "/Music Videos" --out /tmp/ytid-manifest
```

---

## 4. The workflow (5 stages)

Everything is safe/dry-run by default. Only `apply --apply` moves files.

```
scan → (unresolved / resolve) → fetch → classify → (review) → plan → apply
```

### Step 1 — `scan`: index your source folder
Walks a folder recursively and records each file, extracting its YouTube ID.
```bash
ytid scan --source ~/Videos/YouTube
```
Files whose ID can't be confidently detected are flagged for attention.

### Step 1a — `unresolved` / `resolve`: fix ambiguous files (optional)
```bash
ytid unresolved                              # list files needing attention
ytid resolve --id 42 --youtube-id dQw4w9WgXcQ   # assign an ID manually
ytid resolve --id 43 --ignore                # or mark it ignored
```

### Step 2 — `fetch`: get metadata via yt-dlp (slow, network)
Politely rate-limited; results are cached in the DB so you only fetch once.
```bash
ytid fetch                       # fetch everything pending
ytid fetch --limit 20            # do a batch at a time
ytid fetch --retry-errors        # retry previous transient failures
```

### Step 3 — `classify`: decide artist / genre / action
Applies your config + heuristics and stores a decision per video.
```bash
ytid classify
ytid classify --allow-missing-genre   # move confident artist-only files into /Artist
ytid classify --config ~/my-config    # use a specific config dir
```
Each video gets an action: **move** (confident), **review** (needs a human), or
**skip**.

### Step 3a — `review`: inspect the buckets (optional)
```bash
ytid review                 # the review queue (default)
ytid review --action move   # what will be moved
ytid review --action skip
```
Curate `overrides.yaml` from what you see here, then re-run `classify`.

### Step 4 — `plan`: build a dry-run manifest
Computes destination paths. **Nothing is moved.**
```bash
ytid plan --target "/Music Videos"
# tidy destination filenames + prepend artist/title:
ytid plan --target "/Music Videos" --clean-names moderate --enhance-names
```

### Step 5 — `apply`: execute the manifest
```bash
ytid apply --manifest manifest.json            # dry-run pre-flight (default)
ytid apply --manifest manifest.json --apply    # actually move files
ytid apply --manifest manifest.json --undo --apply   # reverse a prior apply
```
On failure the default policy rolls back the current run; `--on-error stop` or
`skip` change that behavior.

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
# from a working directory you'll reuse (this is where ytid.db is created)
ytid scan     --source ~/Videos/YouTube
ytid fetch
ytid classify --allow-missing-genre
ytid review                      # sanity-check the queue
ytid plan     --target "/Music Videos" --clean-names moderate --enhance-names
ytid apply    --manifest manifest.json          # dry-run
ytid apply    --manifest manifest.json --apply  # go
```
