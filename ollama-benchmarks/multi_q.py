#!/usr/bin/env python3
"""multi_q.py — test whether Google's free 'gtx' translate endpoint accepts
multiple strings in a single request (batching).

Answer: it does — repeat the `q=` parameter and you get one result block
per string. But beware: batching does NOT dodge the per-IP rate limit,
which still bites at volume (~2.3k strings from one IP in our testing).

Usage:
    python3 multi_q.py
"""

import urllib.request, urllib.parse, json, time

def gt_batch(texts, sl="auto", tl="en"):
    """Translate several strings in one HTTP request. Returns the raw
    parsed response (list of result blocks, one per input string)."""
    params = [("client", "gtx"), ("sl", sl), ("tl", tl), ("dt", "t")]
    url = "https://translate.googleapis.com/translate_a/single?" + \
        urllib.parse.urlencode(params)
    # the interesting bit: append q= once per string instead of encoding
    # a list — the endpoint treats repeated params as a batch
    for t in texts:
        url += "&q=" + urllib.parse.quote(t)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    d = json.load(urllib.request.urlopen(req, timeout=25))
    return d

# mixed-language batch: Chinese, Russian, Japanese in one request
batch = ["人工智能产品经理", "蓝牙扫描器", "无线电频谱分析仪",
         "Мониторинг Bluetooth", "Wi-Fi スキャンツール"]
t = time.time()
try:
    d = gt_batch(batch)
    print(f"OK in {time.time() - t:.2f}s  top-level entries: {len(d)}")
    # d[0] is a list of blocks; with multiple q= params each block is
    # itself a list of [translated, original, ...] segment pairs
    for i, block in enumerate(d[0]):
        txt = "".join(seg[0] for seg in block if seg and seg[0]) \
            if block and isinstance(block, list) else block
        print(f"   [{i}] {str(txt)[:70]}")
except Exception as e:
    print("FAILED:", e)
