# ollama-benchmarks — why local LLM classification didn't work

Benchmarks and protocol probes run while trying to classify ~70,000 skills
with locally-hosted models (Kali laptop, RTX 5070 Ti 12 GB). The short
version: **every local candidate failed for a measurable, documentable
reason**, which is why the classification job ultimately went to an online
flash-tier API.

Each script is self-contained and re-runnable against a live local server.
They double as templates for benchmarking any local model before you commit
a bulk job to it.

## The findings (what these scripts proved)

1. **Reasoning/thinking models burn your token budget on hidden thinking.**
   Qwen3.5-9B (and granite-4.2-8b before it) emit hundreds of "thinking"
   tokens before the answer, even for a trivial prompt (`think_test.py`).
   At 70k calls that's minutes per call and dollars of pure waste. The
   `think=False` toggle did not reliably suppress it via Ollama's chat API.
2. **Context length silently destroys prefill throughput.** Loading the same
   model with `num_ctx=98304` vs 4096 changes prefill cost dramatically
   (`prefill_bench.py`) — big-context configs are not free, they tax every
   request. Bench before you pick a context window for bulk work.
3. **Sampler parameters can 4xx a llama.cpp server.** LM Studio's
   llama-server accepts many sampler fields but rejects some combinations
   (`oh_bisect.py`, `oh_bisect2.py`): DRY needs `dry_allowed_length >= 2`,
   unknown fields are ignored silently, some values hard-error the request.
   Bisecting each parameter alone is the only reliable way to find which one
   breaks.
4. **Free translation APIs rate-limit hard.** Google's public `gtx` endpoint
   works per-string (`gt_test.py`) and even accepts batched queries
   (`multi_q.py`), but after ~2,300 strings the source IP gets blocked —
   not viable for a 25k-item non-English corpus.

## Scripts

| Script | Target | What it does |
|---|---|---|
| `think_test.py` | Ollama `/api/chat` | Measures thinking-token burn: sends one trivial prompt with default options, `think=False`, and explicit options; reports wall time, output tokens, tok/s, and how many chars went to thinking vs content. |
| `prefill_bench.py` | Ollama `/api/generate` | Prefill throughput vs context length: sends a ~12k-token prompt at `num_ctx` 4096 / 49152 / 98304 and reports prompt-eval tok/s, generation tok/s and wall time from Ollama's own eval counters. |
| `oh_bisect.py` | llama.cpp server (OpenAI API) | Sampler smoke test: finds the live server's port + API key from `ps`, then sends one request per sampler parameter (temperature, top_p, top_k, min_p, repeat_penalty, typical_p, tfs_z, mirostat, seed, DRY, XTC...) each in isolation, printing HTTP status so the offending parameter is obvious. |
| `oh_bisect2.py` | llama.cpp server | Same idea for parameter *combinations*: the full sampler set at once, then XTC alone, DRY alone, common LM Studio defaults, and edge values — to catch interactions rather than single bad fields. |
| `gt_test.py` | Google translate (gtx) | Single-string translation probe: measures latency and correctness for Chinese/Cyrillic/Japanese samples via the free `translate_a/single` endpoint. |
| `multi_q.py` | Google translate (gtx) | Batch probe: repeats `gt_test` with multiple `q=` parameters on one URL to test whether batching is possible (it is, but rate limits still bite at volume). |

## Usage

Each script is designed for a specific live server:

```bash
# Ollama must have the target model pulled
ollama pull hf.co/mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF:Q4_K_M
python3 think_test.py
python3 prefill_bench.py

# LM Studio (or any llama.cpp server) must be serving a model
python3 oh_bisect.py     # auto-discovers port + API key from the process list
python3 oh_bisect2.py

# translation probes need no local server
python3 gt_test.py
python3 multi_q.py
```

Edit the `tag` / `MODEL` constants at the top of each script to point at
whatever model you're benchmarking.

## Reading the results

- `think_test.py`: if `thinking_chars` >> `content_chars` on a trivial
  prompt, the model is unusable for bulk structured work — every call pays
  the thinking tax before producing anything useful
- `prefill_bench.py`: compare prefill tok/s across `num_ctx` values; if
  throughput collapses at high ctx, use the smallest context your task fits
- `oh_bisect*.py`: any non-200 line names the parameter responsible; the
  error body from llama.cpp usually says which field it choked on

## The conclusion these benchmarks drove

Bulk classification of 70k items needs: a non-reasoning (or reliably
reasoning-off) model, a modest context window, and structured JSON output.
No 12-GB-VRAM local configuration delivered all three at acceptable speed,
so the job went to an online flash-tier model (glm-5.3-flash) at ~$0.50 for
the whole corpus — cheaper than the electricity to do it locally.
