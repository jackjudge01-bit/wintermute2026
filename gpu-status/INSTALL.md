# INSTALL — gpu-status

## Prerequisites

- `nvidia-smi` (NVIDIA driver installed)
- `ollama` on PATH (optional — script degrades gracefully if absent)
- `lms` (LM Studio CLI, optional — same)

## Install

```bash
chmod +x gpu-status.sh
```
No dependencies to install; it only shells out to tools you already have.

## Usage

```bash
./gpu-status.sh
```

Run it any time something GPU-related looks slower or more contended than
expected, before assuming the model or the code is at fault. Safe to run
at any time — read-only, makes no changes.

## Verification

With nothing loaded, expect:
```
=== GPU memory ===
<low used>, <total>, <mostly free>, <low %>

=== Ollama's view (ollama ps) ===
NAME    ID    SIZE    PROCESSOR    CONTEXT    UNTIL

=== Cross-checks ===
(done)
```
No `WARNING:` lines with nothing loaded. Load a model and rerun to see
`ollama ps` populate; the warnings only fire on the specific bad states
described in README.md.
