# ble-fingerprint — identifying a BLE device beyond its broadcast name

A quick BLE scanner (e.g. an ESP32 Marauder's `list -b`) only ever shows a
device's advertised **name** *or* its **MAC**, never both for the same
entry — genuinely useless if you want to know what a named device actually
*is*. This is the second stage: a host-side scan that gets both, plus
enough to actually identify the device type when its name is uninformative
or its OUI is unregistered.

## Technique

1. `scan --name <substring>` — find a device by (partial) advertised name,
   get its current MAC + advertised service UUIDs + manufacturer data in
   one shot (`bleak`, not the OS's cached-and-often-stale
   `bluetoothctl devices` list).
2. Check whether the MAC is a real manufacturer OUI or a randomized
   privacy address (bit 1 of the first octet) — a fixed OUI can be looked
   up locally (`/usr/share/ieee-data/oui.txt`, `nmap-mac-prefixes`) for a
   manufacturer name even with zero other information.
3. `inspect <MAC>` — connect and enumerate the full GATT profile
   (services + characteristics). This is the strongest signal: a specific
   combination of *standard* Bluetooth SIG services can identify device
   *role* even when the vendor used a completely custom/undocumented
   128-bit UUID everywhere else. Example from real use: Telephone Bearer
   Service (0x184C) + Telephony and Media Audio (0x1855) + Media Control
   Service (0x1849) together is the standard signature of a phone's LE
   Audio call/media control server — that combination alone identifies
   "this is a phone" regardless of what its owner named it or what
   unknown vendor-specific service sits alongside it.

## Real gotchas hit building this

- **MAC rotation**: phones/laptops rotate their BLE address periodically
  for privacy — an address from one scan can be gone 60 seconds later.
  `inspect` scans for the target address fresh (`find_device_by_address`)
  immediately before connecting rather than assuming a previously-seen
  address is still valid; if it's rotated, you'll get a clear "not seen in
  a 10s scan" instead of a confusing connection failure.
- **BlueZ needs the device in its live scan cache to connect at all** —
  connecting cold to an address nobody has seen recently fails with
  `device 'dev_..' not found`, which looks like a bug but isn't; it means
  "BlueZ doesn't currently know this device exists," not "connection
  failed." The pre-scan above exists specifically to avoid this.
- **Not every device accepts connections.** Some BLE peripherals only
  ever advertise and reject/ignore connection attempts from unrecognized
  centrals (a smart TV in testing did exactly this — advertises fine,
  `inspect` fails at the actual connect step even though the device is
  right there and was just seen in a scan). That's the device's own
  behavior, not a script problem — headphones/earbuds/wearables in the
  same test connected and enumerated without issue.
- **Many custom 128-bit service UUIDs have no public documentation at
  all.** Don't expect every service to resolve to something named — the
  *combination* of the standard ones present is usually more informative
  than chasing an unknown vendor UUID.

## Legal

Passive scanning observes what's already being broadcast publicly, same
as any BLE scanner app. Connecting and enumerating GATT services is a
step further than passive listening — only do this against your own
devices or with clear authorization.
