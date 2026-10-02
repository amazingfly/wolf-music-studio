#!/usr/bin/env bash

MODEL_PATH="./models/gemma-4-12B-it-Q6_K.gguf"
if [ ! -f "$MODEL_PATH" ] && [ -f "./models/google_gemma-3-12b-it-Q6_K.gguf" ]; then
    MODEL_PATH="./models/google_gemma-3-12b-it-Q6_K.gguf"
fi

LLAMA_BIN="./llama.cpp/build/bin/llama-cli"
[ ! -f "$LLAMA_BIN" ] && LLAMA_BIN="./llama.cpp/llama-cli"

CTX_SIZE=${1:-16384}

echo "===================================================="
echo " 🔍 Gemma VRAM & Maximum Offload Layer Calculator"
echo "===================================================="

# Read VRAM from sysfs
if [ -f "/sys/class/drm/card0/device/mem_info_vram_total" ]; then
    VRAM_TOTAL_MB=$(( $(cat /sys/class/drm/card0/device/mem_info_vram_total) / 1024 / 1024 ))
    VRAM_USED_MB=$(( $(cat /sys/class/drm/card0/device/mem_info_vram_used) / 1024 / 1024 ))
    VRAM_FREE_MB=$(( VRAM_TOTAL_MB - VRAM_USED_MB ))
    echo "📊 System VRAM Status:"
    echo "   Total: $VRAM_TOTAL_MB MB | Used: $VRAM_USED_MB MB | Free: $VRAM_FREE_MB MB"
else
    VRAM_FREE_MB=3400
    echo "⚠️ Could not read sysfs VRAM. Assuming standard ~3400 MB free headroom."
fi

echo "----------------------------------------------------"
echo "Probing layer allocations at $CTX_SIZE context window..."

# Probe with 1 layer to get baseline Vulkan graph overhead
PROBE_LOG=$(mktemp)
$LLAMA_BIN -m "$MODEL_PATH" -ngl 1 -c $CTX_SIZE -ctk q8_0 -ctv q8_0 -n 1 -p "Test" 2>&1 > "$PROBE_LOG"

# Extract memory allocation statistics from llama.cpp logs
echo "----------------------------------------------------"
grep -i -E "vulkan|llama_kv_cache_init|offload|graph reserve" "$PROBE_LOG" | head -n 10 || true
rm -f "$PROBE_LOG"

echo "----------------------------------------------------"
echo "🧪 Running Step Probing (Testing NGL from 14 down to 0)..."

STABLE_NGL=0
for ngl in {14..0..-1}; do
    echo -n "Testing -ngl $ngl ... "
    TEST_LOG=$(mktemp)

    if $LLAMA_BIN -m "$MODEL_PATH" -ngl $ngl -c $CTX_SIZE -ctk q8_0 -ctv q8_0 -n 1 -p "Test" 2>&1 > "$TEST_LOG" | grep -qi "out of memory\|VK_ERROR_OUT_OF_DEVICE_MEMORY\|failed to allocate"; then
        echo "❌ OOM"
    else
        echo "✅ OK!"
        STABLE_NGL=$ngl
        rm -f "$TEST_LOG"
        break
    fi
    rm -f "$TEST_LOG"
done

echo "===================================================="
echo "🎯 OPTIMAL CONFIGURATION FOR YOUR RX 480:"
echo "   --ngl $STABLE_NGL"
echo "   --ctx $CTX_SIZE"
echo "   --ctk q8_0 --ctv q8_0"
echo "===================================================="
