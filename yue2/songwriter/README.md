> Component reference notes. Use the top-level [README](../../README.md) and installation guide for portable setup/service names; dated campaign details describe the original deployment.

# Gemma wolf-girl songwriting

The default campaign explores ten concepts, ten songs per concept, in round-robin order. The first accepted pilot goes to Yue2 immediately; subsequent songs go in batches of ten, with the last partial batch submitted at completion. `accepted_config.json` contains the complete accepted campaign. Drafts that fail validation or similarity review never reach the music queue.

## Reference recipe and provenance

`examples.json` contains the exact saved **Frizzed and Fractured V2**, **Bone and Iron V3**, and **Pelt and Polish V3** prompts. `reference_sources.json` records paths, hashes and seeds. Bone and Pelt are from `2026-09-26_18-51-22_286288_wolfcorev3`; Frizzed is from the September 25 `WolfCore` run. The user's “Blood and Iron” reference corresponds to the saved title Bone and Iron. No namesake from a different generation is substituted.

The comparison is of prompts and the user's descriptions, not an audition of the audio. Frizzed combines urgent physical action, a wolf-specific humiliation, a compact emotional chorus and an earned comic consequence. Bone has the hard supersaw/double-kick entrance, clipped female/male calls, rapid technical sections, a breathless sensory interlude and symphonic escalation. Pelt makes the costs and pleasures of changing bodies drive the plot. Their common anchors are 155–168 BPM electronicore/metalcore, rapid kicks, seven-string chugs, supersaw, compact sung phrases, a female lead, sparse male responses and soaring female hooks.

The prompt keeps those anchors. It asks for mostly intelligible, urgent female verses, brief male support, a concentrated screaming breakdown and a **clear female phrase climbing into a metal belt ending in a short gritty yell**. It avoids replacing the whole successful style with a wall of clean/power-metal adjectives. `[Intro]`, `[Pre-Chorus]`, `[Chorus]`, the trap-metal shift, sensory interlude, symphonic bridge, electronicore breakdown, guitar/synth break and final chorus are emphasized. A resolved `[Outro]` ends with `[End]`. These are generation cues, not guarantees of BPM, vocal delivery or an exact audio ending.

Ten briefs cover breakaway heirloom jewelry; zombie contamination behind the ears; a witch counterfeiting a mother's scent; scentless skeletons with ankle bells; a whistle painful to wolf ears; a forged lover's letter; an obedience-knot grooming collar; alternating human/wolf footprints; clove flour sabotaging a wolf's nose; and a hot latch requiring human fingers. Each variant receives a different narrative angle and the titles/choruses already accepted for that concept, to avoid repeatedly writing the same resolution.

## Sampling and seeds

The existing installation is `/path/to/wolf-music-studio/gemma` (not `gemma412B`). It is Gemma 4 12B IT Q6_K on llama.cpp/Vulkan. The harness starts one slot, 12,288 context tokens, 16 GPU layers, Q8 KV caches and six CPU threads. Thinking is disabled.

Sampling follows the [official Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4): temperature **1.0**, top-p **0.95**, top-k **64**. Min-p is zero, extra repetition/Dry penalties are disabled, and maximum output is 3,500 tokens. After the initial unconstrained pilot demonstrated malformed escapes, plain JSON-object grammar was enabled. It constrains JSON syntax, not lyric choices or a large Yue2-specific output schema. Actual pipeline validation still checks every draft and final config.

### Shared prompt cache

The system prompt and three full examples have identical chat formatting for every draft. Requests explicitly use `cache_prompt: true` and `id_slot: 0`. A one-token warmup evaluates the invariant prefix once per server session. The model stays loaded for the rest of the campaign; llama.cpp retains its live KV state and internal attention checkpoints, rewinds to the common user-message boundary, removes previous generated text, and evaluates the changed brief. Neither a previous generated song nor the warmup response becomes part of the next brief.

Gemma4 uses sliding-window attention. The tested slot-file restore API discards the internal rewind checkpoints and therefore triggered complete reprocessing on this model. It is deliberately not used between drafts. A full server restart performs one new warmup; ordinary song transitions do not reload the model or re-evaluate the entire shared prefix. The server keeps 16 context checkpoints and a 512 MiB prompt-cache budget without increasing GPU layer count or enabling large full-window caches on the 4 GB GPU. Warmup provenance is saved under `kv_cache/`; the old experimental `.bin` is unused.

