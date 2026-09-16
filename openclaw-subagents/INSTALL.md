# INSTALL — openclaw-subagents

This is a config-pattern writeup, not a script — there's nothing to
install. Use it as a checklist when adding a second (or further) OpenClaw
agent for sub-agent delegation.

## Before adding a second agent entry

1. Check current ownership mode:
   ```bash
   python3 -c "import json; c=json.load(open('$HOME/.openclaw/openclaw.json')); print(c.get('agents',{}).get('ownership'))"
   ```
   If this prints `explicit`, the legacy `default: true` marker is
   unusable — go straight to the `bindings` approach below.

2. Back up the config first, always:
   ```bash
   cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.bak-$(date +%s)
   ```

## Adding the second agent (sub-agent-only, no channel binding)

```json
"agents": {
  "entries": {
    "main": {
      "...": "existing fields unchanged",
      "subagents": { "allowAgents": ["my-specialist"] }
    },
    "my-specialist": {
      "name": "my-specialist",
      "workspace": "/path/to/workspace",
      "agentDir": "/path/to/agents/my-specialist/agent",
      "model": "provider/model-id"
    }
  }
}
```

## Fixing channel routing (do this regardless of ownership mode)

Add a top-level `bindings` entry (sibling of `agents`, not nested in it)
for every channel that needs an explicit owner:

```json
"bindings": [
  { "match": { "channel": "whatsapp" }, "agentId": "main" }
]
```

Repeat per channel (`telegram`, `discord`, etc.) if you use more than one.

## Validate and apply

```bash
openclaw config validate
```

Then do a **full restart**, not just a config edit — the routing-ambiguity
check only fires at channel startup:
```bash
systemctl --user stop openclaw-gateway
pgrep -f "openclaw.*gateway --port" | xargs -r kill -9
systemctl --user start openclaw-gateway
```

## Verify

```bash
journalctl --user -u openclaw-gateway --no-pager -n 30 | grep -i <channel>
```
Expect `Listening for <channel> inbound messages` with no `channel exited`
/ `no explicit owner` lines, and no repeated `auto-restart attempt N/10`.

Test the sub-agent mechanism directly:
```bash
openclaw agent --agent my-specialist --message "test" --json
```
Then test it being invoked *from* the main agent (confirms the
`allowAgents` wiring, not just that the specialist model itself works):
```bash
openclaw agent --agent main --message "spawn my-specialist and ask it something, tell me what it said" --json
```
Check the response JSON for `terminalReceipt.successfulToolNames`
containing `sessions_spawn` and `sessions_yield` — that's confirmation the
delegation actually happened, not just that the main agent answered on
its own.
