# Operating the workflow

## Commands

The launcher preserves each component's CLI, including `--help`:

| Command | Purpose |
| --- | --- |
| `./studio library` | Interactive library: default name search, playback, clipboard, favorites |
| `./studio make-config` | Validate/merge downloaded JSONs and publish to pending |
| `./studio trim DIRECTORY` | Recursively create strict-truncation named FLAC masters and tag them |
| `./studio tag FILE_OR_GLOB` | Analyze/backfill Essentia tags and update the database/library |
| `./studio visualize --status` | Persistent visualizer queue state |
| `./studio visualize --enqueue DIRECTORY` | Backfill processed masters |
| `./studio visualize --retry-failed` | Retry reported failures after correcting the cause |
| `./studio songwriter --status` | Active service campaign's review/queue metrics |
| `./studio songwriter --campaign PATH` | Run/resume a different campaign |
| `./studio compare` | Latest campaign versus its baseline/previous campaign; single campaign if none exists |
| `./studio validate INPUT --output REVIEW` | Review arbitrary prompts without generating music |
| `./studio start-run --config FILE --prepare-only` | Validate/create a run without requesting Colab |

Use absolute paths when invoking a component from outside the checkout. The queue root is `yue2/queue`, recordings/catalogs are under `yue2/outputs`, campaign drafts are under `yue2/songwriter/runs`, and video/model caches are under `visualizer/output`.

## Queue and output safety

Audio seed sweeping is opt-in. New runs default to one generation per input song using its own seed; `./studio start-run --config FILE --seedSweep 3` creates three total seed variants. `--seed-sweep` is an equivalent spelling. Extra variants keep the same lyrics/style, get deterministic distinct seeds and IDs, and appear in the saved `requests.jsonl`. Add `--prepare-only` to review those requests without cloud work.

The raw directory watcher accepts `python yue2/queue_supervisor.py --seedSweep 3` to apply sweeping to newly claimed jobs. Its default remains one. A saved run always resumes its frozen requests regardless of the watcher's current default; existing WolfcoreV7 sweeps stay intact. Configs that already contain explicit seed variants are preserved as written, so do not add a sweep flag to them unless you want additional variants. Gemma's `--per-idea` count controls different lyrics, not repeated audio generations.

`make-config` accepts substring/glob filename filters, modification-age filters, or an explicit time range. It parses JSON with duplicate-key/nonfinite-value rejection, validates the actual pipeline schema, and atomically publishes the aggregate. Existing pending files are not overwritten.

```bash
./studio make-config -name powerwolf -iString 'gemini-' -time 5h
./studio make-config -name powerwolf --input-dir /path/to/downloads --dry-run
```

Historical configs under `yue2/examples/historical_configs` and source requests under `yue2/requests` are **examples**, not automatically queued. The simulator does not copy an old queue into the new installation. To submit a prepared config atomically, copy it to a `.tmp` path in `pending` and rename it to `.json` after validation.

Queue states are `pending → running → done`, with `failed` and `cancelled` holding explicit outcomes. Interruptions preserve the current run identity/stages. Completed local downloads retain original `audio.flac`; strict truncation creates `<track-directory>.flac`, reports, checksums and registry metadata. Detached trailing music is discarded in this automatic workflow. FLAC is retained for subsequent analysis and video creation; generated MP4s contain their own AAC copy.

The global JSON registry, per-file sidecars and SQLite catalog track prompt source, original/trimmed media, tags and visualizer outputs. Favorites and categories are stored in SQLite and cannot be rebuilt from a scan alone.

After each verified download is trimmed, TrackTags analyzes the named FLAC, then the visualizer is enqueued. Install its dependencies with `./studio setup --profile controller --tags`. The classifier runs on CPU with bounded thread counts; `YUE2_TAG_PYTHON` selects its interpreter for standalone workers (the launcher supplies the controller interpreter). Results are bound to the trimmed audio checksum and skipped when current. Tagging failures appear under `tagging_workflow` and in the download receipt, retry on the next harvest, and do not prevent video enqueueing.

