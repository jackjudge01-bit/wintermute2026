# INSTALL — hf-model-vetting

## Prerequisites

- `curl`, `python3` (standard library only — no pip installs needed)
- No HuggingFace account or token required; uses the public API, works
  for any public repo

## Install

```bash
chmod +x hf-vet.sh
```

## Usage

```bash
./hf-vet.sh Qwen/Qwen2.5-7B-Instruct
./hf-vet.sh some-random-user/Suspicious-Sounding-Model-GGUF
```

## Verification

```bash
./hf-vet.sh Qwen/Qwen2.5-7B-Instruct
```
Expect real download/like counts (millions/thousands), author `Qwen`, and
a substantive README about Qwen2.5's actual capabilities. Compare against
a repo you suspect is spam — a large downloads/likes disproportion from an
unfamiliar single-user account is the pattern to watch for (see README.md
for the full checklist).

```bash
./hf-vet.sh nonexistent-org/nonexistent-repo-xyz
```
Expect an `ERROR:` line from the model-info step (HF's API returns a
generic auth-flavored error for unknown repos, not a clear "not found") and
the script stops there (`set -e`) — no traceback, but also no README/
checklist output for a repo that doesn't exist.
