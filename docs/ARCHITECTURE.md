# Architecture and source map

```text
wolf-music-studio/
  studio                       portable executable launcher
  studio.py                    setup, settings, doctor, services and CLI routing
  studio.json.example          no secrets; local/settings.json is ignored
  requirements-*.txt           controller/dev/optional tagging dependencies
  yue2/
    src/yue2/                  upstream inference plus local T4 memory adaptations
    queue_supervisor.py        directory queue ownership and Colab lifecycle
    colab_supervisor.py         allocation, progressive harvest, remote supervision
    remote_worker.py           detached bootstrap, restore, generation, backup
    archive_run.py             checksum/duration verification and remote cleanup
    run_config.py              JSON schema, immutable run identity and Drive path
    processTracks.py           automatic strict truncation and named FLAC publication
    autoTrim.py, trim_music.py  analysis/trimming and review viewer generation
    track_registry.py          global JSON database and per-file sidecars
    track_catalog.py           SQLite searchable catalog, favorites and exports
    trackLibrary.py            nonblocking curses library and clipboard interface
    track_player.py            asynchronous mpv controls
    outputs/trackTags.py        optional Essentia tags; outputs data is ignored
    gemma_runtime.py            scoped model lifecycle and hardware lease
    gemma_prompt_cache.py      resident llama.cpp prefix checkpoints
    gemma_songwriter.py         campaigns, retries, validation and atomic submission
    songwriter_client.py       JSON-schema chat requests, SSE logs and provenance
    songwriter_policy.py       packaging-only normalization and isolated line repair
    songwriter_metrics.py      separately audited review outcomes and rates
    compareSongwriters.py      HTML/JSON comparisons and separate listening ratings
    songwriter/                static system prompts, concepts and reference examples
    examples/historical_configs/  archived source prompts; off-limits for copying
  visualizer/
    vis2GPUV8.py                shared-lease entrypoint around V7
    vis2GPUV7.py                karaoke, verified multi-orientation output
    vis/                       audio features, captions, effects, renderer, encoder
    scripts/setup_karaoke.sh    pinned engine and optional model downloads
    config.json                visual/encoder configuration
  gemma/
    setupGemma12B.sh            pinned llama.cpp build and optional GGUF download
    startGemma.sh, stopGemma.sh scoped manual runtime controls
    askGemma.sh                 streaming manual prompt client
  docs/, tests/, scripts/      operation guides, contracts and publication checks
```

## Local versus remote computation

The controller imports only lightweight analysis/standard-library orchestration modules. The remote kit bundles the exact YuE2 source and selected run requests, using the run manifest's Drive path. The worker installs pinned inference dependencies into a managed Python 3.12 environment and preserves completed stages between allocations.

Locally, visualizer subprocesses explicitly use the visualizer interpreter, not the controller's interpreter. `YUE2_COMPUTE_ROOT` is shared across the songwriter and V8 visualizer. FIFO tickets include process/start-time identity so stale tickets are pruned; the inherited hardware lock survives controller death until the child releases it. A resident songwriter retains that lease for the entire campaign. Prefix cache warming is once per server/prompt signature, with live checkpoints reused across drafts.

## Original audio, masters and captions

Verified downloads are immutable originals. The automatic processing step truncates the main track before detached trailing material and writes a named FLAC with provenance. Original-only/trimmed status, song prompts, tags and visualizer state flow into the shared registry and catalog. Video jobs snapshot the exact audio, prompt, visualizer config and source fingerprints, making unrelated song versions unsuitable as accidental lyric references.

Recognition uses HTDemucs stems, Vulkan Whisper and CPU CTC alignment. Prompt lyrics guide interpretation; they do not establish perfect sung-word recognition. Word timelines and review flags remain available for correction/re-rendering. MP4 receipts verify streams, orientation and duration before final publication.

## Songwriter review and accounting

V2 constrains only the outer creative JSON fields. It never constrains the words using a vocabulary grammar. The harness assigns IDs/settings and can append a missing terminal marker to an otherwise mechanically complete outro without changing sung words. Incomplete responses remain invalid.

Similarity combines content-aware weighted 3-word overlap, 4/5-word overlap, meaningful full-line copying, longest matching sequences and whole-corpus collage coverage. Section names and musical cues are excluded from sung-text comparison. Historical configs and reference examples are always included, alongside live queued/generated/accepted songs.

An isolated short copied verse passage can receive one bounded replacement of at most two original line addresses. Hooks, collages and broad reuse require a full rewrite. Repairs cannot change other lines or performance cues. The final candidate is validated and compared again; the original failure remains retained and counted. Separate outcome records survive restart without duplicate counting.

Human listening scores are independent, with missing scores represented as unrated. A high acceptance rate is not evidence that vocals, musical pace or emotion are better.
