# wintermute2026

Lab notebook: working tools and scripts built on the wintermute Kali laptop
(RTX 5070 Ti, HackRF One SDR, ESP32 Marauder). Everything here was built to
solve a real problem on the box and is verified working unless marked
otherwise.

## Contents

| Directory | What it is |
|---|---|
| `hackrf/` | Live FM demodulation monitor + wideband recon waterfall/signal detector for the HackRF One SDR |
| `devicedb/` | Unified BT/Wi-Fi/LAN device inventory (SQLite + collectors) |
| `marauder/` | ESP32 Marauder serial-CLI protocol probes (pyserial) |
| `ollama-benchmarks/` | Local-LLM benchmark probes (thinking tax, prefill vs ctx, sampler bisects) |
| `cai-ollama/` | CAI + local-Ollama config bugs (keep-alive, dotenv, model precedence) and the wrapper that works around them |
| `clawhub-taxonomy/` | Embedding + clustering pipeline for building a real category system over 70K+ unlabeled items |
| `forum-dl-vbulletin-fix/` | Patch for a forum-dl crash against vBulletin sites with no `<base>` tag |
| `gpu-status/` | Cross-checks nvidia-smi against Ollama/LM Studio to catch VRAM contention neither tool reports on its own |
| `ble-fingerprint/` | Identify a BLE device via host-side scan + GATT enumeration, beyond just its broadcast name |
| `hf-model-vetting/` | Data-gathering script + checklist for telling a real HuggingFace model release from a spam/rebrand repo |
| `openclaw-subagents/` | OpenClaw multi-agent routing bugs — two wrong fixes tried before the real one (a `bindings` entry, not `default:true` or `systemAgent`), plus real VRAM and thinking-level findings |
| `ghostesp-virtual-sd/` | Two-patch series adding a user-sizeable internal-flash "virtual SD" to GhostESP for boards with no SD slot — patches only, compile-test blocked, not yet flashed |
| `ghostesp-uart-capture-fix/` | Merged GhostESP command-sender + raw UART listener into one script — the ESP32 exclusively locks its serial port, so two processes fighting over it always lost data. Verified working. |
| `runpod-context-window-fix/` | Diagnosed why a RunPod-hosted Ollama model's practical context ceiling (~100k) is far below its advertised max (262k) — real VRAM/config numbers, fix identified, not yet applied/verified |

More added case-by-case as work is captured.
