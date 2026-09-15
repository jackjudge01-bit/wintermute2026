#!/usr/bin/env python3
"""Live FM monitor: HackRF -> FM demod -> WAV file (append), sox play.
Usage: live_monitor2.py [freq_hz] [out.wav]
"""
import subprocess, sys, time, numpy as np, wave

FREQ = int(sys.argv[1]) if len(sys.argv) > 1 else 142_095_000
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/ecomm/live.wav"
RATE = 2_000_000
AUDIO = 48_000
DECIM = 40          # 2M -> 50k
BLOCK = 100_000     # complex samples per read

print(f"Live FM monitor: {FREQ/1e6:.3f} MHz -> {OUT}", flush=True)
proc = subprocess.Popen(
    ["hackrf_transfer", "-f", str(FREQ), "-s", str(RATE),
     "-l", "24", "-g", "24", "-a", "1", "-r", "-"],
    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=16_000_000)

w = wave.open(OUT, "wb")
w.setnchannels(1); w.setsampwidth(2); w.setframerate(AUDIO)
last = 0 + 0j
t0 = time.time()

def resample_to(x, ratio):
    n = max(1, int(len(x) / ratio))
    idx = np.linspace(0, len(x) - 1, n)
    return np.interp(idx, np.arange(len(x)), x)

try:
    while True:
        raw = proc.stdout.read(BLOCK * 8)
        if not raw:
            break
        iq = np.frombuffer(raw, dtype=np.int8).astype(np.float32) / 128.0
        c = iq[0::2] + 1j * iq[1::2]
        c = c.reshape(-1, DECIM).mean(axis=1)     # 50 kS/s
        y = c * np.conj(np.concatenate(([last], c[:-1])))
        last = c[-1]
        audio = np.angle(y)
        audio = resample_to(audio, 50_000 / AUDIO) # 48 kS/s
        audio = np.clip(audio * 5.0, -1, 1)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())
        el = time.time() - t0
        print(f"\r{el:6.1f}s captured", end="", flush=True)
except KeyboardInterrupt:
    pass
finally:
    proc.terminate()
    w.close()
    print("\nStopped.")
