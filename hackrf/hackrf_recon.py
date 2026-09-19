#!/usr/bin/env python3
"""hackrf_recon.py - passive wideband RF reconnaissance waterfall/detector.

Takes an IQ capture (hackrf_transfer output) and produces:
  * a waterfall spectrogram (PNG)
  * a list of detected narrowband signals above the noise floor

The DC-spike artifact (HackRF center frequency) is masked out.
Fully parameterized via CLI args; defaults match a 430-450 MHz scan window.

Typical capture:
  hackrf_transfer -f 440000000 -s 20e6 -l 20 -g 20 -n <N> -r capture.raw

Usage:
  python3 hackrf_recon.py --lower 430e6 --upper 450e6 --sample-rate 20e6 \\
      --capture capture.raw --out screenshot.png
"""
import argparse
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lower", type=float, default=430e6, help="window lower edge Hz")
    ap.add_argument("--upper", type=float, default=450e6, help="window upper edge Hz")
    ap.add_argument("--sample-rate", type=float, default=20e6, help="hackrf sample rate")
    ap.add_argument("--capture", required=True, help="path to raw int8 IQ capture")
    ap.add_argument("--out", default="recon.png", help="output PNG path")
    ap.add_argument("--nfft", type=int, default=16384)
    ap.add_argument("--hop", type=int, default=8192)
    ap.add_argument("--floor-db", type=float, default=6.0,
                    help="detection threshold: dB above noise median")
    args = ap.parse_args()

    filesize = os.path.getsize(args.capture)
    center = (args.lower + args.upper) / 2.0
    nfft, hop, fac = args.nfft, args.hop, 1.0 / 128.0
    freq = np.fft.fftshift(np.fft.fftfreq(nfft, 1.0 / args.sample_rate)) + center

    acc = np.zeros(nfft)
    frames = []
    npix = 1000
    chunk_bytes = int(120e6)
    offset = 0
    while offset < filesize:
        nb = int(min(chunk_bytes, filesize - offset))
        raw = np.fromfile(open(args.capture, "rb"), dtype=np.int8,
                          count=nb, offset=offset)
        iq = (raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)) * fac
        ns = len(iq)
        nf = (ns - nfft) // hop
        for i in range(nf):
            w = np.hanning(nfft) * iq[i * hop:i * hop + nfft]
            acc += np.abs(np.fft.fftshift(np.fft.fft(w)))
        step = max(nf // npix, 1)
        for i in range(0, nf, step):
            w = np.hanning(nfft) * iq[i * hop:i * hop + nfft]
            frames.append(np.abs(np.fft.fftshift(np.fft.fft(w))))
        offset += nb

    spec_db = 20 * np.log10(acc + 1e-9)
    frames = np.array(frames[:npix])
    F = 20 * np.log10(frames + 1e-9)

    # Mask the DC spike (HackRF center-frequency artifact).
    mask = np.abs(freq - center) > 150e3
    base = np.median(spec_db[mask])
    thr = base + args.floor_db
    sel = np.where(mask & (spec_db > thr))[0]
    groups = []
    if len(sel):
        cur = [sel[0]]
        for i in range(1, len(sel)):
            if sel[i] - sel[i - 1] > 60:
                groups.append(cur)
                cur = [sel[i]]
            else:
                cur.append(sel[i])
        groups.append(cur)

    print("DC-masked noise floor: %.1f dB, detection threshold %.1f dB" % (base, thr))
    if groups:
        for g in groups:
            f0 = freq[g].mean() / 1e6
            span = (freq[g].max() - freq[g].min()) / 1e3
            pk = spec_db[g].max() - base
            print("  %7.3f MHz  peak +%.1f dB  span %.0f kHz" % (f0, pk, span))
    else:
        print("  no signals above threshold")

    t = np.arange(frames.shape[0])
    fig, ax = plt.subplots(figsize=(15, 7))
    im = ax.pcolormesh(freq / 1e6, t, F, shading="auto",
                       cmap="inferno", vmin=base - 20, vmax=base + 40)
    plt.colorbar(im, ax=ax, label="dB")
    ax.set_xlabel("Frequency (MHz)")
    ax.set_ylabel("time frame")
    ax.set_title("hackrf_recon - %d-%d MHz" % (center / 1e6 - 10, center / 1e6 + 10))
    plt.savefig(args.out, dpi=110)
    print("saved %s" % args.out)


if __name__ == "__main__":
    main()