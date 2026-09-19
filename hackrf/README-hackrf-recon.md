# hackrf_recon.py

Passive wideband RF reconnaissance for the HackRF One: turns a raw IQ capture
into a waterfall spectrogram **and** a list of detected narrowband signals
above the noise floor — an SDR-downlink version of PortaPack's "Recon".

Built on wintermute. Verified against real 60-second 430–450 MHz captures.

## What it does

Given an IQ capture (from `hackrf_transfer`), this script:

1. FFTs the I/Q data in chunks (memory-safe for multi-GB files).
2. Builds a time-integrated spectrum and a waterfall PNG.
3. Detects narrowband carriers sitting above the noise floor and prints
   each one's center frequency, peak strength (dB above floor), and width.

The **DC spike** — the HackRF's center-frequency artifact that always shows a
false strong peak — is masked out automatically, so detected signals are real.

## Install

```bash
sudo apt-get install -y hackrf python3-numpy python3-scipy python3-matplotlib
```

No other dependencies. Script is a single self-contained file; drop it in
`~/scripts/` or anywhere on `PATH`.

## Usage

**1. Capture** raw IQ with the HackRF One (e.g. 20 MSPS, centered on 440 MHz,
   60 seconds = 600M samples ≈ 1.2 GB):

```bash
hackrf_transfer -f 440000000 -s 20e6 -l 20 -g 20 -n 600000000 -r capture.raw
```

**2. Analyze:**

```bash
python3 hackrf_recon.py --lower 430e6 --upper 450e6 --sample-rate 20e6 \
    --capture capture.raw --out sweep.png
```

### Options

| Flag | Default | Meaning |
|---|---|---|
| `--lower` | `430e6` | Window lower edge (Hz) |
| `--upper` | `450e6` | Window upper edge (Hz) |
| `--sample-rate` | `20e6` | `hackrf_transfer -s` rate (must match capture) |
| `--capture` | — | **required** — path to raw int8 IQ file |
| `--out` | `recon.png` | Waterfall PNG output path |
| `--nfft` | `16384` | FFT size (higher = finer freq resolution) |
| `--hop` | `8192` | FFT hop between windows |
| `--floor-db` | `6.0` | Detection threshold: dB above noise median |

### Example output

```
DC-masked noise floor: 88.7 dB, detection threshold 94.7 dB
   443.525 MHz  peak +11.4 dB  span 1 kHz
   446.875 MHz  peak +10.2 dB  span 0 kHz
saved sweep.png
```

## Notes & gotchas

- **`-f` in `hackrf_transfer` is Hz; `-f` in `hackrf_sweep` is MHz.** Don't mix
  them up when writing your own capture loops.
- Sample rate in `--sample-rate` **must match** the one used at capture time or
  the frequency axis will be wrong.
- The frequency window is derived from `--lower`/`--upper` (center is
  `(lower+upper)/2`), which must equal the `-f` you captured at.
- Large captures are processed in fixed-size chunks, so megabyte-to-gigabyte
  files are fine with bounded RAM.