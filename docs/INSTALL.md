# Installation and configuration

## Environments

Use Python 3.12 and Linux. Check `python3 --version`; if your default differs, invoke the installer with `STUDIO_BOOTSTRAP_PYTHON=python3.12 ./studio setup ...`. Controller transitive dependency versions are constrained by the tested `locks/controller.txt` snapshot. The controller only needs NumPy, SciPy, SoundFile and the Colab CLI. Local orchestration does not install the 3B YuE2 weights or require CUDA; the remote Colab worker installs the pinned inference dependencies itself.

The visualizer has a **separate** environment: PyTorch/torchaudio 2.8 preserve the CPU forced-alignment API that newer torchaudio removes. The installer uses CPU wheels for separation/alignment, ModernGL for rendering, Vulkan whisper.cpp for recognition, and VAAPI where available for encoding. Do not install visualizer requirements into the Colab inference environment, which uses PyTorch 2.10.

```bash
./studio setup --profile controller --dev
./studio setup --profile visualizer
./studio setup --profile all --engines --models --tags
```

The last command includes optional tagging. Essentia TensorFlow wheel availability varies by platform; omit `--tags` if the wheel cannot install, while retaining trim/library/visualizer functionality. Without tagging, existing tags are still imported into the library.

System dependencies are listed in the main README. `./studio doctor` reports missing dependencies without loading a model, rendering media, requesting Colab or reading credentials into its output. A controller-only install legitimately reports missing visualizer/model prerequisites. Gemma/Whisper builds are pinned to the commits in `sources.json`; cached external checkouts and build products are ignored by Git.

## Settings

`./studio init` creates `local/settings.json` and empty queue directories without replacing existing settings. Paths in settings are relative to the checkout or absolute; `~` expands to your home directory. The file is ignored by Git.

| Setting | Purpose |
| --- | --- |
| `drive_base` | rclone remote and subfolder, e.g. `gDrive:yue2/` |
| `controller_python` | controller interpreter, normally `.venv/bin/python` |
| `visualizer_python` | separate karaoke interpreter |
| `colab_python` | interpreter with `colab_cli` for tunnel keepalive |
| `gemma_root`, `visualizer_root` | source/engine directories |
| `compute_root` | shared FIFO lease directory, default under `~/.cache` |
| `campaign` | persistent service campaign name; defaults to `studio_v2` |
| `rclone_config` | local rclone config path; never copied into Git |
| `gemma_gpu_layers` | local offload layers, default `16`; `0` permits CPU operation |
| `gemma_context` | context length, default `12288`; minimum supported by this setup is `8192` |

The launcher exports these settings for every component. Raw component scripts accept the equivalent `YUE2_*` variables. Drive paths are saved in each run manifest and shipped with the remote kit, preserving the selected destination across resumes. Changing settings affects future processes, so stop at a draft boundary before restarting a resident songwriter.

## Google Drive and Colab

Create your own rclone remote with `rclone config`. Set `drive_base` to that remote plus a dedicated folder. Ensure the Colab OAuth client/session credentials and rclone account refer to accounts you intend to use. The Colab CLI expects an installed-app OAuth client JSON; supply it using the CLI's documented option, keeping the file outside the checkout or under ignored `local/`.

```bash
.venv/bin/colab --help
.venv/bin/colab --client-oauth-config /path/to/client.json sessions
rclone lsd gDrive:yue2/
```

Do not commit authentication files or export live account configuration into examples. During a generation launch, the controller copies only the selected rclone remote's configuration into a temporary private file and uploads it to your Colab session. That is needed for direct backup/restore and is not part of the repository. Confirm you trust the Colab session/account before launching.

The queue requests a T4 only for pending work and releases its session when idle. Allocation failures are retried. A verified complete run is downloaded with checksum/duration checks and then its corresponding **remote run folder is removed**; local audio, plans, latents and receipts remain. Use a dedicated Drive folder and retain local backups.

Token cycling is off. Optional custom token cycling requires an explicitly supplied `YUE2_TOKEN_CYCLE_SCRIPT`; no unrelated video-project scripts are bundled or assumed.

## Models and hardware

The tested local model is `gemma-4-12B-it-Q6_K.gguf`. **Q6_K refers to the weights; q8_0 refers to K/V caches.** The tested Radeon RX480 profile uses 16 offloaded layers, a 12,288-token context and q8_0 K/V. Adjust layer count for your own VRAM; desktop/GPU use affects available memory. The reference/examples prefix plus output needs a substantial context. The runtime keeps one llama.cpp slot, warms the shared prefix once and uses live rewind checkpoints rather than restoring serialized slots between songs.

`gemma/setupGemma12B.sh --build` builds pinned llama.cpp without starting it or running OOM probes. `--download-model` downloads the GGUF. Set `GEMMA_MODEL_URL` for a mirror/pinned revision, and optionally `GEMMA_MODEL_SHA256` for an integrity check before publishing the downloaded file. The default weight URL uses the upstream repository's main revision; the source-engine commits are pinned, the weight download revision is not. You may copy your existing GGUF into `gemma/models` instead.

`visualizer/scripts/setup_karaoke.sh --build-only` builds pinned whisper.cpp. `--models-only` downloads Whisper large-v3 q5_0 and the CTC alignment weights. Demucs fetches its weights on first use. Models follow their own upstream licenses; obtain any required access/terms directly from their providers.

Headless machines also need the EGL/OpenGL runtime libraries: `libegl1`, `libgl1` and `libgl1-mesa-dri` on Ubuntu. For a software-rendered headless preview/test, use `LIBGL_ALWAYS_SOFTWARE=1 EGL_PLATFORM=surfaceless`; this is the CI configuration.

For VAAPI/ModernGL, verify your user can access `/dev/dri/renderD*`, the Mesa drivers are installed, and a working EGL/OpenGL context can be created. The visualizer can fall back to software H.264 encoding; changing render-device paths happens in `visualizer/config.json`. Recognition/alignment of harsh singing needs human review.

## Services

```bash
./studio services install
./studio services start
./studio services status
```

Install renders units into `~/.config/systemd/user` and reloads systemd, but does not start anything. Start checks component prerequisites. `--only queue`, `--only visualizer`, `--only songwriter`, or a comma-separated selection starts only those components. `report` controls the comparison-refresh timer. Startup settings are read from the local JSON each time a worker launches.

To keep user services running after logout, optionally enable lingering using `loginctl enable-linger "$USER"` according to your system's policy. Do not enable a new installation alongside the legacy installation when both manage the same tracks/account.
