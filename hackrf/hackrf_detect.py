#!/usr/bin/env python3
"""
hackrf_detect.py — Detect HackRF PortaPack Mayhem on USB, identify port and firmware.

Scans USB serial ports for a PortaPack in Mayhem mode (VID 1d50, PID 6018).
On success, queries firmware version and device info, writes results to
hackrf-elk.conf for other tools in the family.
"""

import sys
import os
import time
import glob
import threading
import configparser
import argparse

try:
    import serial
except ImportError:
    print("ERROR: pyserial is required (python3 -m pip install pyserial)")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(PROJECT_DIR, 'hackrf-elk.conf')

USB_VID = '1d50'
USB_PID_MAYHEM = '6018'
USB_PID_HACKRF = '6089'

BAUD = 115200


# ---------------------------------------------------------------------------
# USB device detection
# ---------------------------------------------------------------------------

def find_portapack_ports():
    """Find serial ports belonging to a PortaPack in Mayhem mode.
    Returns list of (port_path, vid, pid) tuples."""
    results = []
    for devpath in sorted(glob.glob('/dev/ttyACM*')):
        devname = os.path.basename(devpath)
        # Read VID/PID from sysfs
        sysfs_pattern = f'/sys/class/tty/{devname}/device/../idVendor'
        try:
            # Walk up to find the USB device
            sysfs_base = os.path.realpath(f'/sys/class/tty/{devname}/device')
            # Go up until we find idVendor
            check = sysfs_base
            for _ in range(5):
                vid_path = os.path.join(check, 'idVendor')
                pid_path = os.path.join(check, 'idProduct')
                if os.path.exists(vid_path):
                    with open(vid_path) as f:
                        vid = f.read().strip().lower()
                    with open(pid_path) as f:
                        pid = f.read().strip().lower()
                    if vid == USB_VID:
                        results.append((devpath, vid, pid))
                    break
                check = os.path.dirname(check)
        except (OSError, IOError):
            continue
    return results


def check_hackrf_mode():
    """Check if a HackRF is connected in raw SDR mode (wrong mode for us)."""
    try:
        with open('/proc/bus/usb/devices', 'r') as f:
            pass  # not always available
    except:
        pass
    # Check via lsusb-style sysfs
    for devpath in sorted(glob.glob('/sys/bus/usb/devices/*/idVendor')):
        try:
            with open(devpath) as f:
                vid = f.read().strip().lower()
            if vid == USB_VID:
                pid_path = devpath.replace('idVendor', 'idProduct')
                with open(pid_path) as f:
                    pid = f.read().strip().lower()
                if pid == USB_PID_HACKRF:
                    return True
        except (OSError, IOError):
            continue
    return False


# ---------------------------------------------------------------------------
# Serial communication (minimal, just for detection)
# ---------------------------------------------------------------------------

class HackRFProbe:
    """Minimal serial probe — just enough to query device info."""

    def __init__(self, port):
        self.port = port
        self.ser = serial.Serial(port, BAUD, timeout=0.1)
        time.sleep(0.5)
        self.ser.reset_input_buffer()
        self._buf = []
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        # Prime the shell
        self.ser.write(b'\r\n')
        time.sleep(0.3)
        self._buf.clear()

    def _read_loop(self):
        while not self._stop.is_set():
            try:
                chunk = self.ser.read(4096)
                if chunk:
                    self._buf.append(chunk)
            except (serial.SerialException, OSError, TypeError):
                break

    def cmd(self, command, wait=1.0):
        self._buf.clear()
        self.ser.write((command + '\r\n').encode())
        time.sleep(wait)
        out = b''.join(self._buf)
        self._buf.clear()
        return out.decode('utf-8', errors='replace')

    def is_mayhem_shell(self):
        """Check if this port responds to the Mayhem ChibiOS shell."""
        # Try twice in case of stale buffer
        for _ in range(2):
            resp = self.cmd('help', wait=1.5)
            if 'applist' in resp and 'ls' in resp:
                return True
        return False

    def get_sysinfo(self):
        """Query sysinfo for uptime and heap."""
        resp = self.cmd('sysinfo', wait=1.0)
        info = {}
        for line in resp.split('\r\n'):
            line = line.strip()
            if ':' in line and not line.startswith('ch>'):
                key, _, val = line.partition(':')
                info[key.strip()] = val.strip()
        return info

    def get_device_type(self):
        """Query device type."""
        resp = self.cmd('getdevtype', wait=0.5)
        for line in resp.split('\r\n'):
            line = line.strip()
            if line and line != 'ch>' and not line.startswith('ch> ') and line != 'getdevtype':
                return line
        return 'unknown'

    def get_firmware_version(self):
        """Try to determine firmware version from getflash or sysinfo."""
        resp = self.cmd('getflash', wait=0.5)
        for line in resp.split('\r\n'):
            line = line.strip()
            if line and line != 'ch>' and not line.startswith('ch> ') and line != 'getflash' and line != 'ok':
                return line
        return 'unknown'

    def get_app_count(self):
        """Count installed apps."""
        resp = self.cmd('applist', wait=2.0)
        count = 0
        for line in resp.split('\r\n'):
            line = line.strip()
            if '[RX]' in line or '[TX]' in line or '[TRX]' in line or '[UTIL]' in line or '[DEBUG]' in line:
                # Each line may have multiple apps (space-separated entries)
                count += line.count('[RX]') + line.count('[TX]') + line.count('[TRX]') + line.count('[UTIL]') + line.count('[DEBUG]')
        return count

    def get_sd_free_info(self):
        """List SD root to confirm SD card is present."""
        resp = self.cmd('ls /', wait=1.0)
        dirs = []
        for line in resp.split('\r\n'):
            line = line.strip()
            if line.endswith('/') and line != 'ch>' and not line.startswith('ch> ') and line != 'ls /':
                dirs.append(line)
        return dirs

    def close(self):
        self._stop.set()
        try:
            self.ser.close()
        except:
            pass


