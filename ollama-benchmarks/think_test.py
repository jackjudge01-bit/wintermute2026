#!/usr/bin/env python3
"""think_test.py — measure how many tokens a 'thinking' model wastes
on hidden reasoning before answering a trivial prompt.

Target: Ollama /api/chat with a Qwen3-family (or granite) model that has a
mandatory/automatic thinking mode. Edits needed for other setups: change
`tag` to your model name.

Why this matters: for bulk jobs (classification, tagging, extraction) the
thinking tokens are pure overhead — you pay generation time for all of them
but only use the final content. This script quantifies the tax.

Usage:
    ollama pull <model>   # if not already pulled
    python3 think_test.py
"""

import json, urllib.request, time

# --- config: point at your model -------------------------------------------
tag = 'hf.co/mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF:Q4_K_M'
URL = "http://127.0.0.1:11434/api/chat"
prompt = "whats the weather like today?"   # deliberately trivial: any
                                           # thinking here is waste

def run(extra, label):
    """Send one chat request with optional extra top-level fields
    (e.g. think=False), and report time, tokens/sec, and the split
    between thinking and content characters."""
    msgs = [{"role": "user", "content": prompt}]
    payload = {"model": tag, "messages": msgs, "stream": False,
               "options": {"num_ctx": 49152, "num_predict": 512},
               "keep_alive": 300}
    payload.update(extra)
    t0 = time.time()
    try:
        req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read().decode())
        el = time.time() - t0
        msg = d.get('message', {})
        content = msg.get('content', '')
        thinking = msg.get('thinking') or ''   # Ollama surfaces the hidden
                                                # chain-of-thought here
        print(f"{label:22s} wall {el:6.1f}s | out_tokens {d.get('eval_count')} "
              f"gen {d.get('eval_count', 0) / max(d.get('eval_duration', 1) / 1e9, 0.01):.0f} t/s")
        print(f"   thinking_chars={len(thinking)} content_chars={len(content)}")
        print(f"   content: {content[:150]!r}")
    except Exception as e:
        print(f"{label:22s} ERR {str(e)[:120]} (wall {time.time() - t0:.0f}s)")

# 1. default behaviour — does it think out of the box?
run({}, "default (thinking?)")
# 2. the documented toggle — does it actually suppress thinking?
run({"think": False}, "think=False")
# 3. explicit options — rules out config interference
run({"options": {"num_ctx": 49152, "num_predict": 512, "temperature": 0.7}},
    "explicit opts")