TrackTags saves ten ranked entries in `top_genres`, ten entries in `hashtags`, and the first five joined by spaces in `hashtags_top5`. Scores are model outputs, not a guarantee that every genre applies. The library's F4 copies the top five; searches still cover all ten. Older five-tag records are reanalyzed when revisited. To backfill one master, run `./studio tag /path/to/track/track.flac`; recursive `./studio trim DIRECTORY` also tags unchanged masters, or add `--no-tags` for trimming alone. These operations preserve original audio and generation prompts.

Visualizer jobs with catastrophic captions become `needs_review`, retaining their timelines, diagnostics and any previous videos. They are not retried automatically. Review the words against the recording, then enqueue the master with a corrected `--words FILE` snapshot, or enqueue it normally after an algorithm update. Both approaches create a new job when the caption/source fingerprint changes. See the [karaoke documentation](../visualizer/README.md) for the guards and commands. These checks detect structural failures; accepted captions can still contain recognition mistakes.

## Logs and services

```bash
./studio logs songwriter
./studio logs queue
./studio logs visualizer
./studio services status
./studio services stop
```

Native names are `wolfstudio-queue.service`, `wolfstudio-songwriter.service`, `wolfstudio-visualizer.service`, and `wolfstudio-report.timer`. The report refreshes every minute and never loads a model. Journals and per-run `pipeline.log`/`colab_run.log`/`render.log` retain detail.

A whole songwriting campaign owns the local lease until it completes, is interrupted, or fails. To let it finish the current draft/repair and release hardware:

```bash
touch yue2/songwriter/runs/studio_v2/pause_after_draft
```

It flushes accepted leftovers to the queue and exits. Remove the marker, then `./studio services start --only songwriter` to resume. A new campaign should have a new directory; frozen prompt assets are verified on resume. The visualizer uses the same lease, so it can take the next turn after the resident model shuts down.

Stopping the local directory watcher does not itself release a remote Colab VM. When deliberately aborting cloud work, also stop the corresponding session with `.venv/bin/colab stop -s wolfstudio-directory-queue`. Do not delete pending/running directories merely to pause them.

## Comparing versions and song quality

```bash
./studio compare --old yue2/songwriter/runs/baseline --new yue2/songwriter/runs/new_version
```

Open `yue2/songwriter/comparison/comparison.html`. The report shows reviewed draft counts, raw pass/fail, marker fixes, isolated line repair attempts/successes, final pass/fail, and separately averaged listening scores. Original failures remain failures at the raw stage even when later rescued. Imported pilots and in-flight/interrupted requests are identified separately. The JSON report retains source/settings hashes, version labels and matching concept/variant seed pairs.

Use the browser's score fields and Export ratings JSON, then import:

```bash
./studio compare --import-ratings ~/Downloads/songwriter_listening_ratings.json
```

Or save a score directly:

```bash
./studio compare --rate-campaign yue2/songwriter/runs/studio_v2 \
  --rate TRACK_ID --overall 8 --pace 9 --vocals 8 --story 7 --notes 'Great kicks; bridge drags'
```

Ratings are separate files and never change prompt JSON. Scores without available local audio are excluded from finished-audio averages. Browser scores stay local until exported/imported. Fresh installs have no baseline/results; reports appear once campaigns exist, and comparisons become useful as tracks finish. Mechanical validation cannot determine the quality of a generated performance.

## Backups

Back up generated media and these records outside Git:

- `yue2/outputs/master_database.json` and per-file sidecars;
- `yue2/outputs/track_catalog.sqlite3` (**favorites/categories**);
- `yue2/outputs/<run>` provenance, stage artifacts and original/trimmed recordings;
- `yue2/queue` while stopped, including visualizer job prompts/config/state;
- `yue2/songwriter/runs` drafts, metrics, frozen assets, ratings and submissions;
- `visualizer/output` word timelines and videos;
- ignored local settings and separately protected authentication configuration.

Use SQLite's online backup command or stop catalog writers before copying the database/WAL files. For example, `.venv/bin/python -c 'import sqlite3; s=sqlite3.connect("yue2/outputs/track_catalog.sqlite3"); d=sqlite3.connect("/path/to/backup/catalog.sqlite3"); s.backup(d); d.close(); s.close()'` creates a consistent catalog backup while preserving categories. Test restores independently of the live queue.