# ---------------------------------------------------------------------------
# Config file management
# ---------------------------------------------------------------------------

def write_config(data):
    """Write device state to hackrf-elk.conf."""
    config = configparser.ConfigParser()
    config['device'] = data
    with open(CONFIG_FILE, 'w') as f:
        config.write(f)


def clear_config():
    """Write a disconnected state config."""
    write_config({
        'connected': 'false',
        'port': '',
        'mode': '',
        'firmware': '',
        'device_type': '',
        'apps': '0',
        'sd_card': 'false',
        'last_seen': '',
    })


def read_config():
    """Read current config. Returns dict or None."""
    if not os.path.exists(CONFIG_FILE):
        return None
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    if 'device' in config:
        return dict(config['device'])
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Detect HackRF PortaPack Mayhem on USB and write config.')
    parser.add_argument('-n', '--no-write', action='store_true',
                        help='Display only — do not write or overwrite hackrf-elk.conf')
    args = parser.parse_args()
    dry_run = args.no_write

    print("HackRF Detect — PortaPack Mayhem Device Scanner")
    print("=" * 50)
    if dry_run:
        print("(display only — config will not be written)")
    print()

    # Step 1: Check for HackRF in wrong mode
    if check_hackrf_mode():
        print("⚠  HackRF found in SDR passthrough mode (PID 6089)")
        print("   This is HackRF Mode — no serial shell available.")
        print("   Reboot the device to normal Mayhem mode (touchscreen menu).")
        print()
        if not dry_run:
            clear_config()
        sys.exit(1)

    # Step 2: Scan for Mayhem serial ports
    print("Scanning USB serial ports...")
    ports = find_portapack_ports()

    if not ports:
        print("✗  No PortaPack found on USB.")
        print("   Check: device powered on, USB cable connected, Mayhem mode (not HackRF mode).")
        print()
        if not dry_run:
            clear_config()
        sys.exit(1)

    # Filter for Mayhem PID
    mayhem_ports = [(p, v, pid) for p, v, pid in ports if pid == USB_PID_MAYHEM]

    if not mayhem_ports:
        print("✗  Found OpenMoko USB device but not in Mayhem mode.")
        for p, v, pid in ports:
            print(f"   {p}: VID={v} PID={pid}")
        if not dry_run:
            clear_config()
        sys.exit(1)

    # Step 3: Probe each candidate port
    device_found = False
    for port_path, vid, pid in mayhem_ports:
        print(f"   Probing {port_path} (VID:{vid} PID:{pid})...")

        try:
            probe = HackRFProbe(port_path)
        except serial.SerialException as e:
            print(f"   ✗  Cannot open {port_path}: {e}")
            continue

        if not probe.is_mayhem_shell():
            print(f"   ✗  {port_path} is not a Mayhem shell (different device?)")
            probe.close()
            continue

        # Found it
        print(f"   ✓  Mayhem shell active on {port_path}")
        print()

        # Query device info
        print("Querying device info...")
        dev_type = probe.get_device_type()
        firmware = probe.get_firmware_version()
        sysinfo = probe.get_sysinfo()
        app_count = probe.get_app_count()
        sd_dirs = probe.get_sd_free_info()

        uptime = sysinfo.get('uptime', '?')
        m0_heap = sysinfo.get('M0 heap', '?')

        print()
        print("Device Information")
        print("-" * 40)
        print(f"  Port:           {port_path}")
        print(f"  Device type:    {dev_type}")
        print(f"  Firmware:       {firmware}")
        print(f"  Uptime:         {uptime}s")
        print(f"  M0 heap free:   {m0_heap} bytes")
        print(f"  Installed apps: {app_count}")
        print(f"  SD card:        {'Yes (' + str(len(sd_dirs)) + ' dirs)' if sd_dirs else 'Not detected'}")
        print()

        # Write config
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        if dry_run:
            print("(dry run — config not written)")
        else:
            write_config({
                'connected': 'true',
                'port': port_path,
                'mode': 'mayhem',
                'firmware': firmware,
                'device_type': dev_type,
                'apps': str(app_count),
                'sd_card': 'true' if sd_dirs else 'false',
                'uptime': uptime,
                'last_seen': timestamp,
            })
            print(f"Config written to {CONFIG_FILE}")
        probe.close()
        device_found = True
        break

    if not device_found:
        print("✗  No Mayhem shell found on any matching port.")
        if not dry_run:
            clear_config()
        sys.exit(1)

    print()
    print("Done. Other tools will read device config from hackrf-elk.conf")


if __name__ == '__main__':
    main()
