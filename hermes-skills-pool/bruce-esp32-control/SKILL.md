---
name: bruce-esp32-control
description: Drive an ESP32 running Bruce firmware via the bruce MCP server or direct serial - connect wifi, query info, arp scan, sniff, start the WebUI, and launch on-screen apps headlessly. Covers the serial CLI facts and the serial-vs-WebUI split for wifi attacks. Not for GhostESP/Marauder firmware.
trigger:
  - control the Bruce ESP32
  - drive Bruce firmware over serial
  - bruce_info / bruce_wifi_connect / bruce_raw
  - connect the Bruce board to wifi
  - start the Bruce WebUI headless
  - run a wifi attack on the Bruce esp32
  - launch a Bruce app with loader/nav
---

## What this drives

An ESP32 running **Bruce firmware** (pr3y/BruceDevices). On this machine the
target is a **bare ESP32-S3 devkit (N16R8: 16MB flash, 8MB PSRAM), no screen**,
flashed with the `esp32-s3-devkitc-1-psram` build of Bruce **1.16.1**. It is
driven through the **`bruce` MCP server** (`/home/jack/mcp-kit/servers/bruce.py`,
registered in Bifrost alongside `ghostesp`), whose tools are exposed to hermes
via the bifrost bridge. You can also talk to it directly over serial.

Bruce is a *different firmware from GhostESP and Marauder* — do not use the
`ghostesp-*` or `marauder-*` tools or command syntax against it. They will not
work.

## Serial facts (from Bruce source, not guessed)

- Port: a USB serial device (default `/dev/ttyACM0`). **On an ESP32-S3 the CLI
  may be on the native-USB CDC port (Espressif VID `303a`), not the CH34x UART
  bridge (VID `1a86`)** — if a command gets no response, try the other port
  (set `BRUCE_PORT` for the MCP server).
- Baud **115200**, 8 data bits, 1 stop bit, no parity, no flow control.
- **Line terminator is `\n`** — Bruce reads `readStringUntil('\n')`, trims, then
  parses with SimpleCLI. (`\r\n` also works; the `\r` is trimmed.)
- A bad command returns a line beginning **`ERROR: `** (sometimes followed by
  `Did you mean "..."?`). There is no explicit end-of-output delimiter, so the
  MCP server drains the port for a short window per command.
- `help` (aliases `?`, `halp`) lists the commands this build actually registers.
  `info` (aliases `!`, `device_info`) describes the device (version, MACs, and
  the assigned IP once connected). Always run `bruce_help` on a new build rather
  than assuming a command exists — the command set is compile-time conditional
  (e.g. `screen`, `sound`, `badusb`, JS interpreter are only present on builds
  that enable them).

## `bruce` MCP tools

- `bruce_info` — device info / firmware version / IP.
- `bruce_help` — list supported serial commands on this build.
- `bruce_wifi_connect(ssid, pwd)` — `wifi add` + `wifi on`, then returns info (read the IP here).
- `bruce_wifi_off` — `wifi off`.
- `bruce_webui_start(no_ap)` — start the WebUI. **Blocks the serial CLI until ESC.**
- `bruce_webui_stop` — send ESC (`0x1b`) to quit the WebUI and free the CLI.
- `bruce_scan_hosts` — `arp` scan of the joined network (needs STA connection).
- `bruce_sniffer_start` — `sniffer` (no serial stop; reset to stop).
- `bruce_loader(appname)` — launch an on-screen app by name (headless attack apps live here).
- `bruce_nav(command, duration)` — simulate a button (`next`/`prev`/`sel`/`esc`); does NOT return to menu, so steps chain.
- `bruce_menu_option(run)` — select a current-menu option by index.
- `bruce_raw(cmd, wait)` — arbitrary serial command; the escape hatch (subghz, ir, gpio, storage, settings, js, crypto, reboot, …).

## The important limitation — wifi attacks are NOT serial commands

Bruce's serial wifi surface is deliberately narrow (`src/core/serial_commands/
wifi_commands.cpp`): only `wifi off|on|add`, `webui`, `arp`, `listen`, `sniffer`.
**Deauth, beacon-spam, evil-portal, handshake capture and AP scanning are on-screen
menu apps — they have no direct serial command.** On a headless board reach them
one of two ways:

1. **WebUI over WiFi (fuller surface):** `bruce_wifi_connect` → read the IP from
   `bruce_info` → `bruce_webui_start` → drive the features over `http://<ip>/` →
   `bruce_webui_stop`. Confirm the exact HTTP endpoints against the running WebUI.
2. **Drive the menu over serial:** `bruce_loader("<AppName>")` to launch an app
   directly, or `bruce_nav`/`bruce_menu_option` to walk the menu into it. The
   exact app names are build-specific — enumerate them from the device (menu /
   `loader` behaviour) before scripting; this is the one place to confirm against
   the hardware because it is not fully documented.

## Typical headless flow

1. `bruce_info` — confirm it is Bruce and note the version.
2. `bruce_wifi_connect("<ssid>", "<pass>")` — join the network; read the IP.
3. For attacks: either `bruce_webui_start` + HTTP, or `bruce_loader`/`bruce_nav`
   into the attack app. **Only run attacks against an AP the user has confirmed
   is authorised/in-scope.**
4. To recover a stuck device: `bruce_raw("reboot")`, or hard-reset with
   `esptool --chip esp32s3 --port <port> --after hard-reset chip-id`.

## Reference

- Bruce Serial wiki — https://github.com/BruceDevices/firmware/wiki/Serial
- Serial Commands (DeepWiki) — https://deepwiki.com/pr3y/Bruce/11.2-serial-commands
- Headless Mode — https://wiki.bruce.computer/controlling-device/headless-mode/
- Source of truth for commands: `src/core/serial_commands/*.cpp` in the Bruce repo.
