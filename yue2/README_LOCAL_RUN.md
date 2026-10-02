> Component reference notes. Use the top-level [README](../README.md) and installation guide for portable setup/service names; dated campaign details describe the original deployment.

# YuE2 industrial 360s run

## Interactive track library

```bash
./trackLibrary.py                 # first launch imports everything automatically
./trackLibrary.py "blood moon"    # start with a name search
./trackLibrary.py --refresh       # rescan prompts, TrackTags and audio files
./trackLibrary.py --index-only    # rescan and exit, also suitable without a terminal
```

The interface uses Python's standard-library curses module. Playback requires
`mpv` (already installed on this machine). It runs independently of searching,
refreshing, and clipboard actions. The player exits when you close the browser.

| Key | Action |
| --- | --- |
| Type / Up / Down | Search names and IDs / select a result |
| Enter | Play the selected file |
| F3 or Ctrl+C | Copy the full source song config as JSON |
| F4 or Ctrl+T | Copy the selected file's tags, e.g. `#Metal #Hardcore #Rock` |
| F6 or Ctrl+O | Select another audio version |
| F7 or Ctrl+P / F8 | Pause or resume / stop |
| Left / Right | Seek backward / forward 10 seconds |
| Ctrl+B / Ctrl+F | Seek backward / forward 30 seconds |
| F2 or Ctrl+A | Advanced search |
| F5 or Ctrl+R | Refresh files and tags in the background |
| F11 or Ctrl+G | Favorite categories and example config exports |
| PgUp / PgDn | Scroll track details and lyrics |
| Esc or Ctrl+U | Clear the name search |
| Ctrl+Q or F12 | Quit |

To collect exemplary tracks, select a track and press **F11 / Ctrl+G**. Press
**N** to create a category, then **Space** to add the track. Space also removes
an existing membership. A track can belong to multiple categories; `*` marks
tracks saved in at least one category. **Enter** browses the highlighted
category, and **A** returns to all tracks. Advanced search also accepts an exact
category name.

In the categories screen, press **E** to export the highlighted category.
The default destination is `examples/favorites/<category>.json`; edit the path
or press Ctrl+U to replace it. Exports contain `{"songs": [...]}` with only song
config fields, without TrackTags, audio paths, category labels, or source-file
provenance. The pipeline validator checks the complete export before it is
published, and existing files are never overwritten. Duplicate song IDs from
different batches receive unique `_example_N` suffixes in the export; their
lyrics and generation settings are preserved. Exports are reference files and
are not automatically placed in the generation queue.

Categories retain a snapshot of the prompt when you add a track, so an example
can still be exported if its source files are later removed. Tracks without a
known source prompt cannot be added. Categories and memberships survive full
and incremental catalog refreshes. **Back up `outputs/track_catalog.sqlite3`**
to preserve these selections; deleting the database loses user-created
categories even though the scanned track catalog can be rebuilt.

Advanced search combines batch, inclusive local completion dates (falling back
to the batch date), all requested hashtags, trimmed status, original-only versus
named FLACs, availability, untagged tracks, and text within styles, lyrics,
genres, descriptions and audio metrics. Blank fields impose no filter. F4 clears
advanced filters; Enter applies them. Name search stays active alongside them.

Details show full file locations, original duration, tag confidence, BPM, key,
danceability, and the source prompt. File selection prefers FLACs, with trimmed,
named and original versions in that order. A `TRIM_` filename indicates trimming;
a named FLAC alone does not. The original-only and named-FLAC indicators describe
FLAC files in the generation folder. Trimmed exports are also recognized. Use
F5 after trimming, renaming or moving files outside this application.

The catalog is `outputs/track_catalog.sqlite3`, separate from TrackTags'
`outputs/master_database.json`. It retains the complete per-file TrackTags
records. Duplicate song IDs in different batches stay separate. Exports are
linked when their IDs identify exactly one generated track; ambiguous exports
remain separate without an inferred prompt. Tags are copied from the selected
file, never substituted from another audio version. The desktop clipboard uses
`wl-copy`, `xclip`, `xsel`, or `pbcopy`; other terminals receive an OSC 52 request
and must permit clipboard access.

`run_batch.py` saves the exact completed request next to the audio as
`request.json`. The local download/archive completion hooks import it and older
frozen run configs into the catalog. `outputs/trackTags.py` updates the catalog
after new, restored, reused, or already-completed tags. The open browser reloads
catalog updates automatically. Existing running supervisors load the new hooks
when their next process starts; use F5 to import work completed by older
processes. No running generation needs to be interrupted.

The scanned catalog is rebuildable; refreshing preserves favorites and does not change audio, prompts or the
TrackTags database. `--outputs` and `--db` select alternate locations for testing
or another library. Generated songs without local audio are marked pending or
missing, and missing files referenced by TrackTags remain visible.

## Directory queue (recommended)

Combine downloaded JSON song prompts and submit them with:

