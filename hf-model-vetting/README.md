# hf-model-vetting — telling a real model release from a spam/rebrand repo

HuggingFace's search results are full of two very different kinds of
"unfamiliar" repos, and conflating them leads to opposite mistakes:

1. **Genuinely new releases** you just haven't heard of yet — fast-moving
   labs (Qwen especially) ship point releases faster than any single
   person tracks. Dismissing `Qwen/Qwen3.6-27B` as "fake, I've never heard
   of Qwen3.6" was wrong in practice — it was a real, current release,
   verifiable in about two minutes.
2. **Actual spam/rebrand repos** — independent accounts publishing
   community merges under fabricated version numbers or names that graft
   an unrelated well-known brand onto the title (seen in practice:
   "Claude-Opus"/"Claude-Fable" branding on merges with zero connection to
   Anthropic), often with artificially inflated download counts.

The name alone doesn't distinguish these. **The org identity, the
download/like ratio, and whether the README tells a real story do.**

## Method

For a given repo:
- **Who published it?** The real `Qwen` org, or a random independent
  username that merely *sounds* official?
- **Downloads vs. likes.** A repo with 600K+ downloads and ~270 likes from
  an unknown single-user account is a red flag pattern (likely automated/
  farmed download traffic) — a real lab's popular release usually has
  engagement roughly proportional to its download count over time.
- **Does the README cohere?** A real release references actual prior
  versions, cites benchmarks or an arxiv paper, explains what changed. A
  templated/generic README with no real content, or one that reuses
  another product's name without explanation, is the tell.
- **Verify before dismissing.** If a version number looks wrong because
  it's unfamiliar to *you*, check whether the org and README are
  coherent before concluding it's fake — a genuinely new release can be
  newer than your own knowledge.

## `hf-vet.sh`

Pulls model info (author, downloads, likes, tags, base_model lineage),
the org/user type, and the first 40 lines of the README in one shot — the
data you'd otherwise gather across 2-3 separate page visits — then prints
a checklist to judge it against. It deliberately does **not** output a
verdict: the judgment call (does this actually cohere) doesn't reduce to
a threshold on any single number, and a false-positive "confirmed spam"
from an automated heuristic is worse than doing the two minutes of
reading yourself.
