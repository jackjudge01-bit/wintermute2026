# clawhub-taxonomy — building a real category system for 70K unlabeled items

Source data: 76,562 ClawHub (OpenClaw's skill registry) entries, each just
a name + ~200-char description. ClawHub's own category headers are junk —
keyword-matched off description text, so e.g. a sales-pitch skill lands in
"SDR/RADIO/RF" because it mentions "radio silence." Goal: a real,
data-driven, multi-label taxonomy, built without hand-guessing categories
up front.

## Pipeline

```
embed_skills.py   -->  skill_embeddings.npy + _meta.jsonl
                          |
cluster_skills.py -->  proposed clusters (read, name them by hand)
                          |
                        taxonomy.json  (final, hand-curated category list)
```

### 1. `embed_skills.py`

Embeds `name + ": " + desc` for every canonical (non-duplicate) skill using
a **local** `Qwen3-Embedding-0.6B` via Ollama (`ollama pull
hf.co/Qwen/Qwen3-Embedding-0.6B-GGUF`). Chosen specifically for its
multilingual reach — roughly a third of this catalog is Chinese/Cyrillic,
and a single shared embedding space lets clustering work across languages
without a separate translation step.

Checkpointed every batch (writes the `.npy` incrementally) — safe to
Ctrl-C and resume; it re-reads how many rows already exist and continues
from there. ~17 items/s on a single consumer GPU; ~70K items ≈ 70 minutes.

### 2. `cluster_skills.py`

**The real finding here: don't run HDBSCAN directly on 1024-dim
embeddings.** Tree-based nearest-neighbor search (which HDBSCAN relies on)
degrades to near-brute-force at that many dimensions — a run that should
take seconds took 35+ minutes of CPU time with no output and had to be
killed. Reducing to 50 dims with PCA first (`--pca-dims 50`, on by
default) took the same clustering down to ~20 seconds, no accuracy loss
that mattered for this use case.

Second finding: HDBSCAN (density-based, leaves "noise" unassigned) is the
wrong tool when the goal is a *complete* candidate taxonomy over a
long-tail catalog — it left 90%+ of items as noise regardless of parameter
tuning, because most items in a catalog like this genuinely are one-off/
niche rather than members of a dense cluster. **KMeans** (`--method
kmeans`, full coverage, no noise concept) produced far more useful output
for *this specific* goal — proposing candidate categories to name by hand
— even though HDBSCAN would be the right choice for a different goal (e.g.
finding only the tightest, most confident groupings).

```bash
python3 cluster_skills.py skill_embeddings --method kmeans --k 55 \
  --samples 6 --out cluster_assignments.jsonl
```
Prints each cluster's size + the N examples closest to its centroid — read
those, assign a real category name, iterate `--k` up/down if a cluster
still looks like a grab-bag of unrelated things.

### 3. `taxonomy.json`

The actual output: 52 hand-named categories (from reading the KMeans
clusters above) plus one `flags` entry (`boilerplate` — a cluster that
turned out to be the same "Use this skill for ANY \<SaaS\> request"
template reused across dozens of unrelated product names, i.e. a
mass-produced pattern, not a real topic). `open_items` in the file notes
two candidate merges (probable EN/CN duplicate clusters) spotted but not
yet acted on.

## Deliberately not included here

A `classify_skills.py` (multi-label classification of every skill against
this taxonomy via a local LLM with JSON-schema-constrained output) exists
but isn't included — it's blocked on the same "thinking" tax problem
documented in `cai-ollama/README.md` (Granite 4.2 generated 300-500+
reasoning tokens per trivial classification call, ~13s each, not viable at
70K scale) and needs either a non-reasoning local model or a cloud
batch-API model before it's actually usable.
