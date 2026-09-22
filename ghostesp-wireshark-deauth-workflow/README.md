# GhostESP live Wireshark capture + deauth workflow

A workflow for capturing WPA handshakes (or any live 802.11 traffic) straight
into Wireshark on the Kali box, using the ESP32/GhostESP purely as a radio —
no SD card, no virtual storage, no file-write-during-capture involved at all.
This sidesteps every crash this whole session was spent chasing in
`ghostesp-virtual-sd/`, because this mode never touches flash storage.

## Why this exists

Every other path this session explored for getting capture data off the
device (SD read over serial, TFTP, HTTP `/api/sdcard/download`, Bluetooth,
UUCP, SCP) either needs new firmware code, a network connection that costs
you your own internet/session access, or doesn't exist on this hardware at
all (Bluetooth Classic/OBEX — this chip has no Classic BT radio, full stop).
This one already exists in GhostESP today, uses the same USB/serial
connection already in use for everything else, and needs zero new firmware
code — just a plugin on the Kali side.

## What it is

GhostESP has a **live streaming capture mode** (`capture -wireshark`,
`main/vendor/pcap.c`: `pcap_wireshark_start()`) that's architecturally
separate from the file-based `capture -probe`/`-deauth`/etc. family this
session spent all day fixing. In this mode, captured frames go straight to
`serial_manager_write_bytes()` — raw bytes out the UART, live, as they're
captured. No buffer-then-flush-to-SD step exists in this path at all.

GhostESP-Revival ships a Wireshark **extcap** plugin
(`scripts/wireshark_extcap_installer` in the repo) that's the other half:
it opens the serial port and feeds whatever comes over it straight into
Wireshark as a live capture interface — Wireshark shows GhostESP as a
capture source, same as any NIC.

**Verified this session, on real hardware**: started `capture -wireshark`,
let it run 6 seconds, saw real live traffic (259 packets, `written=259
dropped=0`), stopped it cleanly with `capture -stop`. No crash. Confirmed
the raw stream contains genuine 802.11 frames (visible SSID fragments —
`SHAW-9C52`, `SHAW-57BA`, `La.sede` — inside the raw bytes).

## The deauth half — and the one combination that doesn't work

To actually force a handshake while this capture is running, you need a
second, concurrently-running command: `attack -d` (plain deauth).

**Do not use `attack -hsd`** (the built-in combined handshake+deauth
attack) for this. Checked in source
(`main/attacks/wifi/deauth_attack.c: deauth_attack_start_handshake_deauth()`):
it calls `esp_wifi_set_promiscuous_rx_cb()` itself to register its own
EAPOL-only callback. Only one promiscuous callback can be registered on
this chip at a time — starting `-hsd` while `capture -wireshark` is running
would **silently hijack and replace** the wireshark stream's callback,
killing your live capture and switching to `-hsd`'s own internal
file-based capture path instead (the SD-writing one, exactly what this
workflow exists to avoid).

**Use plain `attack -d` instead.** Checked in source
(`deauth_attack_start_station()`): it only calls `esp_wifi_80211_tx()` to
inject deauth frames — it never touches
`esp_wifi_set_promiscuous_rx_cb()`/`esp_wifi_set_promiscuous()` at all. It
doesn't disturb whatever capture is already running; it just transmits on
the same channel while the capture keeps listening. This is the verified,
safe combination:

```
capture -wireshark      # start the live stream first
attack -d               # then inject deauth frames while it's running
```

Not yet tested end-to-end (the `attack -d` half specifically — capture
alone is verified above): needs a target AP, and that has to be one you
have authorization to deauth. **Every AP visible in range during this
session was a real, unidentified neighboring network** — this workflow
must not be pointed at one of those without confirming it's yours or
otherwise authorized.

## Setup on the Kali box

1. **Get the extcap plugin.**
   ```
   git clone --branch Development-deki --single-branch \
     https://github.com/GhostESP-Revival/GhostESP.git
   ```
   The plugin is at `scripts/wireshark_extcap_installer/` —
   `ghostesp_extcap.py` (the real integration script) plus
   `ghostesp_extcap.bat` (a Windows wrapper, not relevant here).

2. **The installer as packaged is Windows-oriented.** It installs to
   `%APPDATA%\Wireshark\extcap\` and its packaging (`.bat` wrapper, GUI
   installer, "COM port" language) assumes Windows. The underlying
   `ghostesp_extcap.py` itself is Python + PySerial, which isn't inherently
   Windows-only — but getting it running on Linux/Kali means manually
   dropping it into Wireshark's Linux extcap directory rather than running
   the provided installer:
   ```
   mkdir -p ~/.config/wireshark/extcap
   cp scripts/wireshark_extcap_installer/ghostesp_extcap.py \
     ~/.config/wireshark/extcap/
   chmod +x ~/.config/wireshark/extcap/ghostesp_extcap.py
   pip install pyserial   # if not already present
   ```
   **Not yet verified** that the script runs correctly unmodified on Linux
   (path separators, `/dev/ttyACM0` vs `COMx` handling, etc. haven't been
   checked against the actual script source) — this is the next thing to
   confirm before relying on this step.

3. **Free the serial port.** Bifrost (`bifrost.service`)'s `ghostesp` MCP
   client holds `/dev/ttyACM0` between commands but not continuously — check
   with `ss -ltnp | grep 8080` / `journalctl -u bifrost.service` if in
   doubt. For a live Wireshark capture, stop it outright so nothing else
   contends for the port during the capture session:
   ```
   sudo systemctl stop bifrost.service
   ```
   Restart it after (`sudo systemctl start bifrost.service`) once done —
   otherwise `hermes-offsec` loses its `ghostesp` MCP tools.

4. **Open Wireshark**, pick the GhostESP interface from the capture list
   (appears once the extcap script is correctly installed and the device is
   plugged in), start the capture.

## On the device (over the same serial connection, or the Wireshark-driven
capture itself once step 4 is working)

```
capture -wireshark        # starts the live stream Wireshark is reading
attack -d                 # after selecting a target AP: forces a reconnect
capture -stop              # when done
```

Target selection (`select -a <idx>` after a `scanap` scan) has to happen
before `attack -d` — see `cmd_wifi.c`'s `select`/`attack` commands. Confirm
authorization for whichever AP is selected before running `attack -d`
against it.

## Open items

- Linux compatibility of `ghostesp_extcap.py` itself: unverified.
- The `attack -d` + `capture -wireshark` combination: verified independent
  in source (no shared state conflict), not yet run together end-to-end
  against a real target — blocked on having an authorized target AP to
  test against.
- Whether Wireshark's live decode correctly parses the radiotap + 802.11
  framing GhostESP emits in this mode: not checked. The raw capture in this
  session's test was inspected as a byte stream (visible SSID fragments),
  not opened in Wireshark itself.
