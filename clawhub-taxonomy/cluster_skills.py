#!/usr/bin/env python3
"""Cluster skill embeddings to discover a natural, data-driven taxonomy.

Normalizes embeddings to unit length (so euclidean distance ~ cosine
distance) and runs HDBSCAN, which picks its own cluster count instead of
forcing a k -- appropriate here since we don't know how many real
categories exist yet.

For each cluster, prints its size and the skills closest to its centroid,
so a human (or Claude) can read a handful of representative examples and
assign a category name -- that's the actual taxonomy-building step, this
script just proposes the groupings.

Usage:
    cluster_skills.py <embeddings_prefix> [--min-cluster-size N] [--samples N]
"""
import json, sys, argparse, time
import numpy as np
from sklearn.preprocessing import normalize
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans
import hdbscan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prefix")
    ap.add_argument("--min-cluster-size", type=int, default=150)
    ap.add_argument("--samples", type=int, default=8)
    ap.add_argument("--pca-dims", type=int, default=50,
                     help="reduce to this many dims before HDBSCAN (0 = skip). "
                          "1024-dim embeddings make tree-based NN search in HDBSCAN "
                          "nearly as slow as brute force -- PCA first is standard practice.")
    ap.add_argument("--min-samples", type=int, default=None,
                     help="HDBSCAN min_samples; lower = less conservative about noise. "
                          "Defaults to min-cluster-size (HDBSCAN's own default) if unset.")
    ap.add_argument("--cluster-selection-method", choices=["eom", "leaf"], default="eom",
                     help="'leaf' tends to give more, smaller, more homogeneous clusters "
                          "instead of eom's fewer/larger/more-conservative ones")
    ap.add_argument("--out", default=None, help="write full assignments here (jsonl)")
    ap.add_argument("--method", choices=["hdbscan", "kmeans"], default="hdbscan",
                     help="kmeans gives full coverage (no noise bucket) -- better for "
                          "proposing a taxonomy over a long-tail catalog like this one")
    ap.add_argument("--k", type=int, default=40, help="cluster count for --method kmeans")
    args = ap.parse_args()

    vecs = np.load(args.prefix + ".npy")
    meta = [json.loads(l) for l in open(args.prefix + "_meta.jsonl", encoding="utf-8")]
    assert len(vecs) == len(meta), f"mismatch: {len(vecs)} vecs vs {len(meta)} meta rows"
    print(f"loaded {len(vecs)} embeddings, dim {vecs.shape[1]}")

    vecs = normalize(vecs)

    if args.pca_dims and args.pca_dims < vecs.shape[1]:
        t0 = time.time()
        pca = PCA(n_components=args.pca_dims, random_state=0)
        vecs = pca.fit_transform(vecs)
        vecs = normalize(vecs)  # renormalize after PCA
        print(f"PCA {pca.n_components_} dims, "
              f"{pca.explained_variance_ratio_.sum()*100:.1f}% variance retained, "
              f"{time.time()-t0:.1f}s")

    t0 = time.time()
    if args.method == "kmeans":
        print(f"running KMeans (k={args.k})...", flush=True)
        clusterer = KMeans(n_clusters=args.k, random_state=0, n_init=10)
        labels = clusterer.fit_predict(vecs)
        print(f"KMeans done in {time.time()-t0:.1f}s")
    else:
        print("running HDBSCAN...", flush=True)
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=args.min_cluster_size,
            min_samples=args.min_samples,
            cluster_selection_method=args.cluster_selection_method,
            metric="euclidean",
            core_dist_n_jobs=-1,
        )
        labels = clusterer.fit_predict(vecs)
        print(f"HDBSCAN done in {time.time()-t0:.1f}s")

    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise = int((labels == -1).sum())
    print(f"{n_clusters} clusters, {n_noise} noise points ({n_noise/len(labels)*100:.1f}%)")
    print()

    # sort clusters by size, largest first; noise (-1) last
    from collections import Counter
    counts = Counter(labels)
    order = sorted([c for c in counts if c != -1], key=lambda c: -counts[c])

    for c in order:
        idx = np.where(labels == c)[0]
        centroid = vecs[idx].mean(axis=0)
        centroid /= np.linalg.norm(centroid)
        sims = vecs[idx] @ centroid
        top = idx[np.argsort(-sims)[:args.samples]]

        print(f"--- cluster {c}  (n={len(idx)}) ---")
        for i in top:
            name = (meta[i]["name"] or "").strip()[:70]
            desc = (meta[i]["desc"] or "").strip().replace("\n", " ")[:100]
            print(f"  {name} :: {desc}")
        print()

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            for i, m in enumerate(meta):
                f.write(json.dumps({**m, "cluster": int(labels[i])}, ensure_ascii=False) + "\n")
        print(f"wrote full assignments to {args.out}")


if __name__ == "__main__":
    main()
