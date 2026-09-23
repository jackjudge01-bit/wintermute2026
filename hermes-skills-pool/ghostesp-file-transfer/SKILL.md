---
name: ghostesp-file-transfer
description: Pull a file (pcap capture, scan log, any file under /mnt) off a GhostESP ESP32's SD-backed storage over WiFi/HTTP, instead of over the exclusive UART.
trigger:
  - get a file off the ESP32
  - download pcap from GhostESP
  - transfer file from ESP32 SD card
  - ESP32 SD card download
  - GhostESP file retrieval
  - UART port busy / exclusively held (this is the fix)
---

# GhostESP file transfer over WiFi

## Why WiFi instead of UART

The UART/serial port is exclusively held by whichever process opened it —
see `ghostesp-eapol-capture-toolchain` for the "PCAP flood" and two-process
port-fighting problems that come from trying to move data over serial
while also sending commands. This skill sidesteps that class of problem
entirely: once set up, file retrieval is plain HTTP, no serial contention.

Other alternatives were checked and ruled out on this hardware, not just
skipped: the ESP32-S3 has no Bluetooth Classic radio (BLE only, so no
OBEX/native Bluetooth transfer); GhostESP has no SSH server or client
(SCP/RCP impossible); UUCP needs a full daemon on both ends, more work
than this; TFTP exists in ESP-IDF's lwIP but isn't compiled into this
build.

## One-time setup (per network — persists across ESP32 reboots, saved to NVS)

Over the serial console (same port `ghostesp-eapol-capture-toolchain`
uses, e.g. `/dev/ttyACM0`, 115200 baud):

1. `connect "<SSID>" "<PASSWORD>"` — joins the target WiFi in STA mode.
   GhostESP keeps its own AP running too. Confirm success by looking for
   `Got IP: <address>` and `Successfully connected to <SSID>` in the
   response — that text can arrive mixed in with other serial noise, so
   search the full output rather than assuming failure from a messy read.
2. `webuiap off` — GhostESP's HTTP API defaults to only answering requests
   from its own AP subnet (192.168.4.x) and returns 403 to everything
   else, including the network just joined in step 1. This is the step
   that's easy to miss. Verify with `webuiap status`.

Never write a WiFi password into this skill or hardcode it in a script —
check the credentials store this machine already uses
(`/home/jack/api-keys/CREDS.md` on this box) before asking the user for it
again.

## Per-use (no serial connection needed once setup is done)

3. Find the ESP32's current IP on the shared network by MAC (stable
   across reconnects, unlike the IP): `nmap -sn <subnet>/24`.
4. List files: `curl "http://<esp32-ip>/api/sdcard?path=<dir>"` — JSON,
   files under `/mnt` (capture files are typically under
   `/mnt/ghostesp/pcaps`) plus storage stats.
5. Download: this is a POST, not a GET —
   ```
   curl -X POST "http://<esp32-ip>/api/sdcard/download" \
     -H 'Content-Type: application/json' \
     -d '{"path":"<full path under /mnt>"}' \
     -o <local-output-path>
   ```
   Verify the download actually worked — check size against the listing,
   and for pcap files, `file <output>` should report `pcap capture file`.
   Don't trust a 200 status code alone.

## Failure modes

- **403 Unauthorized** — `webuiap off` wasn't done, or didn't take.
  Re-check `webuiap status`.
- **Device stops responding to serial / keeps dumping binary data even
  after `capture -stop`** — same PCAP-flood class of problem
  `ghostesp-eapol-capture-toolchain` documents, but if a couple of stop
  attempts don't clear it, don't keep retrying indefinitely. A hardware
  reset clears it without touching flash or losing the WiFi connection
  (STA creds and `webuiap off` are both in NVS, not RAM):
  `esptool --chip esp32s3 --port <port> --after hard-reset chip-id`.
  Wait a few seconds and confirm with `chipinfo` before continuing.
- **Can't find the device's IP** — re-resolve by MAC, don't reuse a
  cached IP; DHCP leases change.
