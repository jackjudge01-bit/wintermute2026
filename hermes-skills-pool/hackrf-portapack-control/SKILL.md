---
name: hackrf-portapack-control
description: Drive a HackRF One + PortaPack running Mayhem firmware over its official USB serial console - launch any of its ~93-98 on-device apps (RX/TX/utility), read radio/system state, and know what each app does via the bundled catalog. Use this whenever the user wants to control, query, or launch something on the PortaPack/Mayhem device, asks what RF tools/apps are available on the HackRF, or mentions the HackRF/PortaPack by name in the context of doing something with it (not just analyzing an already-captured file - see hackrf_recon.py for that separate, unrelated post-processing tool).
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
  Cross-checked live `applist` output against `mayhem_apps.json`:
  5 catalog entries weren't confirmed live in one pass (`digitalrain`,
  `foxhunt`, `morse_tx`, `pacman`, `wav_view` - at least one of these,
  `foxhunt`, looks like a stale name for what the live device calls
  `foxhunt_rx`, not a genuinely missing app), and the live device has at
  least one app (`lookingglass`) the catalog file doesn't list at all.
  Treat the catalog as a very good reference, not ground truth on its
  own - if `appstart` on a catalog-listed id fails, check the live
  `applist()` output for the actual current id before assuming the app
  doesn't exist.
- **`applist()`'s parser has a known blind spot**: it uses the catalog's
  id list to find entry boundaries in the device's packed, delimiter-free
  output (necessary - verified a naive whitespace/regex split
  mis-parses multi-word names like "Looking Glass" into fake extra
  entries). The tradeoff: any live app whose id isn't in the catalog
  file gets silently merged into the *previous* entry's name rather than
  appearing as its own entry or raising an error. It's not lost data,
  just misattributed - if an entry's name looks unexpectedly long or
  garbled, that's likely why.

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
