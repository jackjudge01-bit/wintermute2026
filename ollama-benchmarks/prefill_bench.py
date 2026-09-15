#!/usr/bin/env python3
"""prefill_bench.py — measure how context-window size (num_ctx) affects
prefill (prompt-processing) throughput on a locally-served model.

Target: Ollama /api/generate. Edits needed for other setups: change `tag`
to your model name.

Why this matters: loading a model with a huge context window is not free —
every request pays a prefill tax proportional to the configured context,
even when the actual prompt is short. Before committing a bulk job to a
big-context config, run this to see what it costs you.

Usage:
    ollama pull <model>
    python3 prefill_bench.py
"""

import json, urllib.request, time

# --- config -----------------------------------------------------------------
tag = 'hf.co/mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF:Q4_K_M'
URL = "http://127.0.0.1:11434/api/generate"

# a big prompt (~12k tokens worth of text) to measure prefill on.
# repeated filler works fine: we only care about tokens processed, not meaning
big = ("The quick brown fox jumps over the lazy dog. " * 900)

def run(ctx, prompt, label):
    """One generate request at the given num_ctx. Reports prompt-eval
    (prefill) tok/s, generation tok/s, and wall time, using Ollama's own
    eval counters from the response."""
    payload = {"model": tag, "prompt": prompt, "stream": False,
               "options": {"num_ctx": ctx, "num_predict": 16},
               "keep_alive": 300}
    t0 = time.time()
    try:
        req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=600) as r:
            d = json.loads(r.read().decode())
        el = time.time() - t0
        # Ollama response counters:
        #   prompt_eval_count / prompt_eval_duration -> prefill work
        #   eval_count / eval_duration               -> generation work
        pec = d.get('prompt_eval_count', 0)
        ped = d.get('prompt_eval_duration', 1) / 1e9
        ec = d.get('eval_count', 0)
        ed = d.get('eval_duration', 1) / 1e9
        print(f"ctx={ctx:6d} {label:10s} prefill {pec} tok in {ped:.1f}s = "
              f"{pec / max(ped, 0.01):.0f} tok/s | gen {ec} tok "
              f"{ec / max(ed, 0.01):.0f} tok/s | wall {el:.1f}s")
    except Exception as e:
        print(f"ctx={ctx:6d} {label:10s} ERR {str(e)[:90]} "
              f"(wall {time.time() - t0:.0f}s)")

# 1. tiny prompt, small ctx — baseline: what the model costs at rest
run(4096, "hi", "baseline")
# 2-3. same big prompt at increasing context windows.
# NOTE: the first request at each new num_ctx also reloads/re-KV-caches
# the model, so expect the wall time to include that one-time cost.
run(49152, big, "bigprompt")
run(98304, big, "bigprompt")
