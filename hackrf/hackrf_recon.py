#!/usr/bin/env python3
"""
hackrf_recon.py — HackRF One + PortaPack Mayhem Recon driver over USB serial.

Drives the Mayhem firmware shell (prompt 'ch> ') over USB CDC serial to:
  * discover and parse FreqMan frequency files on the SD card
  * upload built-in scan presets (Vancouver BC area focus)
  * write Recon config (/SETTINGS/recon.ini + /SETTINGS/rx_tx_recon.ini)
  * launch the Recon app and monitor status

Only dependency: pyserial.
"""

import sys
import os
import time
import glob
import binascii
import threading
import configparser

try:
    import serial
except ImportError:
    print("ERROR: pyserial is required (python3 -m pip install pyserial).")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(PROJECT_DIR, 'hackrf-elk.conf')

USB_VID = 0x1D50
USB_PID = 0x6018
DEFAULT_PORT = '/dev/ttyACM1'
BAUD = 115200
PROMPT = 'ch> '

FREQMAN_DIR = '/FREQMAN'
SETTINGS_DIR = '/SETTINGS'

# Per-preset radio settings, keyed by FREQMAN name (no extension)
PRESET_SETTINGS = {
    'AIRBAND':       {'lna': 32, 'vga': 32, 'amp': 0, 'squelch': -20},
    'MARINE_VHF':    {'lna': 32, 'vga': 32, 'amp': 0, 'squelch': -20},
    'FM_BROADCAST':  {'lna': 16, 'vga': 16, 'amp': 0, 'squelch': -20},
    'PUBLIC_SAFETY': {'lna': 32, 'vga': 32, 'amp': 0, 'squelch': -14},
    'ISM_433':       {'lna': 32, 'vga': 32, 'amp': 0, 'squelch': -30},
    'AMATEUR_2M':    {'lna': 32, 'vga': 32, 'amp': 0, 'squelch': -20},
    'AMATEUR_70CM':  {'lna': 32, 'vga': 32, 'amp': 0, 'squelch': -20},
    'WEATHER':       {'lna': 32, 'vga': 32, 'amp': 0, 'squelch': -20},
    'TPMS':          {'lna': 32, 'vga': 32, 'amp': 0, 'squelch': -30},
    'RAILROAD':      {'lna': 32, 'vga': 32, 'amp': 0, 'squelch': -20},
    'GMRS_FRS':      {'lna': 32, 'vga': 32, 'amp': 0, 'squelch': -20},
}

DEFAULT_SETTINGS = {'lna': 32, 'vga': 32, 'amp': 0, 'squelch': -20}

# Built-in presets (FreqMan file contents, \n line endings on device)
PRESETS = {}

PRESETS['AIRBAND'] = """\
a=118000000,b=136975000,m=AM,bw=DSB 9k,s=25kHz,d=Airband|Aircraft comms
f=121500000,m=AM,bw=DSB 9k,d=Emergency|Guard frequency
f=123450000,m=AM,bw=DSB 9k,d=Air-to-air|Pilot chat
f=128775000,m=AM,bw=DSB 9k,d=Vancouver Tower
f=119550000,m=AM,bw=DSB 9k,d=Vancouver Approach
f=132850000,m=AM,bw=DSB 9k,d=Vancouver Centre
"""

