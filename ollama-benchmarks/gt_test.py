#!/usr/bin/env python3
"""gt_test.py — single-string translation probe against Google's free
public 'gtx' endpoint (the one the translate web widget uses).

No API key, no billing. Fine for a handful of strings; NOT viable for bulk
work — the endpoint rate-limits the source IP hard after roughly 2,000-2,500
requests (discovered the hard way on a 25k-item corpus).

Usage:
    python3 gt_test.py
"""

import urllib.request, urllib.parse, json, time

def gt(text, sl="auto", tl="en"):
    """Translate one string. sl='auto' detects the source language.
    Returns the translated text, or raises on HTTP error (429 = banned)."""
    q = urllib.parse.urlencode({"client": "gtx", "sl": sl, "tl": tl,
                                "dt": "t", "q": text})
    url = "https://translate.googleapis.com/translate_a/single?" + q
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    d = json.load(urllib.request.urlopen(req, timeout=15))
    # response shape: [[segment, original, ...], ...] — join the segments
    return "".join(seg[0] for seg in d[0] if seg[0])

# --- CJK samples (the actual use case: Chinese skill descriptions) ----------
samples = ["人工智能产品经理教练", "蓝牙扫描器", "无线电频谱分析仪"]
t = time.time()
for s in samples:
    print("  ->", gt(s))
print(f"  ({time.time() - t:.2f}s for 3)")

# --- mixed scripts: does auto-detect handle Cyrillic / Japanese? -------------
for s in ["Мониторинг Bluetooth устройств", "Wi-Fi スキャンツール"]:
    print("  ->", gt(s))
