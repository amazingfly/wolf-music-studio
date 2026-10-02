#!/usr/bin/env bash
set -euo pipefail
vis_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
whisper_revision=d09f61a708f3487afa956ff578e60eae5e7a233c
build=1
models=1
case "${1:-}" in
  --build-only) models=0;;
  --models-only) build=0;;
  --help) echo 'Usage: setup_karaoke.sh [--build-only | --models-only]'; exit 0;;
  '') ;;
  *) echo 'Unknown setup option' >&2; exit 2;;
esac
mkdir -p "$vis_root/tools" "$vis_root/models"
if [[ $build == 1 ]]; then
  if [[ ! -d "$vis_root/tools/whisper.cpp/.git" ]]; then
    git clone --filter=blob:none --no-checkout https://github.com/ggml-org/whisper.cpp.git "$vis_root/tools/whisper.cpp"
  fi
  git -C "$vis_root/tools/whisper.cpp" fetch --depth 1 origin "$whisper_revision"
  git -C "$vis_root/tools/whisper.cpp" checkout --detach "$whisper_revision"
  cmake -S "$vis_root/tools/whisper.cpp" -B "$vis_root/tools/whisper.cpp/build" -DCMAKE_BUILD_TYPE=Release -DGGML_VULKAN=ON
  cmake --build "$vis_root/tools/whisper.cpp/build" --config Release -j "${BUILD_JOBS:-4}" --target whisper-cli
fi
if [[ $models == 1 ]]; then
  karaoke_python="${YUE2_VIS_PYTHON:-$vis_root/.venv/bin/python}"
  if [[ ! -x "$karaoke_python" ]]; then echo 'Install the visualizer environment first: ./studio setup --profile visualizer' >&2; exit 1; fi
  model="$vis_root/models/ggml-large-v3-q5_0.bin"
  if [[ ! -s "$model" ]]; then
    curl -L --fail --retry 3 -o "$model.partial" https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-q5_0.bin
    mv "$model.partial" "$model"
  fi
  align_cache="$("$karaoke_python" -c 'import torch; print(torch.hub.get_dir())')/checkpoints"
  mkdir -p "$align_cache"
  align_model=wav2vec2_fairseq_large_ls960_asr_ls960.pth
  if [[ ! -s "$align_cache/$align_model" ]]; then
    curl -L --fail --retry 3 -o "$align_cache/$align_model.partial" "https://download.pytorch.org/torchaudio/models/$align_model"
    mv "$align_cache/$align_model.partial" "$align_cache/$align_model"
  fi
fi