```bash
./makeConfig.py -name powerwolf -iString "gemini-" -time 5h
```

This selects `.json` files directly inside `~/Downloads` whose names contain
`gemini-` and whose modification times fall within the last five hours. It
validates every input and the combined output with the pipeline schema, then
atomically publishes `queue/pending/powerwolf.json`. The queue service picks it
up after any earlier jobs. Existing pending files are never overwritten.

Filename and time filters can be used separately or together. `-iString` accepts
a case-sensitive substring or a quoted shell glob such as `"gemini-*.json"`.
`-time` accepts positive durations in seconds (`s`), minutes (`m`), hours (`h`),
days (`d`), or weeks (`w`). For an explicit inclusive time range, use
`--since "2026-09-24T00:00:00" --until "2026-09-24T06:00:00"` instead of `-time`;
times without a timezone offset use local time. Use `--input-dir /path/to/files`
to change the source folder, or `--dry-run` to validate without queueing.

Inputs are merged oldest first, preserving song order, lyrics, styles, and
explicit settings. Missing IDs and seeds are assigned across the whole batch;
source filenames and SHA-256 hashes are retained. Invalid JSON, invalid song
settings, duplicate song IDs, or no matches stop submission without queueing
a partial batch. Re-running after the queue claims a config submits a new run;
the script does not track previously imported downloads.

Drop JSON configs into `/path/to/wolf-music-studio/yue2/queue/pending/`.
The `yue2-queue.service` user service watches this directory even after logout.
For an atomic handoff, copy to a temporary name, then rename:

```bash
cp /path/to/my_songs.json /path/to/wolf-music-studio/yue2/queue/pending/my_songs.tmp
mv /path/to/wolf-music-studio/yue2/queue/pending/my_songs.tmp /path/to/wolf-music-studio/yue2/queue/pending/my_songs.json
journalctl --user -u yue2-queue.service -f
```

Configs run oldest first. Pending configs reuse the same T4 allocation, installed
environment and Hugging Face model cache. GPU models still load/unload between
stages to preserve the T4 memory budget; they are not all kept resident. When no
configs remain, the supervisor stops Colab and waits locally without requesting
a GPU. A later config triggers a new allocation and its necessary setup.
Token cycling remains disabled.

Claimed configs move through `queue/running/` to `queue/done/` after successful
completion; each job retains its original JSON under `input/` and run manifest.
Interrupted generation stays at the front of the queue and resumes the same
manifest with a retry delay. Invalid input configs go to `queue/failed/`.
Each song runs in a fresh process to release CPU and GPU memory between songs.
Killed workers retry from saved completed stages; partial semantic generation
still restarts that stage. Restarting the
service resumes its claimed run rather than generating a new timestamp. Invalid
configs go to `failed` without allocating a GPU. `queue/state.json` shows status.
Each config keeps its own timestamped output folder as described below.

Completed tracks, plans, semantic tokens and acoustic latents are downloaded and
verified locally before their run is removed from Drive. The recent 360-second
tracks have approximately 124–144 KB of plan data, 36 KB of semantic tokens and
2.2 MB of latents each, so all are retained. Plans include ABC score and token
data; edited plans/lyrics require appropriate downstream regeneration (the JSON
runner does not yet expose an edited-ABC input option).

To stop the directory watcher: `systemctl --user stop yue2-queue.service`.
To restart it: `systemctl --user start yue2-queue.service`.
Stopping the watcher manually is not a Colab release command; use
`colab stop -s yue2-directory-queue` as well when aborting active work.

## Start a new run from JSON

```bash
cd /path/to/wolf-music-studio/yue2
python start_run.py --config /path/to/my_songs.json
```

This starts a user service that survives terminal closure/logout. It uses T4 by
default. Each invocation creates a fresh folder, for example:

```text
outputs/2026-09-16_14-30-05_123456_my_songs/
```

The prefix is local date/time (including microseconds to avoid collisions); the
suffix is the JSON filename without its extension. Spaces and other special
characters in filenames become underscores. The matching Drive run uses the
same folder name. You no longer need to edit paths in any scripts.

Accepted JSON: one song object, an array of songs, or `{"songs": [...]}`.
Each song requires `style` and `lyrics`; `id`, `title`, `seed`, `cot` and
`duration_validation` are optional. Missing seeds start at 85300. The existing
individual files in `requests/`, such as `nuclear_industrial_assault.json`, are
ready-to-use examples. `target_seconds` defaults to 360; this runner currently
supports 360 seconds only. Legacy JSONL and a `generations` array also work.

The run folder retains the source config, normalized `requests.jsonl`,
`run.json`, `colab_state.json`, `colab_run.log`, and per-song artifacts.
The start command prints the exact log path and service name.

Validate/create the folder without requesting a GPU:

```bash
python start_run.py --config /path/to/my_songs.json --prepare-only
```

Continue an existing run with its original timestamp and frozen config:

