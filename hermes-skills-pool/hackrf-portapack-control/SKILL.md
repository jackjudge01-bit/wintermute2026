---
name: hackrf-portapack-control
description: Drive a HackRF One + PortaPack running Mayhem firmware over its official USB serial console - launch any of its ~118 cataloged on-device apps (RX/TX/utility), read radio/system state, and know what each app does via the bundled catalog. Use this whenever the user wants to control, query, or launch something on the PortaPack/Mayhem device, asks what RF tools/apps are available on the HackRF, or mentions the HackRF/PortaPack by name in the context of doing something with it (not just analyzing an already-captured file - see hackrf_recon.py for that separate, unrelated post-processing tool).
trigger:
  - control the PortaPack
  - launch a Mayhem app
  - what apps does the HackRF have
  - HackRF PortaPack serial control
  - drive the Mayhem firmware
---

# HackRF + PortaPack Mayhem control

Two things live in `~/data/rf/`, already there before this skill existed
and worth knowing about first: `mayhem_catalog.txt` (long-form
descriptions of what every app actually does - read this to know *what*
to run) and `mayhem_apps.json` (the same apps as a plain id→name/category
index). This skill adds the missing third piece: an actual verified
driver (`scripts/portapack_control.py`) to *run* those apps, not just
know about them.

## The one thing that will make this look completely broken

The device shows up as one of two different USB PIDs depending on what
firmware mode it's currently in:

- **`0x6089`** ("HackRF passthrough") - the Mayhem console is not
  listening. Every serial command times out silently, zero bytes back,
  no error. This looks exactly like a wrong port/baud problem. It isn't.
