#!/usr/bin/env bash
# Cross-checks nvidia-smi against every tool that independently loads models
# onto the GPU (Ollama, LM Studio) to catch contention that none of them
# will tell you about on their own -- see README.md for why each check
# exists.
set -uo pipefail

echo "=== GPU memory ==="
nvidia-smi --query-gpu=memory.used,memory.total,memory.free,utilization.gpu --format=csv,noheader

echo
echo "=== Per-process GPU memory ==="
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null || echo "(none)"

echo
echo "=== Ollama's view (ollama ps) ==="
if command -v ollama >/dev/null; then
    ollama ps
else
    echo "ollama not installed"
fi

echo
echo "=== LM Studio's view (lms ps) ==="
if command -v lms >/dev/null; then
    lms ps
else
    echo "lms not installed"
fi

echo
echo "=== Cross-checks ==="

# Ghost check: an ollama-owned llama-server process running that `ollama ps`
# no longer lists. This happens when `ollama stop` returns success while the
# process is still mid-generation -- it keeps running and serving past the
# point Ollama's own bookkeeping forgot about it.
running_pids=$(pgrep -f "llama-server.*--port" 2>/dev/null || true)
tracked_pids=$(ollama ps 2>/dev/null | tail -n +2 | awk '{print $1}' || true)
if [ -n "$running_pids" ]; then
    for pid in $running_pids; do
        if ! ps -p "$pid" -o cmd= 2>/dev/null | grep -q .; then
            continue
        fi
        # ollama ps doesn't print PIDs directly, so this is a coarse check:
        # any llama-server process running while `ollama ps` prints nothing
        # at all is almost certainly a ghost.
        if [ -z "$(ollama ps 2>/dev/null | tail -n +2)" ]; then
            echo "WARNING: llama-server PID $pid is running but 'ollama ps' shows nothing loaded."
            echo "  -> likely a ghost process from a stop that didn't actually kill it."
            echo "  -> verify with: ps -p $pid -o pid,pcpu,pmem,etime,cmd"
            echo "  -> if confirmed stuck: sudo kill -9 $pid  (it runs as the 'ollama' user)"
        fi
    done
fi

# Stale CPU/GPU split check: if a model shows heavy CPU offload
# (PROCESSOR column like "72%/28% CPU/GPU") while a lot of VRAM is free,
# it likely loaded while something else was hogging VRAM and never
# re-evaluated the split after that freed up. Ollama does not
# auto-rebalance an already-loaded model.
free_mib=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null || echo 0)
if command -v ollama >/dev/null; then
    ollama ps 2>/dev/null | tail -n +2 | while read -r line; do
        if echo "$line" | grep -qE '[0-9]+%/[0-9]+% CPU/GPU'; then
            cpu_pct=$(echo "$line" | grep -oE '[0-9]+%/[0-9]+% CPU/GPU' | grep -oE '^[0-9]+')
            if [ "${cpu_pct:-0}" -gt 40 ] && [ "${free_mib:-0}" -gt 4000 ]; then
                echo "WARNING: a loaded model is ${cpu_pct}% CPU-offloaded despite ${free_mib}MiB VRAM free."
                echo "  -> it likely loaded while VRAM was contended and never rebalanced."
                echo "  -> fix: 'ollama stop <model>', confirm with 'ps aux | grep llama-server'"
                echo "     that the process actually exited (sudo kill -9 <pid> if not), then"
                echo "     re-request the model to force a fresh load with current VRAM."
            fi
        fi
    done
fi

echo "(done)"
