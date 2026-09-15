# cai-ollama — CAI + local Ollama, the bugs that actually bit

[CAI](https://github.com/aliasrobotics/cai) (Cybersecurity AI) driven by a
local Ollama model kept silently misbehaving in ways that looked like
"the model is bad" but were actually config/plumbing bugs. `cai-run` is the
wrapper that pins the model correctly; the real value here is documenting
*why* it was needed, because none of these failure modes are obvious from
the symptom alone.

## Bug 1 — a model never unloads from VRAM, even idle

Symptom: `ollama ps` shows a model `UNTIL: Forever`, permanently holding
VRAM, starving anything else that wants the GPU.

Root cause: a systemd drop-in (`/etc/systemd/system/ollama.service.d/override.conf`)
had `Environment="OLLAMA_KEEP_ALIVE=-1"` — a global, permanent override
applying to every model any client loads, regardless of that client's own
keep-alive setting.

Fix:
```bash
sudo sed -i 's/OLLAMA_KEEP_ALIVE=-1/OLLAMA_KEEP_ALIVE=5m/' \
  /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart ollama
```
Check with `systemctl show ollama --property=Environment` and `ollama ps`
after a load — `UNTIL` should show a real countdown, not `Forever`.

## Bug 2 — a `.env`-style config file is silently never loaded

Symptom: editing `~/.cai.env` (`CAI_MODEL=...`) has zero effect — CAI keeps
using whatever it used before, no error, no indication the file was ignored.

Root cause: CAI's `cli.py` calls plain `load_dotenv(override=True)` with no
path argument. `python-dotenv`'s default search looks for a file literally
named `.env` (in the cwd or walking up from it) — `.cai.env` never matches
that filename, so the file is never read, ever, regardless of its contents
or how many times you edit it.

Lesson: `load_dotenv()` with no arguments is filename-specific, not "any
dotenv-shaped file." If a project's config file isn't named exactly `.env`,
either rename it or export the variables as real shell environment
variables instead of relying on it being picked up automatically.

## Bug 3 — an already-exported shell variable beats every config file

Symptom: `.cai.env` is fixed (see Bug 2) and even `export CAI_MODEL=...`
directly in the shell doesn't change what CAI actually uses.

Root cause: `/etc/profile.d/cai.sh` — sourced by *every* login shell —
hardcoded `export CAI_MODEL=ollama/qwen2.5-coder:14b` as a real environment
variable. Real exported env vars always win over anything a later
`load_dotenv()` call would set, and any shell already open before you fix
`/etc/profile.d/cai.sh` keeps its stale value until that shell restarts.

Fix: `cai-run` (this dir) sets every relevant variable explicitly
(`CAI_MODEL`, `CAI_AGENT_TYPE`, `CAI_STREAM`, endpoint URLs) immediately
before `exec cai`, in the same process, so it always wins regardless of
what `/etc/profile.d/cai.sh` or any stale parent shell exported earlier.
Also fix the root file itself (`/etc/profile.d/cai.sh`) so new shells get
the right default without needing the wrapper.

## Bug 4 — a "stopped" Ollama model can still be alive and serving requests

Symptom: `ollama stop <model>` reports success, `ollama ps` shows nothing
loaded, but the model is still visibly generating tokens (visible in
`journalctl -u ollama`), and a *new* request for a different model spawns
an *additional* `llama-server` process rather than reusing/replacing it —
you end up with two competing processes and VRAM contention that looks
identical to Bug 1 from the outside.

Root cause: `ollama stop` is a request, not a kill — if the underlying
`llama-server` process is mid-generation, it keeps running (and keeps
serving) past the point where Ollama's own bookkeeping (`ollama ps`) has
already forgotten about it.

Fix: verify with `ps aux | grep llama-server` — if a process is still
alive and burning CPU after `ollama stop` reports success, it needs
`sudo kill -9 <pid>` (it runs as the `ollama` system user, not yours). A
fresh request will spawn a clean instance.

## Bug 5 (not a bug, a real gotcha) — "thinking" models make batch/tool
work unpredictably slow

Both Qwen3 (via CAI's streaming path) and Granite 4.2 (in raw structured-
output tests) generated hundreds of extra "reasoning" tokens before ever
producing the actual answer — for prompts as trivial as "pick 0-3 items
from this list." A 13-second round trip for a one-line JSON answer, fully
GPU-resident, is not a hardware problem, it's the model reasoning out loud
before answering. For high-volume/latency-sensitive work (tool-calling
agents, batch classification), prefer a non-reasoning model or confirm
`"think": false` actually suppresses it for your specific model+client
combination before committing to it at scale.

## `cai-run`

Sets every CAI-relevant env var explicitly and `exec`s `cai`, so none of
the above can silently reassert themselves. See [INSTALL.md](INSTALL.md).
