#!/usr/bin/env python3
"""Find where a provider's API key already lives, across other local agent
tools' own credential stores, instead of re-entering/re-generating one.

Written getting CAI talking to Venice AI without a fresh signup: OpenClaw
already had a Venice key configured (entered once, in its own UI), sitting
in its local sqlite auth store. Rather than going and generating/pasting a
second key for CAI, this locates the existing one so you can copy it once
into wherever your other tool actually reads its key from (env var, its
own config file, etc).

Deliberately NEVER PRINTS THE ACTUAL KEY VALUE -- only where it lives
(file, JSON path, length) so you can go retrieve it yourself through
whatever your source tool considers a safe path (its own "reveal" UI,
a properly permissioned file read, etc). A script whose whole purpose is
finding secrets should not also be the thing echoing them to your
terminal/shell history/scrollback.

Known local stores checked by default -- add more via --store:
  - OpenClaw:  ~/.openclaw/agents/main/agent/openclaw-agent.sqlite
               (sqlite, table auth_profile_store, JSON blobs per row)
  - Hermes:    ~/.hermes/auth.json
               (plain JSON)

Usage:
    find-provider-key.py <search-term> [--store PATH ...]
"""
import argparse
import json
import os
import sqlite3
import sys

DEFAULT_STORES = [
    "~/.openclaw/agents/main/agent/openclaw-agent.sqlite",
    "~/.hermes/auth.json",
]


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, obj


def search_json_blob(term, data, source_label):
    hits = []
    for path, value in walk(data):
        if not isinstance(value, str) or len(value) < 16:
            continue
        haystack = f"{path} {value}".lower()
        if term.lower() in haystack:
            hits.append((source_label, path, len(value)))
    return hits


AUTH_TABLE_HINTS = ["auth", "credential", "secret", "key", "token", "profile", "provider"]


def search_sqlite(term, db_path, all_tables=False):
    hits = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return hits
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        if not all_tables:
            # Default to auth-shaped tables only -- scanning every table in
            # an agent's sqlite store also matches the search term against
            # ordinary conversation transcripts/history, which is noise,
            # not a credential. Pass --all-tables to search everything.
            tables = [t for t in tables if any(h in t.lower() for h in AUTH_TABLE_HINTS)]
        for table in tables:
            try:
                cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
            except sqlite3.OperationalError:
                continue
            try:
                rows = con.execute(f"SELECT rowid, * FROM {table}").fetchall()
                row_cols = ["rowid"] + cols
            except sqlite3.OperationalError:
                # WITHOUT ROWID tables and some virtual tables (e.g.
                # sqlite_sequence) don't support rowid -- fall back to a
                # plain select rather than losing the whole table's data.
                try:
                    rows = con.execute(f"SELECT * FROM {table}").fetchall()
                    row_cols = cols
                except sqlite3.OperationalError:
                    continue
            for row_num, row in enumerate(rows):
                row_label = f"row#{row_num}" if row_cols is cols else f"rowid={row[0]}"
                for col_name, val in zip(row_cols, row):
                    if not isinstance(val, str):
                        continue
                    # try treating it as a JSON blob first (richer path info)
                    try:
                        parsed = json.loads(val)
                        hits.extend(
                            (f"{db_path}::{table}[{row_label}].{col_name}", p, ln)
                            for _, p, ln in search_json_blob(term, parsed, "")
                        )
                        continue
                    except (json.JSONDecodeError, TypeError):
                        pass
                    if term.lower() in val.lower() and len(val) >= 16:
                        hits.append((f"{db_path}::{table}[{row_label}]", col_name, len(val)))
    finally:
        con.close()
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("term", help="provider name / search term, e.g. 'venice'")
    ap.add_argument("--store", action="append", default=[],
                     help="additional store path to check (sqlite or json), repeatable")
    ap.add_argument("--all-tables", action="store_true",
                     help="scan every sqlite table, not just auth-shaped ones "
                          "(noisier -- will also match ordinary conversation/history content)")
    args = ap.parse_args()

    stores = [os.path.expanduser(p) for p in DEFAULT_STORES] + args.store
    any_hits = False

    for store in stores:
        if not os.path.exists(store):
            continue
        if store.endswith(".sqlite") or store.endswith(".db"):
            hits = search_sqlite(args.term, store, all_tables=args.all_tables)
        else:
            try:
                data = json.load(open(store, encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            hits = [(store, p, ln) for _, p, ln in search_json_blob(args.term, data, store)]

        for source, path, length in hits:
            any_hits = True
            print(f"FOUND: {source}  path={path}  length={length}")

    if not any_hits:
        print(f"Nothing matching '{args.term}' found in: {', '.join(stores)}", file=sys.stderr)
        sys.exit(1)

    print("\nValue NOT printed here on purpose -- go retrieve it from the "
          "source above via that tool's own config/UI, or with a one-off "
          "read you control directly (e.g. sqlite3/jq on the path shown).",
          file=sys.stderr)


if __name__ == "__main__":
    main()