- **`0x6018`** (Mayhem's own serial shell) - this is the mode needed.

**Check first, before debugging anything else**, if a command returns
nothing: `lsusb -d 1d50:`. If it's not `6018`, the console genuinely
isn't there to talk to yet.

Also: `/dev/ttyACMn` is not stable - the device re-enumerates as a new tty
on every mode switch or reconnect (verified live: it moved from
`ttyACM1` to `ttyACM2` mid-session). Don't hardcode a port; check
`ls /dev/ttyACM*` / `dmesg | tail` first, or use the script's
`find_port()` fallback and verify it guessed right.

## Using the driver

```python
from portapack_control import PortaPack
pp = PortaPack("/dev/ttyACM2")   # check the actual current path first
pp.applist()                     # [{"id": "blerx", "name": "BLE Rx", "category": "RX"}, ...]
pp.appstart("blerx")             # -> (True, "ok")
pp.radioinfo()                   # dict of receiver_model.*/transmitter_model.* fields
pp.close()
```

Or from a shell for one-off commands: `python3 portapack_control.py --port /dev/ttyACM2 sysinfo`.

## Real gotchas found by actually testing this against the device, not assumed from docs

- **`setfreq` doesn't always stick.** Verified: called it while `blerx`
  (BLE Rx) was running, got `ok` back, but `radioinfo` right after still
  showed the app's own frequency, not the one just set. Apps that manage
  their own frequency (channel-hopping receivers especially) can silently
  override a manual `setfreq`. Always read back with `radioinfo` after
  setting a frequency - don't trust the bare `ok`.
- **`button 6` is DFU** (firmware update mode), not a back/home button -
  the mapping (1=Right, 2=Left, 3=Down, 4=Up, 5=Select, 6=DFU, 7=Rotary
  Left, 8=Rotary Right) has no dedicated back/home button at all. The
  driver's `button()` method refuses button 6 outright rather than
  risking it. Use the documented `dfu` console command directly if DFU
  mode is genuinely wanted - never reach for `button 6` as a shortcut.
- **`applist` needs a real read window.** The default 2.5s wait silently
  truncated the list before the full ~2600 characters arrived at 115200
  baud - fixed to 4.0s in the driver. If you're calling `send("applist")`
  directly instead of the `applist()` wrapper, use a wait of at least 4s.
- **The static catalog and the live device don't perfectly agree.**
  As of 2026-09-23, `mayhem_apps.json` has 118 ids, up from 98 the pass
  before (added: `recon`, `capture`, `replay`, `lookingglass`, `audio`,
  `blerx`, `pocsag`, `radiosonde`, `search`, `subghzd`, `weather`,
  `bletx`, `ooktx`, `rdstx`, `touchtune`, `microphone`, `filemanager`,
  `freqman`, `iqtrim`, `notepad` - all cross-checked against the official
  wiki app-name list and all already had prose descriptions in
  `mayhem_catalog.txt`, they just hadn't been added to the JSON id map
  yet). **That pass could NOT reach the live device directly** - it was
  stuck enumerated as USB PID `0x6089` (passthrough) the whole time, so
  the 20 ids came from re-parsing a genuine prior `applist` capture saved
  at `~/projects/hackrf-ops/tools/device_dump/applist.txt` (dated
  2026-09-19, this device), fixing the same multi-app-per-line merging
  problem described below by hand instead of trusting that file's literal
  line breaks.
  **Still unresolved** (left alone rather than guessed, needs a live
  `appstart` test to settle): the 2026-09-19 capture's ids for four apps
  don't match what's already in `mayhem_apps.json` for what looks like
  the same app - `foxhunt_rx` (capture) vs `foxhunt` (catalog),
  `view_wav` (capture) vs `wav_view` (catalog), `pacman_app` (capture) vs
  `pacman` (catalog), `digitalrain_app` (capture) vs `digitalrain`
  (catalog). Also unresolved: the capture's "Morse TX" line parsed as one
  app under id `morseradiotx`, while the catalog carries `morse_tx`
  ("Morse TX") and `morseradiotx` ("Morse Radio TX") as two distinct
  entries - not clear if both are real. Treat the catalog as a very good
  reference, not ground truth on its own - if `appstart` on a
  catalog-listed id fails, check live `applist()` output for the actual
  current id before assuming the app doesn't exist, and see
  `mayhem_catalog.txt`'s "2026-09-23 CATALOG PASS" section for the full
  writeup of what's still open.
- **`applist()`'s parser has a known blind spot**: it uses the catalog's
  id list to find entry boundaries in the device's packed, delimiter-free
  output (necessary - verified a naive whitespace/regex split
  mis-parses multi-word names like "Looking Glass" into fake extra
  entries). The tradeoff: any live app whose id isn't in the catalog
  file gets silently merged into the *previous* entry's name rather than
  appearing as its own entry or raising an error. It's not lost data,
  just misattributed - if an entry's name looks unexpectedly long or
  garbled, that's likely why.

## Full command reference — not just the wrapped subset

The driver's `send(cmd, wait)` method accepts ANY of these raw, sending
the string straight to the console — the wrapper methods (`applist`,
`appstart`, `setfreq`, `radioinfo`, `sysinfo`, `button`, `touch`,
`keyboard`, `screenshot`) are convenience only, not the limit of what's
usable. This is the complete list, confirmed live via `help` against the
actual device (not just the wiki):

**System**: `help` (list commands) · `exit` (shutdown console) · `info`
(ChibiOS/build details) · `systime` (uptime ms) · `reboot` · `dfu` (DFU
mode) · `hackrf` (switch to native HackRF firmware - drops the console)
· `sd_over_usb` · `sysinfo` / `radioinfo` (wrapped) · `getres` /
`getflash` / `getdevtype` (device identity queries, not individually
wrapped) · `notif` · `asyncmsg <enable|disable>` (debug log toggle)

**Display**: `screenshot` (saves to SD, wrapped but file-transfer not
implemented - see below) · `screenframe` / `screenframeshort` (screen
content as text/hex - not wrapped, use `send()` directly)

**Memory**: `write_memory <addr> <val>` · `read_memory <addr>` ·
`pmemreset yes` (reset all settings to default - destructive)

**Files** (not wrapped at all yet - use `send()`): `ls <dir>` ·
`unlink <path>` · `mkdir <path>` · `filesize <path>` · `fopen <path>` ·
`fseek <pos>` · `fclose` · `ftruncate` · `fsync` · `ftell` ·
`fread <n>` / `frb <n>` · `fwrite` / `fwb` · `crc32 <path>`. Note:
`fopen`+`fread`/`frb` was tried live this session to pull a screenshot
off SD and returned empty despite `fopen` reporting `ok` - there's a
mode-flag or sequencing detail not yet worked out. Don't assume this path
works until someone gets it working and updates this note.

**Input simulation**: `button [1-8]` (wrapped, refuses 6/DFU) ·
`touch <x> <y>` (wrapped) · `keyboard <hex>` (wrapped, driver hex-encodes
for you) · `accessibility_readall` / `accessibility_readcurr` (list/
describe on-screen widgets - useful for scripted navigation without
guessing coordinates)

**Date/time**: `rtcget` · `rtcset <y> <mo> <d> <h> <mi> <s>`

**CPLD**: `cpld_info <hackrf|portapack>` · `cpld_read <device> <sram|eeprom>`
· `cpld_write <device> <target> <file>` - not used or tested this
session, handle with care, this touches low-level chip config.

**Apps/simulated sensors**: `applist` / `appstart <id>` (wrapped) ·
`gotgps <lat> <lon> [alt] [speed] [sats]` · `gotorientation <angle>` ·
`gotenv <temp> [humidity] [pressure] [light]` · `gotlight <lux>` - feed
fake sensor data to apps that consume it.

**Config**: `settingsreset yes` (delete all INI settings - destructive)
· `flash` (flash utility access)

**Protocol TX** (transmits real RF - confirm authorization before use,
same reasoning as any TX app): `sendpocsag <addr> <msglen> [baud] [type]
[function] [phase]` · `sendflex` (send FLEX pager message, args not
captured this session - check `help` output or the wiki page for exact
syntax before using)

Full official reference:
https://github.com/portapack-mayhem/mayhem-firmware/wiki/usb-serial-console

## What this doesn't cover yet

Pulling files off the SD card (screenshots, captures) over the console's
`ls`/`fread`/`frb` commands isn't wrapped in the driver yet - `screenshot()`
only triggers the on-device save, it doesn't transfer the file. The
`ghostesp-file-transfer` skill solves the equivalent problem for the
other board over WiFi/HTTP; this device's file transfer would need to go
through these serial file commands instead, not built out yet.

Only ever tested with RX apps (`blerx`) during verification - TX apps
transmit real RF. Confirm any TX app is actually intended/authorized
before launching it, the same authorization reasoning as anything else in
this skill tree that can put a signal on the air.
