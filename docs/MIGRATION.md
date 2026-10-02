# Migrating the existing installation

This repository is a source snapshot assembled from the existing YuE2, visualizer and Gemma directories. Packaging and publication do not move those directories, replace their remotes, change their active services, copy active queue jobs, or stop a running generation. The newly created controller environment is for setup/testing, not a second active pipeline.

## Keep the current pipeline running

The legacy installation remains valid. Use its existing `yue2-*` services and logs until you deliberately switch. Existing campaign files, success metrics, ratings, audio, tags and favorites remain there; Git contains only source, static examples and documentation.

To browse the old output library using the new checkout:

```bash
./studio library --outputs /path/to/legacy/yue2/outputs
```

This rebuilds/updates the catalog for that output root; back up its SQLite catalog first if you want an independent trial. Alternatively copy completed outputs and the catalog into a test directory and point the library there. Record paths in old catalog/JSON/job files are absolute; a copied database is not automatically rewritten for a new location.

## Switch deliberately

1. Back up the original queue, campaign directories, catalog/favorites, JSON database and media. Note the exact legacy Drive remote/root-folder setting.
2. Let the current Gemma draft finish and pause the campaign with its `pause_after_draft` marker, then stop the original songwriter service. Stop the original visualizer and directory watcher after their current work. If abandoning the cloud session, stop it with the original Colab session name.
3. Finish setup/authentication in the new checkout. Set `drive_base` to the same intended remote or a **new dedicated remote folder**. A fresh installation does not resume old jobs by implication.
4. Reuse model files by copying/reflinking existing Gemma and Whisper weights into the new ignored model directories, or download them. Build the pinned engines in the new checkout. The global PyTorch model cache can reuse already downloaded alignment/Demucs weights.
5. Copy only deliberately selected configs to the new pending queue. Do not copy every archived/submitted config into pending, or all those songs will be generated again. Starting with a new campaign name creates separate results and IDs.
6. Install/start `wolfstudio-*` services. Ensure only one pipeline manages the selected remote/run folders.

`run.json` and job/campaign manifests preserve identity, checksums, provenance and absolute paths. To resume old runs in place, keep using the original installation. To migrate unfinished runs, move them only while stopped, inspect and deliberately adapt path-bearing manifests, and test a copy first. Do not perform a bulk search/replace on frozen lyric/config assets or checksum-protected artifacts.

## Existing examples and results

The repository retains historical source configs as examples under `yue2/examples/historical_configs`, alongside the three exact songwriter reference configs. These files are added to the similarity corpus but are never automatically queued. No recording, mutable acceptance/rejection result or personal favorite database is distributed.

For a version comparison against a legacy campaign, `./studio compare --old /path/to/legacy/campaign --new yue2/songwriter/runs/new_campaign` reads its metrics and accepted prompts. To link legacy audio, use `--root /path/to/legacy/yue2`; a combined listening report requires the referenced outputs to be accessible under the selected root. The legacy campaign's baseline snapshot remains unchanged.
