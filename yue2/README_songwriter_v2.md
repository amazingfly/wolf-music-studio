> Component reference notes. Use the top-level [README](../README.md) and installation guide for portable setup/service names; dated campaign details describe the original deployment.

# Gemma songwriter V2

The active writer uses `songwriter/runs/gemmaWolf_v2_20261002`. Original results remain in `songwriter/runs/gemmaWolf_20261002`; that campaign is paused after a fully completed draft, and all 35 accepted songs have been submitted. Its remaining slots are retained for later resumption. Neither already queued music nor existing outputs were cancelled.

V2 changes only the authoring/review workflow:

* Gemma returns `title`, `style`, `lyrics` under a three-string JSON schema. The harness supplies ID, audio seed, CoT and duration, then validates the actual YuE2 config.
* A missing final `[End]` can be appended to closed JSON with an existing nonempty `[Outro]`, balanced cues and all other quality checks passing. Truncated JSON, unfinished API responses and missing outros remain failures. This packaging fix changes no sung words; its count is separate.
* An isolated copied passage can receive one repair request targeting at most two short sung lines. Replacements cannot change other lines, cues, style, title or metadata. Hooks, long passages, broad borrowing and collages require a full new draft. Every repaired song passes the same quality and similarity checks. Original rejected drafts remain in `tooSimilar`.
* Earlier choruses are not fed back verbatim. Only human-authored summaries of previous situations are supplied as directions to distinguish.
* Ten concepts each have ten distinct situations, leaving Gemma to choose the precise plot, stakes, imagery and resolution.

The three original reference configs, reference sequence, audio seeds and sampling remain the same: temperature 1, top-p .95, top-k 64, min-p 0. The real model is Gemma4 12B **Q6_K**; K/V caches are **q8_0**, with 16 GPU layers and a 12,288-token context. Gemma stays loaded throughout this campaign. The system/examples prefix is warmed once per server session, then retained in llama.cpp's native checkpoints; no per-draft serialized-slot restores. The existing FIFO hardware lease still prevents concurrent Gemma/visualizer work.

## Logs and status

```bash
journalctl --user -u yue2-songwriter.service -f
python gemma_songwriter.py --campaign songwriter/runs/gemmaWolf_v2_20261002 --status
python compareSongwriters.py
```

Open `songwriter/comparison/comparison.html`. It is black on white and links to prompt JSON, finished audio and visualizer MP4s when available. A timer refreshes it every minute; reload the browser to see updates. `comparison.json` contains the same machine-readable results, paired by concept and variation. Each campaign also has its own `metrics.json`.

## Counts and honest comparisons

The old snapshot, `baseline_metrics.json`, has 56 reviewed drafts: 34 accepted, 16 invalid and 6 too similar; raw success 60.71%. Its imported pilot is listed separately, giving 35 accepted tracks total. A full retry is another draft in the denominator. In-flight requests are excluded. A repaired original is still a raw failure; separate columns show acceptance after packaging and acceptance after local repair. Completed repair requests and time spent on them are counted separately from full-song requests.

V2 keeps one outcome record per full draft in `attempt_results/<track>/attemptNN.json`, plus raw requests, responses and timings in `attempts/`, repair requests and patch audits in `repairs/`, original rejected songs in `tooSimilar/`, and failures in `invalid/`. Metrics are reconstructed from these durable files, so restarts don't reset the counters or double-count a draft.

This is a production-version comparison, not a controlled audio experiment: situations differ, the historical corpus grows, and the older run used different server/cache behavior earlier in its lifetime. Context/cache improvements affect speed; do not attribute all elapsed-time changes to the authoring policy. Higher mechanical acceptance does not establish better music. Listen and score separately.

## Listening quality

The HTML report lets you enter scores and notes, keeps them in browser storage, and exports a ratings JSON file. Import it with `python compareSongwriters.py --import-ratings ~/Downloads/songwriter_listening_ratings.json` to persist scores in campaign records and update the averages. Each accepted track also has a CLI rating command in the report. For example:

```bash
python compareSongwriters.py \
  --rate-campaign songwriter/runs/gemmaWolf_v2_20261002 \
  --rate TRACK_ID --overall 8 --pace 9 --vocals 8 --story 7 \
  --notes 'Relentless drums; clear lead; bridge drags slightly'
```

Scores are 0–10, with missing scores left unrated. Saved ratings live in the relevant campaign's `listening_ratings/`; they never enter song configs or lyrics. Averages count only rated tracks with locally available audio. The report resolves media paths afresh, so newly downloaded/trimmed songs and visualizers appear automatically.

## Stop at a song boundary / resume

```bash
touch songwriter/runs/gemmaWolf_v2_20261002/pause_after_draft
```

The current draft and any bounded line repair finish; accepted leftovers are queued, the model shuts down, and the hardware lease is released. No next song starts. To resume:

```bash
rm songwriter/runs/gemmaWolf_v2_20261002/pause_after_draft
systemctl --user start yue2-songwriter.service
```

Start a separate campaign with `--writer-version 2 --assets songwriter/v2 --campaign songwriter/runs/NEW_NAME --baseline songwriter/runs/gemmaWolf_20261002` to freeze a fresh set of assets. Resuming existing campaigns preserves their manifest's writer version. Remove any pause marker before resuming an old campaign manually.

## Verification

74 relevant workflow checks passed (2 optional checks skipped), including eight V2-specific checks for packaging, isolated repair, protected hooks, repair failure accounting, resume, listening ratings, and stopping at a draft boundary. A broad run also exposed two existing core acoustic-callback tests that construct a pipeline without the already-added `query_chunk_size` attribute. No core YuE2 model files were changed for this work. Use `PYTHONPATH=src` for core package subprocess tests.

Creation-time implementation hashes and source snapshots are retained in each campaign. Reporting and CLI additions made after launch do not reload the currently resident model; subsequent service starts use the updated source files.