PRESETS['MARINE_VHF'] = """\
f=156000000,m=NFM,bw=16k,d=Marine Ch01
f=156050000,m=NFM,bw=16k,d=Marine Ch02
f=156100000,m=NFM,bw=16k,d=Marine Ch03
f=156150000,m=NFM,bw=16k,d=Marine Ch04
f=156250000,m=NFM,bw=16k,d=Marine Ch05
f=156300000,m=NFM,bw=16k,d=Marine Ch06|Ship-to-ship safety
f=156350000,m=NFM,bw=16k,d=Marine Ch07
f=156400000,m=NFM,bw=16k,d=Marine Ch08
f=156450000,m=NFM,bw=16k,d=Marine Ch09|Calling/boater
f=156500000,m=NFM,bw=16k,d=Marine Ch10
f=156550000,m=NFM,bw=16k,d=Marine Ch11|VTS
f=156600000,m=NFM,bw=16k,d=Marine Ch12|VTS/Port ops
f=156650000,m=NFM,bw=16k,d=Marine Ch13|Bridge-to-bridge
f=156700000,m=NFM,bw=16k,d=Marine Ch14|Port ops
f=156750000,m=NFM,bw=16k,d=Marine Ch15
f=156800000,m=NFM,bw=16k,d=Marine Ch16|Distress and calling
f=157000000,m=NFM,bw=16k,d=Marine Ch20
f=157100000,m=NFM,bw=16k,d=Marine Ch22A|Coast Guard
f=161650000,m=NFM,bw=16k,d=Marine Ch21B
f=162025000,m=NFM,bw=16k,d=AIS Ch87B|AIS 2
f=161975000,m=NFM,bw=16k,d=AIS Ch88B|AIS 1
"""

PRESETS['FM_BROADCAST'] = """\
a=88000000,b=108000000,m=WFM,bw=200k,s=100kHz,d=FM broadcast radio
"""

PRESETS['PUBLIC_SAFETY'] = """\
f=142095000,m=NFM,bw=16k,d=BCAS Dispatch|BC Ambulance
f=142605000,m=NFM,bw=16k,d=BCAS Dispatch 2|BC Ambulance
a=148000000,b=174000000,m=NFM,bw=16k,s=12500,d=VHF public safety
a=450000000,b=470000000,m=NFM,bw=16k,s=12500,d=UHF public safety
"""

PRESETS['ISM_433'] = """\
a=433000000,b=434800000,m=NFM,bw=16k,s=10kHz,d=ISM 433 MHz|Sensors and remotes
"""

PRESETS['AMATEUR_2M'] = """\
a=144000000,b=148000000,m=NFM,bw=16k,s=12500,d=2m amateur radio
f=146520000,m=NFM,bw=16k,d=2m calling frequency
f=146940000,m=NFM,bw=16k,d=VE7RPT Vancouver repeater
"""

PRESETS['AMATEUR_70CM'] = """\
a=430000000,b=450000000,m=NFM,bw=16k,s=25kHz,d=70cm amateur radio
"""

PRESETS['WEATHER'] = """\
f=162400000,m=NFM,bw=16k,d=NOAA Weather 1|WX1
f=162425000,m=NFM,bw=16k,d=NOAA Weather 2|WX2
f=162450000,m=NFM,bw=16k,d=NOAA Weather 3|WX3
f=162475000,m=NFM,bw=16k,d=NOAA Weather 4|WX4
f=162500000,m=NFM,bw=16k,d=NOAA Weather 5|WX5
f=162525000,m=NFM,bw=16k,d=NOAA Weather 6|WX6
f=162550000,m=NFM,bw=16k,d=NOAA Weather 7|WX7
"""

PRESETS['TPMS'] = """\
f=315000000,m=NFM,bw=16k,d=TPMS 315 MHz|US vehicles
f=433920000,m=NFM,bw=16k,d=TPMS 433.92 MHz|EU/Asian vehicles
"""

PRESETS['RAILROAD'] = """\
a=160215000,b=161565000,m=NFM,bw=16k,s=15kHz,d=Railroad|AAR channels
"""

PRESETS['GMRS_FRS'] = """\
f=462562500,m=NFM,bw=16k,d=GMRS/FRS Ch01
f=462587500,m=NFM,bw=16k,d=GMRS/FRS Ch02
f=462612500,m=NFM,bw=16k,d=GMRS/FRS Ch03
f=462637500,m=NFM,bw=16k,d=GMRS/FRS Ch04
f=462662500,m=NFM,bw=16k,d=GMRS/FRS Ch05
f=462687500,m=NFM,bw=16k,d=GMRS/FRS Ch06
f=462712500,m=NFM,bw=16k,d=GMRS/FRS Ch07
f=467562500,m=NFM,bw=16k,d=FRS Ch08
f=467587500,m=NFM,bw=16k,d=FRS Ch09
f=467612500,m=NFM,bw=16k,d=FRS Ch10
f=467637500,m=NFM,bw=16k,d=FRS Ch11
f=467662500,m=NFM,bw=16k,d=FRS Ch12
f=467687500,m=NFM,bw=16k,d=FRS Ch13
f=467712500,m=NFM,bw=16k,d=FRS Ch14
"""

