# Changelog

## 0.2.0

Makes one generation per input song the explicit default and adds opt-in `--seedSweep N`/`--seed-sweep N` for new runs and directory-queue jobs. Sweeps preserve the original seed and create deterministic additional seeds/track IDs; saved runs retain their existing requests when resumed. Documents the distinction between new lyric variations and audio seed sweeps.

Fresh-install testing adds missing SPIR-V headers/OpenSSL build prerequisites to the system package list and installs the visualizer test runner when `--dev` is selected.

## 0.1.1

Adds the missing EGL/OpenGL system dependencies to fresh-machine instructions and configures software headless rendering in CI. Controller/inference jobs passed before this correction; the visualizer job now tests its rendering contracts with Mesa.

## 0.1.0

Initial integrated release of YuE2 music generation, Gemma songwriting, prompt validation/similarity review, verified downloads, strict audio trimming, the searchable/favorites track library, Essentia tagging, and the karaoke visualizer.

Adds a portable top-level installer/launcher, separate controller and visualizer environments, configurable Drive and hardware paths, generated user services, version/listening comparison reports, source provenance, documentation, publication checks, and CI contracts. Original live installations are retained separately.
