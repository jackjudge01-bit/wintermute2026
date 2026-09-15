#!/usr/bin/env python3
"""Identify a BLE device from its advertised name/services, without needing
its owner's cooperation -- passive scan + (optional) GATT enumeration.

Two-stage technique, because no single tool gives you both pieces:
  1. A quick scanner (e.g. an ESP32 Marauder's `list -b`) often gives you a
     device's advertised NAME but not its MAC, or vice versa for unnamed
     devices -- it only ever shows one or the other.
  2. A host-side BLE scan (this script, via `bleak`) gives you the MAC +
     full advertised service UUIDs for a device matched by name, and can
     then connect and enumerate its actual GATT profile (services,
     characteristics) for a much stronger fingerprint than the name alone
     -- e.g. a specific combination of standard Bluetooth SIG services
     (Telephone Bearer Service + Telephony and Media Audio + Media Control
     Service) reliably identifies "this is a phone's LE Audio call/media
     control server", regardless of what the owner named the device.

Gotcha: modern phones/laptops rotate their BLE MAC address periodically
for privacy (this is normal, not evasion) -- an address seen in one scan
may already be gone by the time you try to connect. Rescan immediately
before inspecting if a connect attempt fails with "device_unavailable" /
BleakError.

Usage:
    ble_fingerprint.py scan [--name SUBSTRING] [--timeout SECONDS]
    ble_fingerprint.py inspect <MAC>
"""
import argparse
import asyncio
import re
import sys

from bleak import BleakScanner, BleakClient

OUI_DBS = [
    "/usr/share/ieee-data/oui.txt",
    "/usr/share/nmap/nmap-mac-prefixes",
]


def lookup_oui(mac: str) -> str | None:
    """Best-effort vendor lookup against whatever local OUI databases exist.
    Returns None if not found locally -- doesn't hit the network on its own,
    check https://maclookup.app/api/v2/macs/<oui> by hand if this misses."""
    oui_dash = mac[:8].upper()  # "AA:BB:CC"
    oui_nodash = oui_dash.replace(":", "")
    for path in OUI_DBS:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if oui_dash.replace(":", "-") in line.upper() or oui_nodash in line.upper():
                        return line.strip()
        except FileNotFoundError:
            continue
    return None


def is_randomized_address(mac: str) -> bool:
    """Per the Bluetooth spec, bit 1 of the first octet marks a
    locally-administered (non-manufacturer, often randomized) address."""
    first_octet = int(mac.split(":")[0], 16)
    return bool(first_octet & 0x02)


async def do_scan(name_filter: str | None, timeout: float):
    print(f"Scanning for {timeout}s...", file=sys.stderr)
    devices_and_adv = await BleakScanner.discover(timeout=timeout, return_adv=True)

    for device, adv in devices_and_adv.values():
        name = adv.local_name or device.name
        if name_filter and (not name or name_filter.lower() not in name.lower()):
            continue

        print(f"\n{device.address}  rssi={adv.rssi}  name={name!r}")
        if adv.service_uuids:
            print(f"  services: {adv.service_uuids}")
        if adv.manufacturer_data:
            for company_id, data in adv.manufacturer_data.items():
                print(f"  manufacturer 0x{company_id:04x}: {data.hex()}")

        randomized = is_randomized_address(device.address)
        print(f"  address type: {'randomized/private' if randomized else 'fixed (should be a real OUI)'}")
        if not randomized:
            vendor = lookup_oui(device.address)
            print(f"  OUI lookup: {vendor or 'not found in local databases'}")


async def do_inspect(address: str):
    # BlueZ needs the device in its recent-scan cache to connect by address
    # alone -- connecting cold against an address nobody has seen recently
    # fails with "device 'dev_..' not found", not a timeout. A short
    # targeted scan first ensures it's actually there right now (also
    # sidesteps the MAC-rotation gotcha: if the device changed address
    # since you last saw it, this fails fast here instead of hanging).
    print(f"Scanning for {address}...", file=sys.stderr)
    found = await BleakScanner.find_device_by_address(address, timeout=10.0)
    if not found:
        print(f"'{address}' not seen in a 10s scan -- it may have rotated to a "
              f"new address (privacy randomization) or is out of range/off.",
              file=sys.stderr)
        return

    print(f"Connecting to {address}...", file=sys.stderr)
    async with BleakClient(found) as client:
        services = client.services
        n_services = len(list(services))
        n_chars = sum(len(list(s.characteristics)) for s in services)
        print(f"\n{n_services} services, {n_chars} characteristics\n")

        for service in services:
            print(f"[{service.uuid}] {service.description}")
            for char in service.characteristics:
                props = ",".join(char.properties)
                print(f"    {char.uuid}  ({props})  {char.description}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser("scan")
    scan_p.add_argument("--name", help="only show devices whose advertised name contains this substring")
    scan_p.add_argument("--timeout", type=float, default=10.0)

    inspect_p = sub.add_parser("inspect")
    inspect_p.add_argument("address", help="MAC address to connect to and enumerate GATT services for")

    args = ap.parse_args()

    if args.command == "scan":
        asyncio.run(do_scan(args.name, args.timeout))
    elif args.command == "inspect":
        asyncio.run(do_inspect(args.address))


if __name__ == "__main__":
    main()
