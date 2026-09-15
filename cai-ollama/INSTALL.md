# INSTALL — cai-ollama

## Prerequisites

- [CAI](https://github.com/aliasrobotics/cai) installed in a venv (this
  assumes `~/cai-venv/bin/activate`, edit `cai-run` if yours lives
  elsewhere)
- Ollama running locally (`http://127.0.0.1:11434`) for local-model use,
  and/or an OpenAI-compatible cloud endpoint (the script also wires up
  Venice AI as an example — swap `OPENAI_API_BASE`/`OPENAI_API_KEY` for
  whatever provider you actually use)

## Install

```bash
cp cai-run ~/.local/bin/cai-run
chmod +x ~/.local/bin/cai-run
```

Make sure `~/.local/bin` is on `PATH` (it is by default on most
Debian/Kali setups).

Before using it, check whether your box has any of Bugs 1-3 from
[README.md](README.md):

```bash
# Bug 1: is a model's keep-alive forced to "Forever"?
systemctl show ollama --property=Environment | grep KEEP_ALIVE
# should NOT show OLLAMA_KEEP_ALIVE=-1; fix as described in README if it does

# Bug 3: is CAI_MODEL hardcoded system-wide?
grep CAI_MODEL /etc/profile.d/*.sh 2>/dev/null
# if present, cai-run overrides it at runtime regardless, but fix the file
# too so plain `cai` (without the wrapper) also behaves
```

## Usage

```bash
cai-run                                    # default model/agent from the script
cai-run ollama/qwen2.5-coder:14b           # any local Ollama model
cai-run openai/<model-id> bug_bounter_agent  # different model + agent
```

It prints what it's actually using before launching:
```
[cai-run] model=... agent=... endpoint=...
```
If that line doesn't match what you expected, the fix didn't take — check
Bugs 1-4 in the README, in order, before assuming the model itself is at
fault.

## Verification

After launching, run `/model` inside CAI's REPL — the "Current model"
line should match what `cai-run` printed. If it shows something else, an
already-running CAI process (not this invocation) is what's stale, or the
provider's own API is substituting a different model than requested.
