#!/usr/bin/env bash

MODEL_PATH="./models/gemma-4-12B-it-Q6_K.gguf"
[ ! -f "$MODEL_PATH" ] && MODEL_PATH="./models/google_gemma-3-12b-it-Q6_K.gguf"

LLAMA_BIN="./llama.cpp/build/bin/llama-cli"
[ ! -f "$LLAMA_BIN" ] && LLAMA_BIN="./llama.cpp/llama-cli"

if [ ! -f "$MODEL_PATH" ]; then
    echo "❌ Error: Model file not found."
    exit 1
fi

echo "🔍 Probing GPU layers for PCIe/GTT memory spilling..."
echo "----------------------------------------------------"

# Stop running llama-server if active to ensure clean VRAM readings
if [ -f "stopGemma.sh" ]; then
    ./stopGemma.sh > /dev/null 2>&1
fi

MAX_ZERO_SPILL=0

for ngl in {10..16}; do
    # Run a 1-token test in background
    $LLAMA_BIN -m "$MODEL_PATH" -ngl $ngl -c 8192 -ctk q8_0 -ctv q8_0 -n 1 -p "test" > /dev/null 2>&1 &
    PID=$!

    # Allow llama-cli 2.5 seconds to allocate weights & KV graph
    sleep 2.5

    # Query AMD sysfs memory allocation
    if [ -f "/sys/class/drm/card0/device/mem_info_gtt_used" ]; then
        GTT_BYTES=$(cat /sys/class/drm/card0/device/mem_info_gtt_used 2>/dev/null || echo 0)
        GTT_MB=$(( GTT_BYTES / 1024 / 1024 ))
        VRAM_BYTES=$(cat /sys/class/drm/card0/device/mem_info_vram_used 2>/dev/null || echo 0)
        VRAM_MB=$(( VRAM_BYTES / 1024 / 1024 ))
    else
        GTT_MB=0
        VRAM_MB=0
    fi

    wait $PID 2>/dev/null

    if [ "$GTT_MB" -gt 15 ]; then
        echo "Layer -ngl $ngl: ❌ SPILLING (VRAM: ${VRAM_MB} MB | GTT/PCIe: ${GTT_MB} MB)"
    else
        echo "Layer -ngl $ngl: ✅ ZERO SPILL (VRAM: ${VRAM_MB} MB | GTT/PCIe: ${GTT_MB} MB)"
        MAX_ZERO_SPILL=$ngl
    fi
done

echo "----------------------------------------------------"
echo "🎯 Optimal zero-spill offload limit: -ngl $MAX_ZERO_SPILL"
echo "----------------------------------------------------"
