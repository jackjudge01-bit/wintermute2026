# INSTALL — clawhub-taxonomy

## Prerequisites

```bash
python3 -m venv .venv
.venv/bin/pip install scikit-learn hdbscan requests numpy
```
(Kali/Debian's system Python is externally-managed — always use a venv
for anything not `apt`-packaged, never `pip install --break-system-packages`.)

```bash
ollama pull hf.co/Qwen/Qwen3-Embedding-0.6B-GGUF
```

## Input format

Both scripts expect a `groups.json`: a list of `{"cat": ..., "entries": [...]}`
groups, where each entry has at minimum `name`, `desc`, `slug`, and
optionally `dup` (`"dup_variant"` entries are skipped — they're detected
re-publishes of the same skill under a different slug, not distinct
items). Adapt the `load_entries()` function in `embed_skills.py` if your
source data has a different shape — the rest of the pipeline only needs a
flat list of `{name, desc, slug, ...}` dicts.

## Run

```bash
.venv/bin/python embed_skills.py groups.json skill_embeddings
# resumable: Ctrl-C and rerun the same command to continue

.venv/bin/python cluster_skills.py skill_embeddings \
  --method kmeans --k 55 --samples 6 --out cluster_assignments.jsonl
# read the printed clusters, hand-name them into your own taxonomy.json
```

## Verification

```bash
python3 -c "import numpy as np; a = np.load('skill_embeddings.npy'); print(a.shape)"
# should be (N, 1024) where N == your canonical entry count
```

`cluster_skills.py`'s printed cluster count + noise% (HDBSCAN mode) or
"0 noise points" (KMeans mode) confirms which algorithm/params you ran
with — KMeans always covers everything, HDBSCAN's noise% tells you how
much of the catalog didn't fit any dense cluster at that `--min-cluster-size`.

## Tuning

- If clustering hangs or takes minutes instead of seconds: you likely
  dropped `--pca-dims` to 0 or raised it too high (tree-based NN search
  degrades badly above ~100 dims on this data). Default (50) is a
  reasonable starting point.
- If KMeans clusters look mixed/grab-baggy: raise `--k`. If categories
  look too fragmented/overlapping: lower it. There's no universally
  correct `k` — it's a function of how granular you want the final
  taxonomy to be.
