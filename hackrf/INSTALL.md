# INSTALL — hackrf-fm-monitor

How to get the live FM monitor running on a fresh Kali/Debian box.
What it does and why is in [README.md](README.md).

## Prerequisites

- Linux (Kali/Debian/Ubuntu), Python 3.8+
- A HackRF One plugged into USB
- Root or udev access to the HackRF (see below)

## Install

```bash
sudo apt update
sudo apt install hackrf python3-numpy sox
```

- `hackrf` provides the `hackrf_transfer` CLI the script shells out to.
- `sox` is only needed if you want to play the WAV while recording
  (the script itself never plays audio).

If you prefer pip for numpy: `pip install numpy` (use a venv on PEP 668
systems; `python3-numpy` from apt is simpler).

## Device setup (USB permissions)

The HackRF needs write access to USB. Two options:

1. Non-root via udev (preferred):

```bash
sudo usermod -aG plugdev "$USER"
# log out and back in, then verify:
groups | grep -q plugdev && echo ok
```

The `hackrf` package ships the udev rule
(`/lib/udev/rules.d/..hackrf.rules`, matching 1d50:6089) that grants
plugdev access — replug the HackRF after installing.

2. Or just run the script with `sudo` (works but files end up root-owned).

## Verification

```bash
hackrf_info
```

Expected output: board identity, firmware version, and serial number of
your HackRF One — no "hackrf_open() failed" error. If the USB permissions
are wrong, this is the command that tells you.

```bash
python3 -c "import numpy; print(numpy.__version__)"
```

## First run

The default output path is `/tmp/ecomm/live.wav`, so create that directory
first (the script does not create it):

```bash
mkdir -p /tmp/ecomm
cd hackrf/
python3 hackrf-fm-monitor.py 142095000 bcas.wav
```

You should see `Live FM monitor: 142.095 MHz -> ...` and a running
`captured` counter. Ctrl-C stops it and closes the WAV cleanly.

Listen to what has been captured so far:

```bash
sox bcas.wav -d
```

## Troubleshooting

- **hackrf_open() failed** — permissions; see device setup above, and
  unplug/replug after group changes.
- **No audio / noise** — adjust the fixed LNA/VGA gains (24/24 in the
  script) for your signal strength, and remember there is a DC spike at
  the exact center frequency: offset-tune a few hundred kHz if your
  target sits on it.
- **FileNotFoundError on the WAV** — you skipped `mkdir -p /tmp/ecomm`,
  or pass an explicit output path as the second argument.