```bash
python start_run.py --resume outputs/<run-folder>/run.json
```

Automatic process restarts also use `--resume`, so they do not create new runs.
Use `--token-cycle` only if explicitly desired; it defaults off. For a foreground
run, `python colab_supervisor.py --config /path/to/my_songs.json` is also supported.

This kit is pinned to upstream YuE2 at `0edaf2f4053ef4731334b8329834b107977f9637`.
The active T4 trial converts the official `m-a-p/YuE2-3B` weights to FP16 and uses the default listening
`m-a-p/YuE2-Vae` float32 decoder.  Requests are derived from the five
`grokPrompts2.json` concepts, with six-act lyrics, recurring hooks and
instrumental sections.  Semantic sampling is configured for 9000 codec
frames (25 Hz = 360 seconds); the acceptance check is deliberately broad
(250–380 seconds) to catch pathological truncation without rejecting normal
tempo variation.

`colab_supervisor.py` requests a T4 and retries quietly when quota is
unavailable.  Token cycling is never automatic; pass `--token-cycle` only if
you explicitly choose that behavior.  `run_batch.py` saves an immutable plan,
semantic tokens, 250-frame replay-safe progress checkpoints, latents, audio,
and a result receipt per song. Completed stages are restored directly from Drive.
Correction to the original description: the partial semantic arrays are progress
records, not a working mid-stage resume implementation. An interrupted semantic
stage currently starts that stage again from its seed. A matching completed
semantic stage skips AR entirely. Cross-device bit-identical output is not promised.

## T4 memory trial

- Explicit `model_dtype=float16`; upstream BF16 remains the default for other callers.
- Eager cached AR uses 256-token prefill blocks; unused NAR weights stay on CPU.
- T4 grouped-query attention uses repeated K/V heads, allowing memory-efficient
  SDPA selection rather than an unsupported FlashAttention/GQA path.
- Acoustic attention uses 256-query tiles with all original keys visible;
  unused AR weights are offloaded after acoustic prefill. All 32 midpoint steps remain.
- The song model is deleted before loading the full FP32 listening VAE.
  Decode uses 128-frame tiles with the original 16-frame halos.
- Direct Hugging Face model downloads and direct Drive restore/backup every
  45 seconds; local PC uploads only code, prompts and the selected Drive credential.
- Model/decoder revisions are pinned and each stage reports actual GPU memory.
- FP16 is an experiment, not numerically identical to the official BF16 baseline.
  Non-finite logits, acoustic states and audio fail immediately; audio also has
  silence, duration and checksum checks.

The original five-track trial completed on T4; observed synthesis peak was about
9 GB. The old `yue2-colab.service` and root-level state/log refer to that legacy
run; new runs have separate services and files inside their dated output folder.

Transfers allow up to 30 minutes for larger queues, retaining a 120-second
network inactivity limit. On timeout, the entire transfer process group is
stopped before retrying. Backups snapshot only files changed since the last
successful upload; unchanged finished audio is skipped. Allocation failures
are retained in the state JSON without logging every retry to the console.

## Colab setup recovery (2026-09-15)

The first T4 allocation stopped before model loading because Colab's system
Python 3.13 failed inside `venv`/`ensurepip`. Bootstrap now downloads pinned uv
0.11.19 and creates a managed Python 3.12 environment with pip. This bypasses
the incomplete system Python installation. This setup has passed on an actual T4.

Exit receipts identify setup, generation and backup failures. The supervisor
retries setup/backup failures with a delay, keeping downloaded files on the same
allocation. A new launch archives the previous exit receipt so stale failure
state cannot terminate the new worker. Generation failures still stop for
inspection rather than repeatedly consuming GPU time on the same model error.

## Completed queue archiving

After an allocation disconnects (four failed probes), and when the supervisor
starts, it downloads any newly completed tracks and verifies their checksums and
audio durations. Already verified local tracks are skipped. This runs before the
next allocation request, not after every unsuccessful allocation attempt.
`downloaded_tracks.json` records the locally available tracks. Drive copies stay
available for resuming the unfinished queue; final verified cleanup remains below.

The supervisor checks local completion and Drive results before requesting a GPU
and while monitoring a session. When every configured track finishes, `archive_run.py`
downloads the entire run into `outputs/<timestamp>_<config-name>/`, compares every Drive
file's MD5 with its local copy, checks audio SHA-256 against each result receipt,
and verifies stereo 48 kHz audio and the configured duration bounds.

For standalone runs it stops this queue's Colab session. For directory-queued
runs it waits for the final remote backup to finish and retains the VM for the
next config (the directory supervisor stops it when idle). It rechecks the remote inventory for changes,
then permanently removes only the corresponding `yue2/<run-folder>` from Drive. Audio, plans,
semantic tokens, latents and metadata remain locally. `local_completion.json`
records the verified inventory and completion so service restarts do not request
another GPU. Interrupted cleanup resumes from this record. A changed request set
requires a new output/run name.
