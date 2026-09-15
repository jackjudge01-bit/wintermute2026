#!/usr/bin/env python3
"""oh_bisect2.py — sampler-parameter INTERACTION probe for llama.cpp servers.

Target: same as oh_bisect.py (localhost llama.cpp, OpenAI API, port+key
auto-discovered from the process list). Where oh_bisect.py tests each field
alone, this one tests combinations — because some samplers only error when
their companion fields are present or missing (DRY and XTC are the usual
suspects).

Findings this script produced on the original box:
  - the full sampler set (incl DRY+XTC) is rejected as a whole
  - DRY alone fails unless dry_allowed_length >= 2 is set alongside it
  - XTC alone is fine

Usage:
    # with a llama.cpp server already serving a model:
    python3 oh_bisect2.py
"""

import json, urllib.request, re, subprocess

# --- discover port + api key from the running server's cmdline --------------
ps = subprocess.run(['ps', '-eo', 'cmd'], capture_output=True, text=True).stdout
line = next(l for l in ps.splitlines() if 'llama-server' in l and 'OpenHermes' in l)
port = re.search(r'--port (\d+)', line).group(1)
key = re.search(r'--api-key (\S+)', line).group(1)
URL = f"http://127.0.0.1:{port}/v1/chat/completions"

def call(payload, label):
    """POST one chat completion; report label + HTTP status (or error)."""
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
        headers={'Authorization': f'Bearer {key}',
                 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return f"{label:44s} 200 OK"
    except urllib.error.HTTPError as e:
        return f"{label:44s} {e.code}: {e.read().decode()[:130]}"
    except Exception as e:
        return f"{label:44s} ERR {str(e)[:90]}"

MODEL = "/path/to/model.gguf"
msgs = [{"role": "user", "content": "Say hi in one word."}]

# --- the everything-at-once payload a typical client might send --------------
full = {
    "temperature": 0.8, "top_p": 0.95, "top_k": 40, "min_p": 0.05,
    "repeat_penalty": 1.1, "penalty_last_n": 64,
    "presence_penalty": 0.0, "frequency_penalty": 0.0,
    "typical_p": 1.0, "tfs_z": 1.0,
    "mirostat": 0, "mirostat_tau": 5.0, "mirostat_eta": 0.1,
    "seed": -1,
    "dry_multiplier": 0.8, "dry_base": 1.75, "dry_allowed_length": 2,
    "dry_penalty_last_n": -1,
    "xtc_threshold": 0.1, "xtc_probability": 0.1,
}
print(call({"model": MODEL, "messages": msgs, "max_tokens": 8, **full},
           "full set incl DRY+XTC"))

# --- isolate the usual suspects ----------------------------------------------
# XTC alone (threshold + probability travel together)
print(call({"model": MODEL, "messages": msgs, "max_tokens": 8,
            "xtc_threshold": 0.1, "xtc_probability": 0.1}, "XTC alone"))
# DRY alone — note dry_allowed_length=2, below 2 it hard-errors
print(call({"model": MODEL, "messages": msgs, "max_tokens": 8,
            "dry_multiplier": 0.8, "dry_base": 1.75, "dry_allowed_length": 2,
            "dry_penalty_last_n": -1}, "DRY set alone"))

# --- common client default shapes ---------------------------------------------
# LM Studio commonly sends 'min_p' with top_k/top_p and a tiny temperature
print(call({"model": MODEL, "messages": msgs, "max_tokens": 8,
            "temperature": 0.7, "top_p": 0.9, "top_k": 100, "min_p": 0.0},
           "top_k=100,min_p=0"))
# edge values
print(call({"model": MODEL, "messages": msgs, "max_tokens": 8,
            "temperature": 2.0}, "temperature=2.0"))
print(call({"model": MODEL, "messages": msgs, "max_tokens": 8,
            "top_p": 0.999, "top_k": 200, "min_p": 0.1}, "min_p=0.1"))
