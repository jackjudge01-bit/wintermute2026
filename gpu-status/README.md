# gpu-status — catching VRAM contention that no single tool will tell you about

Running Ollama, LM Studio, and an agent harness (CAI) side by side on one
GPU surfaced three failure modes that are invisible if you only check one
tool at a time:

## 1. Silent competition for VRAM

LM Studio can hold a model resident in VRAM in the background — no
terminal, no obvious indicator — while you're debugging why a *separate*
Ollama model is running slower than expected. `nvidia-smi` shows the real
total; `ollama ps` only shows Ollama's own view. Neither alone tells you
who else is on the card.

## 2. A "stopped" model that's still running

`ollama stop <model>` reports success, and `ollama ps` shows nothing
loaded — but if the underlying `llama-server` process was mid-generation
when the stop was requested, it keeps running (and keeps consuming VRAM)
past the point where Ollama's own bookkeeping already forgot about it. A
subsequent request for a *different* model then spawns an *additional*
process instead of cleanly replacing the first, and you get two processes
competing for the same GPU. `ollama ps` alone will not show you this —
you have to check `ps aux | grep llama-server` against it.

## 3. A stale CPU/GPU offload decision

Ollama decides how to split a model's layers between GPU and CPU **at
load time**, based on how much VRAM is free *right then*. If something
else was hogging VRAM when a model loaded, it gets pushed heavily onto
CPU — and it does **not** re-evaluate that split later, even after the
other process is gone and the VRAM is free again. The model looks
"slow" or "broken" when it's actually just running on a stale decision
from before the GPU was cleared. The fix is to stop and reload it, not
to debug the model itself.

## `gpu-status.sh`

Runs `nvidia-smi` (overall + per-process), `ollama ps`, and `lms ps`
together, and specifically flags:
- an `llama-server` process alive while `ollama ps` shows nothing loaded
  (case 2)
- a loaded model reporting heavy CPU% offload while a lot of VRAM sits
  free (case 3)

It doesn't fix anything automatically — VRAM state affecting other active
work isn't something to silently touch — it tells you what's actually
going on and gives the specific commands to fix each case.
