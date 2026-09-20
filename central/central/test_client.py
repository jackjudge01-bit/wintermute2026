#!/usr/bin/env python3
"""End-to-end test of the central test server."""
import json
import urllib.request

import psycopg

BASE = "http://127.0.0.1:8090"
DB = "postgresql://devicedb:devicedb@127.0.0.1:5432/devicedb_central"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


print("=== 1. register contributor ===")
print(json.dumps(call("POST", "/v1/contributors", {"handle": "jack-test"}), indent=2))

print("\n=== 2. contribute 2 sightings of the SAME mac (diff case) + precise GPS ===")
res = call("POST", "/v1/contribute", {
    "handle": "jack-test",
    "signature": "sig-placeholder",
    "observations": [
        {"observed_at": "2026-09-17T13:00:00Z", "signal": "ble",
         "mac": "AA:BB:CC:11:22:33", "name": "LE-Bose Revolve+", "rssi": -61,
         "vendor": "Bose", "lat": 49.18361234, "lon": -123.11645678,
         "place": "Richmond, BC"},
        {"observed_at": "2026-09-17T13:00:05Z", "signal": "ble",
         "mac": "aa:bb:cc:11:22:33", "name": "LE-Bose Revolve+", "rssi": -70,
         "vendor": "Bose", "lat": 49.18361234, "lon": -123.11645678},
    ],
})
print(json.dumps(res, indent=2))

print("\n=== 3. contribute device knowledge (fingerprint -> model) ===")
print(json.dumps(call("POST", "/v1/fingerprints", {
    "fingerprint": "ble_mfr:0x00d2:0x2c", "signal_type": "ble_mfr",
    "vendor": "Bose", "model_name": "Bose SoundLink Revolve+",
    "device_class": "speaker",
    "capabilities": {"bands_tx": ["2.4GHz"], "bands_rx": ["2.4GHz"],
                     "firmware": "1.8.2", "abilities": ["BLE adv", "A2DP"],
                     "weaknesses": ["no 5GHz"]},
    "confidence": 0.9,
}), indent=2))

print("\n=== 4a. resolve a KNOWN fingerprint (the Ring-doorbell flow) ===")
fpres = call("GET", "/v1/fingerprint?fp=ble_mfr:0x00d2:0x2c")
print(json.dumps(fpres, indent=2))
model_id = fpres["matches"][0]["model_id"]

print("\n=== 4b. resolve an UNKNOWN fingerprint (-> needs research) ===")
print(json.dumps(call("GET", "/v1/fingerprint?fp=ble_mfr:0xdead:0xbeef"), indent=2))

print("\n=== 5. capability KB lookup by model_id ===")
print(json.dumps(call("GET", f"/v1/models/{model_id}"), indent=2))

print("\n=== 6. search ===")
print(json.dumps(call("GET", "/v1/search?vendor=Bose"), indent=2))

print("\n=== 7. DB proof: pseudonymization + clustering + coarsening ===")
with psycopg.connect(DB) as conn, conn.cursor() as cur:
    cur.execute("SELECT mac_hash, device_id, salt_epoch FROM observations ORDER BY obs_id")
    rows = cur.fetchall()
    print(f"raw MAC sent was AA:BB:CC:11:22:33")
    print(f"stored mac_hash (obs1): {rows[0][0]}")
    print(f"stored mac_hash (obs2): {rows[1][0]}")
    print(f"identical hashes (deterministic pseudonymization): {rows[0][0] == rows[1][0]}")
    print(f"same device cluster for both sightings: {rows[0][1] == rows[1][1]}")
    cur.execute("SELECT count(*) FROM devices")
    print(f"device clusters created: {cur.fetchone()[0]}")
    cur.execute("SELECT lat, lon FROM observations ORDER BY obs_id LIMIT 1")
    lat, lon = cur.fetchone()
    print(f"GPS sent: 49.18361234, -123.11645678 -> stored coarsened: {lat}, {lon}")
    cur.execute("SELECT payload_sha256, signature, record_count FROM contributions")
    print(f"ledger row: {cur.fetchone()}")
