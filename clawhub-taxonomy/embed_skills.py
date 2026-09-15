#!/usr/bin/env python3
"""Generate embeddings for every canonical (non-dup_variant) skill in
clean_groups.json, using the local Qwen3-Embedding-0.6B model via Ollama.

Embeds `name + ": " + desc` for each skill. Multilingual model, so English
and non-English skills are embedded into the same space -- no need to
cluster them separately.

Checkpointed: writes progress every BATCH items, safe to Ctrl-C and resume.

Usage:
    embed_skills.py <clean_groups.json> <out_prefix>

Produces:
    <out_prefix>.npy        -- float32 array, shape (N, 1024)
    <out_prefix>_meta.jsonl -- one JSON object per row, same order as .npy
                               {slug, name, desc, cat}
"""
import json, sys, os, time
import numpy as np
import requests

OLLAMA_URL = "http://127.0.0.1:11434/api/embed"
MODEL = "hf.co/Qwen/Qwen3-Embedding-0.6B-GGUF"
BATCH = 64


def load_entries(path):
    groups = json.load(open(path, encoding="utf-8"))
    entries = [e for g in groups for e in g["entries"]]
    # canonical only: drop dup_variant (re-published copies of the same skill)
    entries = [e for e in entries if e.get("dup") != "dup_variant"]
    return entries


def embed_batch(texts, retries=3):
    for attempt in range(retries):
        try:
            r = requests.post(OLLAMA_URL, json={"model": MODEL, "input": texts}, timeout=120)
            r.raise_for_status()
            return r.json()["embeddings"]
        except Exception as e:
            if attempt == retries - 1:
                raise
            print(f"  retry {attempt+1} after error: {e}", file=sys.stderr)
            time.sleep(2)


def main(src, out_prefix):
    entries = load_entries(src)
    n = len(entries)
    print(f"{n} canonical entries to embed")

    meta_path = out_prefix + "_meta.jsonl"
    npy_path = out_prefix + ".npy"

    start_idx = 0
    vecs = []
    if os.path.exists(meta_path) and os.path.exists(npy_path):
        existing = np.load(npy_path)
        start_idx = existing.shape[0]
        vecs = [existing]
        print(f"resuming from checkpoint: {start_idx} already done")

    meta_f = open(meta_path, "a", encoding="utf-8")
    t0 = time.time()
    for i in range(start_idx, n, BATCH):
        chunk = entries[i:i + BATCH]
        texts = [f"{e.get('name','')}: {e.get('desc','')}"[:2000] for e in chunk]
        embs = embed_batch(texts)
        vecs.append(np.array(embs, dtype=np.float32))
        for e in chunk:
            meta_f.write(json.dumps({
                "slug": e.get("slug"), "name": e.get("name"),
                "desc": e.get("desc"), "cat": e.get("cat"),
            }, ensure_ascii=False) + "\n")
        meta_f.flush()

        done = min(i + BATCH, n)
        elapsed = time.time() - t0
        rate = (done - start_idx) / elapsed if elapsed > 0 else 0
        eta = (n - done) / rate if rate > 0 else float("inf")
        print(f"{done}/{n}  ({rate:.1f}/s, eta {eta/60:.1f} min)", file=sys.stderr)

        # checkpoint every batch -- cheap at this size, and lets us resume cleanly
        np.save(npy_path, np.concatenate(vecs, axis=0))

    meta_f.close()
    print("done:", npy_path, meta_path)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
