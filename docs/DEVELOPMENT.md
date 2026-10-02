# Development and publication

## Testing

Controller contracts run without song weights, Colab credentials or GPU allocation:

```bash
./studio setup --profile controller --dev
.venv/bin/python -m pytest -q tests yue2/tests/test_archive_run.py \
  yue2/tests/test_auto_trim.py yue2/tests/test_backup_snapshot.py \
  yue2/tests/test_gemma_prompt_cache.py yue2/tests/test_local_compute.py \
  yue2/tests/test_make_config.py yue2/tests/test_partial_download.py \
  yue2/tests/test_process_tracks.py yue2/tests/test_queue_supervisor.py \
  yue2/tests/test_run_config.py yue2/tests/test_songwriter.py \
  yue2/tests/test_songwriter_v2.py yue2/tests/test_track_library.py \
  yue2/tests/test_visualizer_queue.py
```

For the complete upstream/local inference contract suite, install `yue2[test]` into a separate development environment with CPU PyTorch 2.10. Use `PYTHONPATH=yue2/src` so package subprocesses resolve the copied source, then run `pytest -c yue2/pyproject.toml yue2/tests`. GPU/large-model checks are explicitly skipped where not available. The copied progress-test fixtures were updated for the already-present query-tiling argument; the production tiling implementation is unchanged.

Visualizer contracts use its own environment and import root:

```bash
./studio setup --profile visualizer --dev
PYTHONPATH=visualizer visualizer/.venv/bin/python -m pytest -q visualizer/tests
```

`--dev` installs pytest into the selected environment. CI runs controller and inference contracts plus visualizer contracts in separate jobs. Test credentials are mocked; CI never requests a Colab VM or loads the large local language model.

## Source provenance

`sources.json` records the checked-out upstream YuE2, llama.cpp and whisper.cpp revisions. The YuE2 tree includes the local T4 memory adaptations; it is a source snapshot, not an unchanged upstream release. The source licenses/notices are preserved. Inference-engine trees and weights are downloaded, not vendored. Static reference/historical prompt configs are included because they make the musical recipe and similarity checking reproducible.

## Avoid runtime data in Git

The root/component ignore files exclude credentials, account state, models, compiled engines, environments, logs, active queues, generated media, registries, SQLite data, campaign results and browser reports. The sole source exception inside an outputs directory is `yue2/outputs/trackTags.py`.

Before every publication:

```bash
git status --short
python3 scripts/check_publish.py
```

The publication check inspects tracked files for credentials, runtime/model artifacts and excessive size; it reports filenames/rules rather than printing sensitive content. It is a practical check, not a substitute for reviewing new files. Keep account-specific settings in ignored `local/` and set up secrets directly on each machine.

The monorepo has its own history and remote. Original local repositories and their upstream origins are retained in place. Do not force-push over the upstream YuE repository, commit a model merely because it fits locally, or copy an active pipeline's queue/state wholesale into source control.
