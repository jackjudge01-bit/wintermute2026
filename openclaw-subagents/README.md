# openclaw-subagents — the real multi-agent routing bugs (and the wrong fixes)

Adding a second OpenClaw agent for sub-agent delegation (a specialist
consult model alongside the main driver) broke the primary agent's
messaging channel — twice, via two different wrong fixes, before finding
the actual mechanism. Every wrong turn is documented here because each one
looked correct from the docs/error message alone.

## The bug

```
[whatsapp] [default] channel exited: Multiple agents are configured, but
whatsapp account default routing has no explicit owner. Add a channel-wide
binding for whatsapp:default or configure a sole agent.
[whatsapp] [default] auto-restart attempt N/10 in 300s
```

The moment a second entry exists in `agents.entries`, any channel (WhatsApp,
Telegram, etc.) that isn't explicitly told which agent owns it throws this
and crash-loops on a 5-minute retry backoff — eventually exhausting all 10
attempts and just staying down.

## Wrong fix #1 — `"default": true` on the main agent entry

The error message's own hint ("configure a sole agent... ambient services
can set their agentId target") reads like `agents.entries.main.default =
true` should resolve it — it's a real, documented legacy field. It does
work — **unless** `agents.ownership` is set to `"explicit"` anywhere in the
config (which it will be, if this isn't your first attempt at a multi-agent
setup — OpenClaw's own config-writer sets `ownership: "explicit"`
automatically once more than one agent exists). Under that mode:

```
[reload] config reload skipped (invalid config): agents.ownership:
agents.ownership=explicit cannot be combined with a legacy default=true marker
```

The reload is silently skipped — the file on disk becomes invalid for any
*future* restart while the *currently running* process keeps serving on
its last-good in-memory config, which makes this easy to miss until the
next restart fails outright.

## Wrong fix #2 — `agents.defaults.systemAgent.agentId`

Also real, also documented-adjacent, and it resolves a **different**
ownership question than the one WhatsApp actually asks. It successfully
lets you hot-reload a second `agents.entries` block without an immediate
error, which looks like confirmation it fixed the channel routing — it
didn't. It governs "ambient services" (background/system-level operations),
not channel message routing. WhatsApp still crash-loops on the next full
process restart even with this set, because channel routing goes through a
completely different resolver (`resolve-route`) that never consults
`systemAgent` at all.

## The actual fix — a `bindings` entry

Confirmed by reading the resolver's own source
(`resolve-route-*.mjs`), not the docs — the docs page for sub-agents never
mentions this at all, it's config-schema/channel-routing territory, a
different feature entirely:

```json
{
  "bindings": [
    {
      "match": { "channel": "whatsapp" },
      "agentId": "main"
    }
  ]
}
```
(top-level `bindings` key, sibling to `agents`, not nested inside it)

This is the real, current, `ownership: explicit`-compatible way to give a
channel an explicit owner regardless of how many other agents exist. Add
one `match`/`agentId` pair per channel that needs an explicit owner; a
sub-agent-only entry (never bound to any channel) needs no binding of its
own and won't affect this at all once the *real* owner is bound correctly.

**Diagnostic tip**: a hot-reload succeeding is not proof a routing fix
worked — the ambiguity error only fires at channel *startup*, which a live
hot-reload doesn't necessarily re-trigger. Test with a full process
restart (`systemctl --user restart` or equivalent), not just a config edit.

## Bonus finding: real VRAM vs. GGUF file size

Planning a second model to run alongside the primary on shared VRAM by
adding up `.gguf` file sizes will undercount badly. Resident VRAM at a
model's *configured context window* (KV cache) can be meaningfully larger
than the file itself:

| Model | File size | Resident VRAM (at configured context) |
|---|---|---|
| Qwen3.6-27B-abliterated Q4 | ~17GB | ~18GB |
| Hermes-4-14B-i1 Q4 | ~9GB | ~14GB |
| WhiteRabbitNeo-V3-7B-i1 Q4 | ~4.7GB | ~6.6GB |

Test the actual pairing you need with `nvidia-smi` after loading both,
don't trust file-size arithmetic — Ollama will silently evict the
least-recently-used model to make room rather than erroring, so a
too-tight budget shows up as unexplained reload latency, not a clean
failure.

## Bonus finding: "adaptive" thinking level isn't universal

OpenClaw's `--thinking adaptive` (let the model decide how much to reason)
only works on models with *native* adaptive-reasoning support — Claude
family models, specifically. A local llama.cpp/GGUF model via an
OpenAI-completions-style provider only exposes fixed budget levels (`off`,
`minimal`, `low`, `medium`, `high`) and hard-rejects `adaptive` at
request-validation time:

```
Thinking level "adaptive" is not supported for <provider>/<model>.
Use one of: off, minimal, low, medium, high.
```

If a reasoning model is burning its entire token budget mid-`<think>` and
never reaching an answer (`stopReason: length`, repeated fallback to a
secondary model), don't reach for `adaptive` as the fix on a local
GGUF-backed model — it doesn't exist there. `minimal` is the smallest real
step down from the default, and is worth A/B testing directly (a few CLI
runs comparing `stopReason` and response completeness) before committing
it as a persistent default — the failure mode is prompt-dependent, small
sample sizes can mislead.
