# INSTALL — ollama-benchmarks

How to get the six benchmark/probe scripts running. The findings they
produced and how to read the results are in [README.md](README.md).

## Prerequisites

- Linux/macOS, Python 3.6+ — **stdlib only** (urllib), no pip packages
- Network access to `translate.googleapis.com` for the two translate
  probes
- A local model server, depending on which scripts you run:

| Script | Server needed |
|---|---|
| `think_test.py`, `prefill_bench.py` | Ollama on `127.0.0.1:11434` |
| `oh_bisect.py`, `oh_bisect2.py` | any llama.cpp server speaking the OpenAI API (e.g. LM Studio's `llama-server`) |
| `gt_test.py`, `multi_q.py` | none |

## Install — Ollama (for think_test.py / prefill_bench.py)

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Then pull the model the scripts default to (or edit the `tag` constant
at the top of each script to point at your model):

```bash
ollama pull hf.co/mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF:Q4_K_M
```

Verify:

```bash
ollama list
curl -s http://127.0.0.1:11434/api/tags | head
```

## Install — llama.cpp server (for oh_bisect.py / oh_bisect2.py)

Any OpenAI-compatible llama.cpp server works, e.g. LM Studio
(https://lmstudio.ai) with the local server started, or a raw
`llama-server` build. The scripts auto-discover the port and API key by
scanning the process list for a `llama-server` process, so the model
being served must appear in the process command line.

**Important:** both bisect scripts match a process line containing
`llama-server` **and** `OpenHermes`. If you serve a differently named
model, either rename/re-serve it so `OpenHermes` appears in the command
line, or edit the match string in the two `next(l for l in ...)` lines.

The server must be launched with `--port <n>` and `--api-key <key>`
visible in its command line (that is where the scripts read them from).

Verification once a server is running:

```bash
ps -eo cmd | grep llama-server
```

You should see your server line with its `--port` and `--api-key`.

## Install — translate probes (gt_test.py / multi_q.py)

Nothing to install. Internet access only.

## Verification / first run

```bash
cd ollama-benchmarks/

# no server needed:
python3 gt_test.py          # should print translations of the CJK/Cyrillic/Japanese samples

# Ollama running with the model pulled:
python3 think_test.py        # three rows: default / think=False / explicit options
python3 prefill_bench.py     # prefill tok/s at num_ctx 4096 / 49152 / 98304

# llama.cpp server running (see note above):
python3 oh_bisect.py         # one HTTP status line per sampler parameter
python3 oh_bisect2.py        # sampler combination tests
```

## Troubleshooting

- `Connection refused` (Ollama scripts) — `ollama serve` not running, or
  it is on a non-default port; scripts hardcode `127.0.0.1:11434`.
- `StopIteration` in the bisect scripts — no `llama-server` process with
  `OpenHermes` in its command line (see the note above).
- HTTP 429 from `gt_test.py` / `multi_q.py` — your IP is rate-limited by
  the gtx endpoint; it lifts eventually, but the probes are for a
  handful of strings, not bulk work.