Tokenizing each request is cheap and still happens; caching avoids the expensive neural evaluation of the shared prefix. The changing song brief and any small boundary replay need fresh evaluation. Generation `timings` record `cache_n`, `prompt_n` and `prompt_ms`, and the writer log prints them. Behavior and APIs are documented in the [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

Gemma writing seeds derive deterministically from campaign base seed, idea, variation and attempt. Yue2 audio seeds have a separate ten-seed cycle starting with 85300, 85301 and 85303. New writing attempts change the Gemma seed. Each concept's ten scheduled variants receive ten distinct audio seeds. The dominant musical reference cycles with extra weight on Frizzed and Bone. Inspect `request.json` and `generation.json` under each attempt for exact settings.

## Running and reviewing

```bash
# New campaign, 100 requested accepted slots, automatically enqueue accepted songs:
python gemma_songwriter.py --campaign songwriter/runs/myNextWolfCampaign

# Existing campaign resumes from its saved manifest and frozen prompt/ideas/examples:
python gemma_songwriter.py --campaign songwriter/runs/myNextWolfCampaign

# Just inspect progress:
python gemma_songwriter.py --campaign songwriter/runs/gemmaWolf_20261002 --status

# Retry slots that exhausted their four attempts, preserving the old drafts:
python gemma_songwriter.py --campaign songwriter/runs/myNextWolfCampaign --retry-exhausted

# Independently validate/compare a directory of configs; do not enqueue:
python checkSongPrompts.py ./newSongs --output ./songReview

# Same check, then submit accepted songs together:
python checkSongPrompts.py ./newSongs --output ./songReview --queue-name reviewedWolfSongs
```

Use a new output review directory for each independent check. Config publication refuses to overwrite existing files. The song checker accepts a JSON file or a directory of JSON files. Draft text and raw events are retained even when a song is rejected. `invalid/` holds failure reports pointing to those raw drafts. `tooSimilar/` holds the actual valid song JSON and an adjacent report. `reviews/` holds reports for accepted songs; `submissions/` holds exact configs sent to the queue.

The corpus includes requests, completed/queued/cancelled configs, immutable run snapshots, the global prompt registry and accepted songwriting campaigns. It deduplicates identical lyrics across different seeds. Comparisons exclude section tags and musical directions, normalize punctuation/contractions, and use unique 3–5-word phrases, rare-phrase weighting, meaningful complete lines, long shared spans and vocabulary overlap. Eight-word shared lyric spans and meaningful copied lines of at least six words trigger review. Numerous shared phrases and collage borrowing across multiple songs also trigger review. A common short expression by itself is allowed, and repeating a chorus inside its own song is allowed. Each rejection explains the matching source, phrases and coverage. This is a borrowing filter, not a semantic plagiarism detector or a score for emotional/musical quality.

Mechanical quality checks require approximately example-length lyrics (250–650 sung words), the main musical sections, the clear-to-belt cue, a compact fast-kick/synth/female style and `[End]`. Four attempts per slot are allowed; exhausted slots are retained and reported while other concepts continue. Accepted text still needs your listening review after generation.

## Hardware turn-taking and background services

Gemma and the visualizer share a cross-process FIFO lease in `queue/local_compute`. The entire songwriting campaign is one Gemma turn; a complete karaoke/render job is one visualizer turn. Gemma stays loaded throughout the campaign, including validation and retries, and shuts down on completion, explicit service stop, or a fatal error. The running job is never preempted to make room for the other. A waiting visualizer starts after the campaign releases the model and hardware lease. Waiting visualizer jobs do not count as active renders. Legacy running Gemma/V6/V7 commands are also detected and allowed to finish. The child inherits the hardware lock if the controller dies.

`startGemma.sh` and `stopGemma.sh` now use this scoped controller; start waits for its turn. Stop addresses this project's model/controller rather than killing every llama-server. A manually started server holds its turn until you stop it. To share the machine fairly during an unattended campaign, allow the harness to manage Gemma rather than leaving a manual server running.

The visualizer queue launches **`/path/to/wolf-music-studio/visualizer/vis2GPUV8.py`**, a lifecycle wrapper around V7's existing FLAC/karaoke rendering. `vis/main.py` points to V8. V6/V7 remain available, and the wrapper does not invalidate completed V7 videos. Use V8 or `main.py` for new manual renders so they take the same lease. The existing automatic download → strict-trimmed FLAC → registry update → karaoke/MP4 workflow is retained.

```bash
# Live writer log, also available in the journal:
tail -F songwriter/runs/gemmaWolf_20261002/pipeline.log

# Watch songwriting, Colab generation/downloads and local video processing:
journalctl --user -f -u yue2-songwriter.service -u yue2-queue.service -u yue2-visualizer.service

# Model loading/token progress:
tail -F /path/to/wolf-music-studio/gemma/gemma_server.log

# Which local job owns the hardware, and who is waiting:
python local_compute.py

# Services/status and available visualizer jobs:
systemctl --user status yue2-songwriter.service yue2-queue.service yue2-visualizer.service
python visualizer_queue.py --status

# Stop/resume songwriting without cancelling previously submitted Yue2 configs:
systemctl --user stop yue2-songwriter.service
systemctl --user start yue2-songwriter.service
```

The service automatically resumes this campaign after an unexpected failure and starts on user-service startup. Manifests record submission intentions before publication; restart recovers configs moved into running/done queue folders without enqueueing duplicates. Campaign prompt assets are frozen and hashed, so changing the source prompt only affects a new campaign. The current V7 music job remains ahead of the newly submitted Gemma configs. Previously every draft reloaded the model and evaluated about 5,000 shared prompt tokens, taking roughly 11–13 minutes. The resident campaign and cached prefix remove that repeated work; the remaining cost is the changing brief and song generation. This is still a substantial background campaign.
