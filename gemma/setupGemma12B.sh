#!/usr/bin/env bash
# Reproducible source build/download only; never autotune, kill a server or run a model.
set -euo pipefail
gemma_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
llama_revision=0c1e57098bba43ac29e6e3b677cdceebdd22334f
build=0
download=0
if [[ $# == 0 ]]; then build=1; download=1; fi
for option in "$@"; do
  case "$option" in
    --build) build=1;;
    --download-model) download=1;;
    --help) echo 'Usage: setupGemma12B.sh [--build] [--download-model]'; exit 0;;
    *) echo "Unknown option: $option" >&2; exit 2;;
  esac
done
if [[ $build == 1 ]]; then
  if [[ ! -d "$gemma_root/llama.cpp/.git" ]]; then
    git clone --filter=blob:none --no-checkout https://github.com/ggml-org/llama.cpp.git "$gemma_root/llama.cpp"
  fi
  git -C "$gemma_root/llama.cpp" fetch --depth 1 origin "$llama_revision"
  git -C "$gemma_root/llama.cpp" checkout --detach "$llama_revision"
  cmake -S "$gemma_root/llama.cpp" -B "$gemma_root/llama.cpp/build" -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
  cmake --build "$gemma_root/llama.cpp/build" --config Release -j "${BUILD_JOBS:-4}" --target llama-server llama-cli
fi
if [[ $download == 1 ]]; then
  mkdir -p "$gemma_root/models"
  model_path="$gemma_root/models/gemma-4-12B-it-Q6_K.gguf"
  if [[ ! -s "$model_path" ]]; then
    model_url="${GEMMA_MODEL_URL:-https://huggingface.co/bartowski/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12B-it-Q6_K.gguf}"
    curl -L --fail --retry 3 -o "$model_path.partial" "$model_url"
    if [[ -n "${GEMMA_MODEL_SHA256:-}" ]]; then
      printf '%s  %s\n' "$GEMMA_MODEL_SHA256" "$model_path.partial" | sha256sum --check -
    fi
    mv "$model_path.partial" "$model_path"
  fi
fi
