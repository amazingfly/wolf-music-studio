> Component reference notes. Use the top-level [README](../README.md) and installation guide for portable setup/service names; dated campaign details describe the original deployment.

# AutoTrim for generated music

## Automatic production workflow

`processTracks.py` is the production command. After each verified local track download,
the supervisor uses **strict truncation**, saving `<track-directory>.flac` beside
`audio.flac`. It keeps the original unchanged and excludes detached late music;
it does not join passages. The lossless FLAC master retains the source sample rate,
channels, bit depth and retained PCM samples, making it suitable for audio analysis
and later visualizer/MP4 rendering. FLAC avoids a lossy OGG intermediate before the
video's final audio encode.

```bash
# Recursively process a generation batch, a track directory, or all outputs.
python processTracks.py outputs/2026-09-29_00-46-48_675080_wolfcoreV7
python processTracks.py /path/to/directory
python processTracks.py outputs

# A single original is also accepted.
python processTracks.py /path/to/track/audio.flac

# Recompute outputs already owned by this workflow.
python processTracks.py /path/to/directory --force

# Explicitly replace a conflicting existing or manually modified named export.
python processTracks.py /path/to/directory --overwrite
```

Only original `audio.flac` files are input, so recursive scans never re-trim named
masters or test exports. Unchanged source/settings/output checksums skip audio
processing. A changed source or algorithm regenerates intact outputs owned by this
workflow; other named exports and manually modified outputs are preserved unless
`--overwrite` is provided. Existing generation receipts are checked for completeness
and original SHA256 integrity. Output audio is decoded and verified before atomic
publication. No AI model is loaded for this strict method.

Each master gets `<track-directory>_trim.json` (detailed detection report) and
`<track-directory>_metadata.json`. The original gets `audio_metadata.json`.
`outputs/master_database.json` records both files, prompts, generation batch,
workflow status, durations, removed time, format, checksums, retained frame ranges,
and the preferred playback/visualizer file. `--database /path/to/master.json` selects
an alternative JSON registry. Registry writes are atomic and share an exclusive
lock with TrackTags. Per-track locks prevent concurrent duplicate processing.

The SQLite track library refreshes after downloading or a standalone batch. Named
workflow masters are marked trimmed and selected ahead of test exports or originals.
Acoustic metrics are not copied from untrimmed audio. Existing genres/hashtags can
be inherited, with their source explicitly recorded; running `outputs/trackTags.py`
on a master computes fresh acoustic metrics and preserves its workflow metadata.

Processing failures are recorded in the registry and reported; a standalone run
exits nonzero if any track fails. Downloading and original archive verification remain
independent, and later download harvests retry processing failures. Previously
completed runs can be processed explicitly with this command.

### Automatic karaoke visualization

After successful truncation in the download hook, `archive_run.py` adds the named
FLAC to `visualizer_queue.py`. Rendering runs in the separate
`yue2-visualizer.service`, using `/path/to/wolf-music-studio/visualizer/vis2GPUV7.py`, so lyric
recovery and video encoding do not block downloads. V6 remains available. Jobs use
the exact request lyrics, snapshots of the visualizer config, and source checksums.
Both widescreen and portrait karaoke MP4s are written under
`/path/to/wolf-music-studio/visualizer/output/yue2/<batch>/<track>/<job-prefix>/`.

`visualizer_workflow` in the global registry and local metadata records job status,
video paths, caption timeline, confidence review count, errors and log paths.
Jobs survive restart, deduplicate unchanged inputs, and retry failures up to three
times. Audio/trimming success is retained if visualization fails.

```bash
python visualizer_queue.py --status
python visualizer_queue.py --enqueue /path/to/processed/run
python visualizer_queue.py --retry-failed
systemctl --user status yue2-visualizer.service
journalctl --user -u yue2-visualizer.service -f
```

For standalone processing followed by rendering, run `processTracks.py DIRECTORY`
then `visualizer_queue.py --enqueue DIRECTORY`. New downloaded tracks get both
steps automatically. The visualizer service unit is in `systemd/`; its process runs
independently of the Colab queue's smaller memory limit. Detailed V7 usage is in
the visualizer's README.

## Comparison tool

`autoTrim.py` reads original `audio.flac` files and, by default, writes two versions next to each original: `testTrim_<parent-directory>.flac` (joined) and `testTrim_truncated_<parent-directory>.flac` (strictly truncated). It leaves originals intact and refuses existing output files unless `--overwrite` is supplied. It runs entirely on the local machine.

Run on the exact star-reference batch:

```bash
python autoTrim.py outputs/2026-09-26_18-51-22_286288_wolfcorev3
```

This is the September 26 batch containing Bone and Iron V3 and Pelt and Polish V3, as established in this conversation. It is separate from the older September 18 folder literally named `wolfV3`.

