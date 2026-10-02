# Fresh-install verification

Tested on October 2, 2026 in a separate `wolf-music-studio-install-test` source checkout on the existing Linux host, with new controller and visualizer virtual environments. System tools and account configuration were already present; this was a fresh application installation, not a freshly installed operating system.

## Commands tested

```bash
BUILD_JOBS=2 ./studio setup --profile all --dev --engines --models
./studio setup --profile controller --tags
./studio setup --profile visualizer --dev
./studio doctor
```

The test isolated temporary files and the Torch model cache under ignored `local/`. Setup downloaded the full Gemma Q6_K, Whisper large-v3 q5_0, and CTC alignment models. Their byte counts matched the download endpoints, their file headers were valid, and both pinned Vulkan engines compiled with all executable shared libraries resolving.

Verified:

- Both interpreters remain inside their virtual environments; the base Python packages are unchanged.
- Both environments pass `pip check`; optional Essentia TensorFlow imports and CPU feature extraction work.
- A second full setup with `--dev --tags --engines --models` succeeds and reuses all three model files without changing their modification times.
- All nine top-level tool commands load and expose their help successfully.
- 124 controller/workflow contracts passed, with one intentional skip. After the development-installer correction, 34 setup/seed contracts passed.
- 13 local karaoke/alignment contracts passed. The rendering contract ran in GitHub's software-rendering CI, where all 14 visualizer contracts passed; controller and full inference CI also passed.
- Four generated service units passed systemd validation without installing or starting duplicate workers.
- The installed CLI prepares one generation by default or three with `--seedSweep 3`, rejects invalid counts, and refuses to change seeds on resume.
- The live WolfcoreV7 manifest, source config, and all 24 frozen requests stayed byte-for-byte unchanged. Pending Gemma batches each contain one request per distinct song.

The installation test never started another local language model, requested a Colab allocation, or started the new queue/services. Existing live workers continued running. Test logs and downloaded/generated data remain local and are excluded from Git.

## Setup corrections

The audit added `spirv-headers`, OpenSSL development dependencies, and Wayland/X11 clipboard utilities to the documented system packages. `--dev` now installs pytest into every selected environment, including the visualizer. See [installation](INSTALL.md) for the complete prerequisites and account setup.
