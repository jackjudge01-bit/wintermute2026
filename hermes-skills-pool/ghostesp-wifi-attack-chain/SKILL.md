---
name: ghostesp-wifi-attack-chain
description: End-to-end GhostESP ESP32-S3 WiFi chain - scan, deauth, capture, transfer the capture off the board, and analyse it in Wireshark. Built around the single-radio constraint (time-slice or two-board split), with the safe vs hijacking command combos spelled out.
trigger:
  - run the full wifi scan deauth capture chain
  - GhostESP scan deauth capture workflow
  - capture a wifi handshake with the ESP32
  - deauth and capture at the same time
  - capture -wireshark plus attack -d
  - which capture/attack commands can run together
  - end to end esp32 wifi attack pipeline
---

## What this covers and the one hard constraint

The full chain on a GhostESP-firmware ESP32-S3: **scan → deauth → capture →
pull the capture off the device → analyse in Wireshark.** All of it was
worked out and verified against firmware source and real hardware, except
one final step noted as unverified at the end.

**The hard constraint that shapes everything below:** an ESP32(-S3) has one
physical WiFi radio, on one channel, in one role at any instant. You cannot
truly transmit deauth (TX) and capture (RX) at the same time on a single
board across different channels. Everything here is built around that fact —
either time-slice on one board, or split the two jobs across two boards.

Serial console is the GhostESP CLI: `/dev/ttyACM0` (or similar) at 115200
baud, prompt `ghost>`.

**Authorization:** deauth and handshake capture against a network you do not
own or have written permission to test is unlawful. Only run stages 2–3
against an AP the user has confirmed is theirs / in scope. If no authorized
target has been named, do the scan/recon stage only and ask for the target
before transmitting anything.

## Stage 1 — Scan / recon

Over serial, enumerate APs and their associated stations, and note the
channel of the target (you need the channel to pin capture and deauth to
the same one):

```
scanap          # list nearby APs (SSID, BSSID, channel, RSSI)
scansta         # list stations associated to APs
```

A pure passive listen also works as recon and never transmits:

```
capture -raw -channel <n>
```

Record: target **BSSID**, **channel**, and the **client MAC(s)** you intend
to deauth.

## Stage 2 + 3 — Deauth and capture together

Three working shapes, in order of preference. Pick based on whether you want
a live Wireshark feed or a file on the device, and whether you have one board
or two.

### Shape A — Two boards (cleanest, no self-interference)

One board captures, the other transmits. The capture board is an independent
observer, so nothing the deauth board does can disturb it.

- **Capture board:** `capture -wireshark -channel <n>` — streams raw libpcap
  bytes out over USB-CDC to the host. Pin `<n>` to the target's channel.
- **Deauth board:** `attack -d` on the same channel.

Host side reads the capture board's serial stream into Wireshark (see
Stage 5).

### Shape B — One board, concurrent (verified safe in source)

Run two GhostESP commands at once on a single board:

```
capture -wireshark        # RX, streams raw frames over serial, keeps running
attack -d                 # TX only
```

Verified in firmware source: plain `attack -d` (`deauth_attack_start_station()`)
**only** calls `esp_wifi_80211_tx()` — it never registers a promiscuous RX
callback, so it does not disturb the capture already running.

**Do NOT use `attack -hsd` for this.** `-hsd`
(`deauth_attack_start_handshake_deauth()`) registers its *own* EAPOL-only
promiscuous callback, and only one promiscuous callback can exist at a time —
starting it **hijacks and replaces** the `capture -wireshark` stream's
callback, killing the live feed and silently switching capture to `-hsd`'s
internal file path. Use `capture -wireshark` + plain `attack -d`, nothing else.

Wireshark does the EAPOL/handshake filtering host-side on the raw stream, so
you are not relying on the device's own filtering at all here.

### Shape C — File-based on the device (no host stream)

The device writes a pcap to its SD storage (real SD or the internal-flash
virtual SD), mounted at `/mnt`, capture files under `/mnt/ghostesp/pcaps`:

```
capture -raw -channel <n>     # all frames
capture -eapol -channel <n>   # EAPOL only
attack -hsd                   # burst-deauth then listen, writes handshake pcap
```

This path depends on patches 01–06 to `pcap.c` (the writer-task/queue fix
that moves the SD flush off the WiFi driver's stack) plus the virtual-SD
resize fix. **Verified on hardware:** `rawscan_1.pcap` came back 450,386
bytes with valid pcap magic (`d4c3b2a1`), linktype 127 (radiotap), and real
beacon frames decoded byte-for-byte — not empty, not corrupt. Then pull the
file off with Stage 4.

### Why not concurrent across channels / true simultaneity

One radio. FreeRTOS time-slices CPU fine, but the radio is the bottleneck.
`-hsd` and Bruce's `Brucegotchi` both solve this the same way independently:
time-sliced phases (deauth burst → listen), not real concurrency. Marauder
and Bruce have the same capture-to-storage fragility class — this is a
hardware-class problem, not a GhostESP bug.

## Stage 4 — Pull the capture off the device

If you used **Shape C** (a file on the device), retrieve it over WiFi/HTTP —
**not** over serial (UART is exclusive, and log text interleaves into a
binary pcap stream with no resync framing, corrupting it).

**This is a separate skill: `ghostesp-file-transfer`. Use it.** In short:
one-time `connect "<SSID>" "<PASS>"` + `webuiap off` over serial, then per-use
`nmap -sn <subnet>/24` to find the device by MAC, `GET /api/sdcard?path=<dir>`
to list, `POST /api/sdcard/download` to stream the file back. Full detail,
including the `403`/`webuiap` gotcha and the hard-reset recovery, lives in
that skill — do not re-derive it here.

If you used **Shape A or B** (live stream), there is no file to transfer —
the capture already arrived on the host in Stage 5.

## Stage 5 — Analyse in Wireshark

**Live stream (Shape A/B):** Wireshark cannot open `/dev/ttyACM*` directly as
a live interface. Bridge it — either a small reader script piped to
`wireshark -k -i -`, or the GhostESP extcap plugin so it appears as a capture
interface. The `ghostesp-wireshark-deauth-workflow` README in `wintermute2026`
documents the extcap setup and the Linux-vs-Windows packaging caveat.

Caveat on the live path: on a single board, other console/log output can
interleave into the raw pcap byte stream (the `-wireshark` path does not
suppress logging the way the SD path does), which desyncs libpcap. Prefer the
two-board split, or the file+WiFi path, when stream integrity matters.

**Pulled file (Shape C):** just open the `.pcap` in Wireshark. Filter EAPOL
(`eapol`) to isolate handshakes, or `wlan.fc.type_subtype == 0x08` for beacons,
etc.

## State of the work (as of 2026-09-21)

- Patches 01–06 live in `GhostESP` branch `Development-deki`.
- Virtual-SD work PR'd upstream to GhostESP-Revival (pinged, awaiting review).
- Docs pushed in `wintermute2026`: `ghostesp-virtual-sd/`,
  `ghostesp-uart-capture-fix/`, `ghostesp-wireshark-deauth-workflow/`.
- Handover notes in `/home/jack/handover/`.

**The one thing never verified:** the full `capture -wireshark` + `attack -d`
combo run end-to-end against a *real, authorized* target AP. It was left
blocked on an in-scope target being named. Everything upstream of that (each
command in isolation, the source-level safety of the combo, file capture to
virtual SD, and the file transfer) is verified.
