# RunPod pod: Hermes context-window ceiling — diagnosis + fix

**Status: diagnosed with real data, fix ready, NOT yet applied/verified.**
Written up now while the reasoning's fresh; whoever runs the fix should
update this file with the actual before/after result.

## The problem

`hermes-offsec` (Hermes Agent pointed at a RunPod-hosted Ollama instance)
displays a 262k max context but practically runs out of usable context
around **~100k tokens**, forcing a manual compact-flush-restart cycle that
burns ~62k tokens and costs ~10 minutes each time it happens.

## Root cause — confirmed with real numbers, not guessed

Pod: RunPod community-cloud instance, RTX 5000 Ada Generation, 32760 MiB
total VRAM (pod ID / SSH connection details kept locally, not published here).

```
$ nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free --format=csv
NVIDIA RTX 5000 Ada Generation, 32760 MiB, 26546 MiB, 5686 MiB
```

The loaded model (`hf.co/mradermacher/Huihui-Qwen3.6-27B-abliterated-GGUF:Q4_K_M`,
26.9B params) already uses 26546 MiB, leaving only **5686 MiB (~5.5GB)
free**.

Pod's Ollama config (`/etc/supervisor/conf.d/ollama.conf`):
```
OLLAMA_CONTEXT_LENGTH="262144"
OLLAMA_FLASH_ATTENTION="1"
OLLAMA_KV_CACHE_TYPE="q8_0"
```
Already using flash attention and an already-quantized (q8_0) KV cache, so
neither of those is the lever left to pull.

**The actual number that's wrong:** `OLLAMA_CONTEXT_LENGTH` was set to the
model's full advertised max (262144) without checking it against what this
specific pod's free VRAM can actually back. A full 262144-token KV cache at
q8_0 for this model (64 layers, 4 KV heads) needs an estimated ~8.6GB —
model weights (~17.8GB) + that ≈ 26.4GB, which lines up almost exactly with
the observed 26546 MiB already in use. That means the realistic ~100k
practical wall is **more likely a compute/activation-buffer spike during
large-prompt prefill** eating into the thin 5.5GB margin, not raw KV storage
running out outright — worth confirming against the actual Ollama server
log (`/workspace/ollama_serve.log` on the pod) before treating this
estimate as final; that log wasn't readable in the session this was
diagnosed in (unrelated tooling issue, not a pod problem).

## The fix (not yet run)

```bash
ssh -p <port> root@<pod-ip>   # see local connection notes, not published here

cp /etc/supervisor/conf.d/ollama.conf /etc/supervisor/conf.d/ollama.conf.bak-$(date +%Y%m%d-%H%M%S)
sed -i 's/OLLAMA_CONTEXT_LENGTH="262144"/OLLAMA_CONTEXT_LENGTH="131072"/' /etc/supervisor/conf.d/ollama.conf
supervisorctl restart ollama

# then verify:
nvidia-smi --query-gpu=memory.used,memory.free --format=csv
```

**Target: 131072** — half the model's native max. Reasoning: should drop
total VRAM use from ~26.5GB to ~22GB, nearly doubling free headroom
(5.5GB → ~10.6GB) for compute-buffer spikes, while still giving 4x+ the
current practical ceiling.

**Caution:** this restarts Ollama, which evicts the currently-loaded model
from VRAM. Anything actively using the pod at that moment gets interrupted
— check nothing's mid-session first.

## Next step

Run the fix above, then re-check the practical context ceiling in a real
hermes-offsec conversation (does it now comfortably clear ~100k, and where
does the new wall actually land?). Update this README with the real
before/after numbers once verified — right now this is a diagnosis with an
untested prescription, not a confirmed fix.