# ---------------------------------------------------------------------------
# Serial communication with the Mayhem shell
# ---------------------------------------------------------------------------


def detect_port():
    """Find the HackRF PortaPack serial port.
    Reads from hackrf-elk.conf first (written by hackrf_detect.py).
    Falls back to USB enumeration if config missing or stale."""
    # Try config file first
    if os.path.exists(CONFIG_FILE):
        config = configparser.ConfigParser()
        config.read(CONFIG_FILE)
        if 'device' in config:
            dev = config['device']
            if dev.get('connected') == 'true' and dev.get('port'):
                port = dev['port']
                if os.path.exists(port):
                    return port
                else:
                    print(f"  ⚠  Config says {port} but device not found there.")
                    print(f"     Run hackrf_detect.py to refresh.")

    # Fallback: scan USB
    if os.path.exists(DEFAULT_PORT):
        return DEFAULT_PORT
    candidates = sorted(glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*'))
    for dev in candidates:
        try:
            import serial.tools.list_ports
            for p in serial.tools.list_ports.comports():
                if p.device == dev:
                    if p.vid == USB_VID and p.pid == USB_PID:
                        return dev
        except Exception:
            pass
    if candidates:
        return candidates[-1]
    return None


class HackRFSerial:
    """Background-reader-thread serial wrapper for the Mayhem shell.

    CRITICAL: fread/fwb block if the host does not continuously drain the
    serial port. A daemon thread keeps reading into a buffer at all times.
    """

    def __init__(self, port=DEFAULT_PORT, baud=BAUD):
        self.ser = serial.Serial(port, baud, timeout=0.1)
        time.sleep(0.5)
        self.ser.reset_input_buffer()
        self._buf = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        # Prime the shell
        self.send('')
        self.get_response(0.5)

    def _read_loop(self):
        while not self._stop.is_set():
            try:
                chunk = self.ser.read(4096)
            except Exception:
                break
            if chunk:
                with self._lock:
                    self._buf.append(chunk)

    def send(self, cmd):
        self.ser.write((cmd + '\r\n').encode())

    def get_response(self, wait=1.0):
        time.sleep(wait)
        with self._lock:
            out = b''.join(self._buf)
            self._buf.clear()
        return out.decode('utf-8', errors='replace')

    def cmd(self, command, wait=1.0):
        with self._lock:
            self._buf.clear()
        self.send(command)
        return self.get_response(wait)

    def read_file(self, path):
        """Read a text file from the SD card. Returns decoded string or None.

        NOTE: filesize uses f_stat (no open handle), so it's safe to call
        before fopen. fread output is HEX ENCODED; decode with unhexlify.
        """
        # Get size first (must not be called while a file is open)
        self.cmd('fclose')
        resp = self.cmd(f'filesize {path}')
        size = None
        for line in resp.split('\r\n'):
            line = line.strip()
            try:
                size = int(line)
                break
            except ValueError:
                continue
        if size is None or size == 0:
            return None

        # Open and read. CRITICAL: fopen leaves seek at END; must fseek 0.
        self.cmd(f'fopen {path}')
        self.cmd('fseek 0')
        resp = self.cmd(f'fread {size}', wait=max(2.0, size / 500.0))
        self.cmd('fclose')

        # Parse hex output
        hex_chars = ''
        for line in resp.split('\r\n'):
            line = line.strip()
            if not line:
                continue
            if line.startswith('fread') or line == 'ok' or line == 'ch>':
                continue
            if line.startswith('ch> '):
                line = line[4:].strip()
                if not line:
                    continue
            cleaned = line.replace(' ', '')
            if cleaned and all(c in '0123456789ABCDEFabcdef' for c in cleaned):
                hex_chars += cleaned

        if not hex_chars:
            return None
        try:
            return binascii.unhexlify(hex_chars).decode('utf-8', errors='replace')
        except (binascii.Error, ValueError):
            return None

    def write_file(self, path, content):
        """Write text content to a file on the SD card via fwb (write binary).

        fopen opens with read_only=false, create=true, seek at END (append).
        So: unlink old file, fopen fresh (creates empty), fwb raw bytes, fclose.
        """
        data = content.encode('utf-8')
        self.cmd(f'unlink {path}')   # delete old, ignore error
        resp = self.cmd(f'fopen {path}')
        if 'error' in resp.lower() or 'failed' in resp.lower():
            # Some firmware builds echo an error string; try continuing anyway
            pass
        # Write binary: fwb <size> then exactly <size> raw bytes.
        # The background reader thread keeps draining so fwb never deadlocks.
        self.send(f'fwb {len(data)}')
        time.sleep(0.1)
        self.ser.write(data)
        self.get_response(max(2.0, len(data) / 500.0))  # wait for write to complete
        self.cmd('fclose')

    def close(self):
        self._stop.set()
        try:
            self._reader.join(timeout=1.0)
        except Exception:
            pass
        try:
            self.ser.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# FreqMan parsing
# ---------------------------------------------------------------------------


def parse_freqman(content):
    """Parse FreqMan file content into a list of entry dicts."""
    entries = []
    if not content:
        return entries
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        entry = {}
        for field in line.split(','):
            field = field.strip()
            if '=' in field:
                key, val = field.split('=', 1)
                entry[key.strip()] = val.strip()
        if entry:
            entry['_type'] = 'range' if 'a' in entry and 'b' in entry else 'single'
            entries.append(entry)
    return entries


def entry_freqs(entry):
    """Return (lo, hi) Hz covered by an entry."""
    if entry['_type'] == 'range':
        try:
            lo = int(entry['a'])
            hi = int(entry['b'])
        except (KeyError, ValueError):
            return None
        return (min(lo, hi), max(lo, hi))
    try:
        f = int(entry['f'])
    except (KeyError, ValueError):
        return None
    return (f, f)


def fmt_mhz(hz):
    return f"{hz / 1e6:.3f}"


# ---------------------------------------------------------------------------
# Config generation
# ---------------------------------------------------------------------------


def gen_recon_ini(name, squelch=-20):
    return (
        f"input_file={name}\r\n"
        f"output_file={name}_RESULTS\r\n"
        "lock_duration=100\r\n"
        "lock_nb_match=3\r\n"
        f"squelch_level={squelch}\r\n"
        "match_mode=0\r\n"
        "match_wait=1000\r\n"
        "range_min=0\r\n"
        "range_max=7250000000\r\n"
    )


def gen_rx_tx_ini(st):
    return (
        "tx_frequency=1300000\r\n"
        "tx_amp=0\r\n"
        "tx_gain=0\r\n"
        "channel_bandwidth=1\r\n"
        "rx_frequency=1300000\r\n"
        f"lna={st['lna']}\r\n"
        f"vga={st['vga']}\r\n"
        f"rx_amp={st['amp']}\r\n"
        "modulation=0\r\n"
        "am_config_index=0\r\n"
        "nbfm_config_index=0\r\n"
        "wfm_config_index=0\r\n"
        "wfmam_config_index=0\r\n"
        f"squelch={st['squelch']}\r\n"
        "baseband_bandwidth=1750000\r\n"
        "sampling_rate=3072000\r\n"
        "step=25000\r\n"
        "volume=99\r\n"
    )


# ---------------------------------------------------------------------------
# Menu / main flow
# ---------------------------------------------------------------------------

BANNER = r"""
  _  _     _         _____               _____
 | || |___| |__ __ _|_   _|__  _ __  ___| |___
 | __ / -_) '_ \ _` | |/ / _ \| '  \/ -_) / -_)
 |_||_\___|_.__/\__,_|_|_\___/|_|_|_\___|_\___|
        HackRF Recon — Frequency Scanner
"""


def find_freqman_files(dev):
    """List .TXT files in /FREQMAN; returns list of file names."""
    resp = dev.cmd(f'ls {FREQMAN_DIR}')
    files = []
    for line in resp.split('\r\n'):
        line = line.strip()
        if not line or line == 'ch>' or line.startswith('ch> '):
            continue
        # ls output lines are file names; filter to .TXT
        name = line.split('ch> ')[-1].strip()
        if name.upper().endswith('.TXT'):
            files.append(name if name.upper().endswith('.TXT') else name + '.TXT')
    return sorted(set(files))


def load_profiles(dev):
    """Scan the device and return list of profiles.
    
    For built-in presets, uses embedded metadata (no device read needed).
    For unknown files, just records the name — content is read on demand.
    This avoids reading 50+ files over serial at startup.
    """
    profiles = []
    seen = set()
    for fname in find_freqman_files(dev):
        name = fname[:-len('.TXT')] if fname.upper().endswith('.TXT') else fname
        name_upper = name.upper()
        if name_upper in seen:
            continue
        seen.add(name_upper)

        if name in PRESETS:
            # Use embedded preset content — no device read
            entries = parse_freqman(PRESETS[name])
            profiles.append({'name': name, 'file': fname, 'entries': entries, 'on_device': True})
        else:
            # Unknown file — just record name, read later if selected
            profiles.append({'name': name, 'file': fname, 'entries': None, 'on_device': True})
    return profiles


def ensure_profile_loaded(dev, profile):
    """Read and parse a profile's FreqMan file if not already loaded."""
    if profile['entries'] is not None:
        return True
    path = f'{FREQMAN_DIR}/{profile["file"]}'
    content = dev.read_file(path)
    entries = parse_freqman(content)
    if entries:
        profile['entries'] = entries
        return True
    return False


def profile_summary(profile):
    """Return (count, lo, hi, sample description) for a profile."""
    if profile['entries'] is None:
        # Not yet loaded from device
        return '?', '-', '(not scanned yet)'
    lo = hi = None
    for e in profile['entries']:
        r = entry_freqs(e)
        if not r:
            continue
        if lo is None or r[0] < lo:
            lo = r[0]
        if hi is None or r[1] > hi:
            hi = r[1]
    # Sample description: first entry with a d= field
    desc = ''
    for e in profile['entries']:
        if 'd' in e:
            desc = e['d'].split('|')[0]
            break
    if lo is None:
        rng = '-'
    elif lo == hi:
        rng = f'{fmt_mhz(lo)} MHz'
    else:
        rng = f'{fmt_mhz(lo)}-{fmt_mhz(hi)} MHz'
    return len(profile['entries']), rng, desc


def print_menu(profiles, port):
    print('\n  HackRF Recon — Frequency Scanner')
    print('  ================================')
    print(f'  Connected: PortaPack Mayhem on {port}\n')
    print('  Available scan profiles:\n')
    hdr = f"  {'#':>3}  {'Name':<17} {'Entries':>7}  {'Range':<22}  Description"
    print(hdr)
    print('  ' + '-' * (len(hdr) - 2))
    for i, p in enumerate(profiles, 1):
        count, rng, desc = profile_summary(p)
        print(f"  {i:>3}  {p['name']:<17} {count:>7}  {rng:<22}  {desc}")
    print()
    print('  [U] Upload missing presets to device')
    print('  [Q] Quit')
    print()


def upload_presets(dev, missing):
    """Upload missing built-in presets to the device as FREQMAN .TXT files."""
    uploaded = 0
    for name in missing:
        content = PRESETS[name]
        # FreqMan files use \n line endings on the device
        data = content.encode('utf-8')
        path = f'{FREQMAN_DIR}/{name}.TXT'
        dev.cmd(f'unlink {path}')  # ignore error
        dev.cmd(f'fopen {path}')
        dev.send(f'fwb {len(data)}')
        time.sleep(0.1)
        dev.ser.write(data)
        dev.get_response(max(2.0, len(data) / 500.0))
        dev.cmd('fclose')
        uploaded += 1
        print(f'    uploaded {name}.TXT ({len(data)} bytes)')
    return uploaded


def start_recon(dev, profile):
    """Write configs and launch the Recon app for the chosen profile."""
    name = profile['name']
    st = PRESET_SETTINGS.get(name, DEFAULT_SETTINGS)
    print(f'\n  Writing /SETTINGS/recon.ini ...')
    dev.write_file(f'{SETTINGS_DIR}/recon.ini',
                   gen_recon_ini(name, st['squelch']))
    print(f'  Writing /SETTINGS/rx_tx_recon.ini ...')
    dev.write_file(f'{SETTINGS_DIR}/rx_tx_recon.ini', gen_rx_tx_ini(st))
    print('  Starting Recon app ...')
    dev.cmd('appstart recon', wait=1.0)
    print(f"\n  Recon started on profile {name}. Disconnect USB or Ctrl+C to exit.")


def monitor(dev, name):
    """Sit in a loop printing status via sysinfo every 30 seconds."""
    print(f'  Monitoring (profile: {name}) — press Ctrl+C to stop.\n')
    n = 0
    while True:
        try:
            resp = dev.cmd('sysinfo', wait=2.0)
            n += 1
            lines = [l.strip() for l in resp.split('\r\n')
                     if l.strip() and l.strip() != 'ch>' and not l.strip().startswith('ch> ')]
            stamp = time.strftime('%H:%M:%S')
            body = ' | '.join(lines) if lines else 'no data'
            print(f'  [{stamp}] #{n} {body}')
        except (KeyboardInterrupt, serial.SerialException):
            raise
        time.sleep(28)


def main():
    print(BANNER)

    port = detect_port()
    if port is None:
        print("ERROR: No HackRF PortaPack found on USB (VID 1d50, PID 6018).")
        print("       Check the connection and that the device is in USB mode.")
        return 1

    try:
        dev = HackRFSerial(port, BAUD)
    except serial.SerialException as e:
        print(f"ERROR: Could not open {port}: {e}")
        print("       Try: sudo usermod -aG dialout $USER  (then re-login)")
        return 1

    exit_code = 0
    try:
        while True:
            print('\n  Scanning FREQMAN files on device ...')
            profiles = load_profiles(dev)
            if not profiles:
                print('  No FREQMAN files found on device.')
            names_on_device = {p['name'] for p in profiles}
            missing = [n for n in PRESETS if n not in names_on_device]

            print_menu(profiles, port)

            if missing:
                print(f'  ({len(missing)} built-in preset(s) not on device: '
                      f'{", ".join(missing)})')
            choice = input('  Select profile number to start scan: ').strip()

            if choice.lower() == 'q':
                break
            if choice.lower() == 'u':
                if not missing:
                    print('  All built-in presets already present on device.')
                    input('  Press Enter to return to menu...')
                    continue
                print(f'\n  Uploading {len(missing)} missing preset(s):')
                n = upload_presets(dev, missing)
                print(f'\n  Uploaded {n} preset(s). Re-scanning ...')
                continue

            try:
                idx = int(choice)
            except ValueError:
                print('  Invalid choice.')
                continue
            if not (1 <= idx <= len(profiles)):
                print('  Invalid profile number.')
                continue

            profile = profiles[idx - 1]
            start_recon(dev, profile)
            monitor(dev, profile['name'])
            break  # monitor exited via Ctrl+C

    except KeyboardInterrupt:
        print('\n  Interrupted by user.')
    except serial.SerialException as e:
        print(f'\n  Serial error: {e}')
        exit_code = 1
    finally:
        dev.close()
        print('  Serial port closed. Bye.')
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
