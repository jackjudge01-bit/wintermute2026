---
name: ghostesp-wifi-recon-chain
description: Modular passive-then-focused WiFi recon toolchain for the GhostESP ESP32 - Module 1 broadly scans local APs and ranks them by connected-client count, Module 2 does a longer channel-locked listen on the top-ranked APs to harvest client probe-request details (device MAC, probed SSID). Both modules are receive-only.
trigger:
  - scan local wifi for most active APs
  - rank APs by connected clients
  - passive wifi recon
  - probe request harvesting
  - GhostESP recon toolchain
  - which AP has the most clients
---

# GhostESP WiFi recon toolchain

Two modules, deliberately stopped at recon - no deauth, no forced
reconnects, no joining a target network, no touching an individual
client device. Everything here only listens to radio traffic that's
already being broadcast in the open, the same class of action as running
any WiFi survey tool. That boundary is intentional, not an oversight: go
past it (deauth, EAPOL/handshake capture, joining and probing devices on
a specific network) only against a target that's been explicitly
confirmed in-scope/authorized for this engagement - see
`ghostesp-eapol-capture-toolchain`-style work for what that next stage
looks like, once that authorization exists.

## Why one Python process per module, not separate command-senders and listeners

GhostESP's UART is exclusively held by whichever process has it open -
documented extensively elsewhere in this skill tree (search for "PCAP
flood" / exclusive port). Each module below opens the serial port once,
does its whole sequence of commands and reads, and closes it - never two
processes fighting over `/dev/ttyACM0` at once.

## Module 1 — passive scan, rank APs by connected-client count

`scripts/module1_passive_scan.py`

```
python3 module1_passive_scan.py --port /dev/ttyACM0 \
  --ap-seconds 10 --sta-seconds 15 --out /tmp/scan.json
```

Runs `scanap` (AP scan) then `scansta` (station scan), groups the
stations under their associated AP by BSSID, and ranks APs by how many
clients are attached. Output is JSON - each AP entry has its scan fields
(SSID, BSSID, channel, RSSI, vendor) plus a `clients` list and
`client_count`, sorted descending.

**Be upfront about what "most traffic" actually means here**: GhostESP's
scan commands don't return a byte/frame-count metric, so this ranks by
connected-client count, not measured traffic volume. Client count is a
reasonable proxy for AP activity/importance (more attached devices
generally means more traffic) but it isn't the same measurement as
counting bytes. If a true traffic-volume metric is ever needed, that's a
different, heavier technique (e.g. a timed raw-frame capture per channel
counting bytes seen) - not something to silently claim this module
already does.

## Module 2 — focused probe-request harvesting on the top APs

`scripts/module2_aggressive_probe.py`

```
python3 module2_aggressive_probe.py --port /dev/ttyACM0 \
  --module1-json /tmp/scan.json --top 3 --seconds-per-ap 20
```

Takes Module 1's ranked list, and for the top N APs by client count,
does a longer channel-locked listen (`capture -wireshark -channel <ch>`,
streamed straight over the same serial connection this script has open -
no SD/file step needed) and parses every probe-request frame seen for
the transmitting client's MAC and the SSID it's asking about. An empty
probed SSID means a wildcard/broadcast probe - common on modern
phones with randomized-MAC privacy features, and worth reporting as its
own signal rather than treating as "no data."

Read the script's own docstring before using it - it's explicit about
what this can't give you (no RSSI per probe; GhostESP's raw stream uses
a stub radiotap header with no real radio metadata in it) and about the
one real caveat in what it captures: probe requests seen during the
listen window come from whatever's transmitting on that channel, which
usually overlaps with but isn't guaranteed to be limited to the specific
client list Module 1 found associated with that AP.

## Chaining them

```bash
python3 module1_passive_scan.py --port /dev/ttyACM0 --out /tmp/scan.json
python3 module2_aggressive_probe.py --port /dev/ttyACM0 \
  --module1-json /tmp/scan.json --top 3
```

Run them back to back, not concurrently - same single-UART-at-a-time
reasoning as everything else in this skill tree. Module 1 fully closes
its serial connection before Module 2 opens its own.

## Pitfall: never reset the serial buffer before a STOP command

Commands that STREAM results while running (e.g. `blescan -adv`) accumulate
their data in the serial input buffer during the scan. A command helper that
calls `reset_input_buffer()` before writing the stop command (`blescan -s`)
discards every result collected during the scan — you get 0 records with no
error. Send the stop WITHOUT resetting, then read both the buffered and
newly arriving bytes. (Verified on GhostESP Revival v2.2: wiped a full 10s
BLE scan; fix restored 23 advertisers.)

## What comes after this, and why it isn't in this skill

Deauthing a target, capturing the resulting handshake, joining and
enumerating devices on a specific network - all of that needs the target
to be a confirmed, in-scope engagement, not just "whatever's nearby."
This toolchain stops here on purpose so that boundary stays a deliberate
decision each time, not something baked into a script that gets run
without thinking about who's on the other end of it.
