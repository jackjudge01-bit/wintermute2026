#!/usr/bin/env python3
"""oh_bisect.py — find which sampler parameter makes a llama.cpp server
reject your request, by testing each parameter in isolation.

Target: any llama.cpp-based server speaking the OpenAI API on localhost
(LM Studio, llama-server, etc.). The port and API key are auto-discovered
from the running process's command line, so nothing is hardcoded.

Why this matters: llama.cpp accepts many sampler fields but rejects some
combinations and silently ignores unknown ones. When a bulk client (or
LM Studio preset) sends 15 sampler fields and gets a 4xx, bisecting each
field alone is the fastest way to find the culprit.

Usage:
    # with a llama.cpp server already serving a model:
    python3 oh_bisect.py
"""

import json, urllib.request, os, re, subprocess

# --- discover port + api key from the running server's cmdline --------------
ps = subprocess.run(['ps', '-eo', 'cmd'], capture_output=True, text=True).stdout
line = next(l for l in ps.splitlines() if 'llama-server' in l and 'OpenHermes' in l)
port = re.search(r'--port (\d+)', line).group(1)
key = re.search(r'--api-key (\S+)', line).group(1)

URL = f"http://127.0.0.1:{port}/v1/chat/completions"

def call(payload):
    """POST one chat completion; return '200 OK: <snippet>' on success or
    the HTTP error code + body excerpt on failure."""
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
        headers={'Authorization': f'Bearer {key}',
                 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            b = r.read()
            try:
                msg = json.loads(b)['choices'][0]['message']['content'][:60]
            except Exception:
                msg = b[:60]
            return f"200 OK: {msg!r}"
    except urllib.error.HTTPError as e:
        return f"{e.code}: {e.read().decode()[:160]}"
    except Exception as e:
        return f"ERR {str(e)[:100]}"

# --- the minimal request that should always work ----------------------------
base = {"model": "/path/to/model.gguf",
        "messages": [{"role": "user", "content": "Say hi in one word."}],
        "max_tokens": 16}

print("port:", port)
print("1. default ............", call(base))

# --- one sampler parameter per request ---------------------------------------
# if any of these lines comes back non-200, that parameter is the culprit.
# (yes, some of these are no-op values — the point is whether the *field*
# is accepted at all, not whether it changes the output)
tests = {
    "temperature=0.8": {"temperature": 0.8},
    "temperature=0": {"temperature": 0},
    "top_p=0.95": {"top_p": 0.95},
    "top_k=40": {"top_k": 40},
    "min_p=0.05": {"min_p": 0.05},
    "repeat_penalty=1.1": {"repeat_penalty": 1.1},
    "typical_p=0.9": {"typical_p": 0.9},
    "tfs_z=1.0": {"tfs_z": 1.0},
    "penalty_present=0": {"penalty_present": 0},
    "mirostat=0": {"mirostat": 0},
    "seed=42": {"seed": 42},
    "dry_multiplier=0.8": {"dry_multiplier": 0.8},      # DRY: needs
    "xtc_threshold=0.1": {"xtc_threshold": 0.1},        # companions, see
}                                                       # oh_bisect2.py
for name, extra in tests.items():
    p = dict(base)
    p.update(extra)
    print(f"2. {name:22s}", call(p))