The joined version follows your requested rule: preserve a later passage when more than five seconds is classified as music, then replace the intervening inactive gap with **0.5 seconds**. The additional truncated version stops after the main song’s attached fade and padding; it excludes every detected later group, even if that group contains good music. It has no joins, inserted gaps or splice fades and preserves its one retained PCM interval exactly. Music is classified with local YAMNet inference. Gaps are only shortened after the main song; pauses within the main song remain intact. Short detached scraps are excluded. A substantial later passage with uncertain classification is retained with its original gap and flagged for review.

## Detection and preservation

The first stage combines stereo-safe AC energy, a track-relative gate, estimated quiet-floor level, spectral flatness, low-frequency concentration, persistence and attached fade tracking. It suppresses isolated clicks, stationary low-level hiss, DC offset and low-frequency hum instead of treating every nonzero sample as music. Opposite-phase stereo channels cannot cancel into a false silence decision.

Sustained activity is grouped into sections. The largest group supplies the main-song anchor; earlier audio remains intact by default. Detached groups after the anchor are classified with **Google YAMNet**, a general AudioSet event classifier, through a pinned ONNX conversion. The model consumes unnormalised 16 kHz waveform audio and scores overlapping 0.96-second windows. Confirmed duration is the union of windows whose Music score is at least 0.45 and exceeds the noise/silence scores; it must exceed five seconds with at least 45% coverage before joining. These are model scores, not calibrated probabilities or a measure of artistic quality.

Normal crops preserve all retained PCM samples, sample rate, channels and bit depth. Joins insert exact silence and apply **5 ms edge ramps** to prevent clicks. No normalisation, time stretching or denoising occurs. Every export is decoded again to verify its exact frame count, format and PCM hash. Input checksums are checked before publication. Audio writes are atomic.

This addresses the observed failure modes, but no general classifier can guarantee a perfect artistic ending on arbitrary generated audio. Decisions are inspectable; originals remain available. Low-level ambiguous sustained sound or music that continues to the source limit may be retained rather than forced into a cut.

## Review and overrides

The batch creates `testTrim_review.html`, with Original, Joined and Truncated players grouped under each track, per-version level timelines, highlighted discarded regions, ±10-second controls and complete decision details. Comparison buttons align source positions to each edited version’s timeline; only one player plays at a time. Each output has a `.json` sidecar with source/output checksums, retained sample ranges, thresholds, joins, classifier scores and model identity. The combined `testTrim_report.json` contains all results and any errors.

```bash
# Preview decisions without creating outputs.
python autoTrim.py /path/to/run --dry-run

# Write only the strictly truncated main-song version, without loading AI.
python autoTrim.py /path/to/run --late-audio truncate

# Write only the joined version.
python autoTrim.py /path/to/run --late-audio join

# Keep later passages and all their original gaps.
python autoTrim.py /path/to/run --late-audio keep

# Discard short detached later sections, using signal analysis alone.
python autoTrim.py /path/to/run --late-audio drop

# Regenerate test outputs; original audio.flac files stay intact.
python autoTrim.py /path/to/run --overwrite

# Set an exact end on one track after reviewing it, in seconds.
python autoTrim.py /path/to/track/audio.flac --end 305.8 --overwrite
```

Advanced controls are `--gap` (default 2 seconds), `--join-pause` (default 0.5 seconds), `--trim-leading` (off by default), `--model-dir` and `--no-model-download`.

Dependencies: `numpy`, `scipy`, `soundfile`, and `onnxruntime` for joined output. The default `--late-audio both` creates both versions from one shared analysis pass; `--late-audio truncate` needs no model. They were already installed in this workspace. The model is about 16 MB, automatically fetched once if absent, verified against pinned SHA256 hashes and cached at `models/trim-yamnet`. No audio is uploaded. CPU inference uses two threads; the installed ONNX Runtime has a CPU provider and does not expose Vulkan. A Radeon GPU is unnecessary for this small model.

Model provenance:

- [Google YAMNet documentation](https://www.tensorflow.org/hub/tutorials/yamnet).
- [ONNX export and conversion details](https://huggingface.co/audiomagic/yamnet-onnx), revision `f25b741c2f0bdc6d7e6db24b5fddda23347dbafd`.
- ONNX SHA256: `d3835ffbbd4a1bb3e777f0ca217b5007907f5171dd5d17c4236b95b2af8f908e`.
- The conversion's Apache 2.0 licence is retained in `models/trim-yamnet/LICENSE`.

The track catalog recognises `testTrim_` files as trimmed versions. Refresh the library to discover new exports.

Verification:

```bash
python -m unittest discover -s tests -p test_auto_trim.py -v
```

Tests cover silence, hiss, DC offset, isolated late clicks, fades, opposite-phase stereo, internal pauses, late-music policy, ambiguous later material, exact crop PCM, splice silence/format/interior PCM, refusal to overwrite, strict truncation of substantial later music, exact PCM in truncated output, three-player viewer generation, and real-classifier rejection of silence/hiss/beeps. These checks verify the mechanics and selected failure cases; they do not claim a listening evaluation.
