# hackrf-fm-monitor

Live narrowband FM demodulator for the HackRF One SDR. Tunes to a frequency,
streams IQ samples over USB, demodulates FM in pure Python (numpy), and writes
continuous 16-bit 48 kHz WAV audio you can play with any player.

Developed on Kali Linux against a HackRF One r10 to monitor local emergency
radio dispatch (analog VHF FM repeaters, e.g. BCAS ambulance dispatch on
142.095/142.605 MHz). Vancouver's E-Comm trunked system is P25 Phase II and
encrypted, so this targets the analog channels that remain in the clear.

## Requirements

- HackRF One (any revision), USB connected
- `hackrf_transfer` CLI (hackrf package)
- Python 3 with `numpy`

## Usage

```bash
python3 hackrf-fm-monitor.py [frequency_hz] [output.wav]
```

Defaults: 142.095 MHz → `live.wav`.

Examples:

```bash
# monitor BCAS Vancouver 4 dispatch (142.095 MHz)
python3 hackrf-fm-monitor.py 142095000 bcas.wav

# monitor a local 2m amateur repeater
python3 hackrf-fm-monitor.py 146940000 ham.wav
```

Listen while it records (follows file growth on rerun, or loop it):

```bash
sox live.wav -d          # play what's captured so far
```

## How it works

1. `hackrf_transfer` streams raw int8 IQ at 2 MSPS to stdout
2. Python reads 100k-sample blocks, decimates 40x to 50 kS/s (complex)
3. FM demodulation via conjugate-multiply phase difference (discriminator)
4. Resampled to 48 kHz, gain-normalized, clipped, written as PCM16 WAV

Gain settings: LNA 24 / VGA 24 — good balance for strong local repeaters.
Raise for weak signals, lower if nearby transmitters saturate.

## Notes

- The HackRF int8 format is 2 bytes per sample (I + Q), so the script reads
  `block_size * 8` bytes per block
- There is a DC spike at the exact center frequency; offset-tune by a few
  hundred kHz if your target sits right on it
- Wave file grows ~5.7 MB/min; for long sessions pipe to a compressor instead

## Related

For one-shot analysis rather than live monitoring, see the
[hackrf-sdr skill](https://github.com/jackjudge01-bit/wintermute2026) tooling:
`hackrf_sweep` for wideband discovery, then capture IQ with `hackrf_transfer`
and demod offline.
