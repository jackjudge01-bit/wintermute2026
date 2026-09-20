#!/usr/bin/env python3
"""HackRF PortaPack Mayhem serial shell interface."""

import serial
import time
import os

class HackRFSerial:
    def __init__(self, port='/dev/ttyACM1', baud=115200, timeout=2):
        self.ser = serial.Serial(port, baud, timeout=timeout)
        time.sleep(0.5)
        self.ser.reset_input_buffer()
        # eat any prompt
        self.ser.write(b'\r\n')
        time.sleep(0.3)
        self.ser.read(4096)
    
    def send_cmd(self, cmd, timeout=3):
        """Send a command and return response lines (excluding prompt and echo)."""
        self.ser.reset_input_buffer()
        self.ser.write((cmd + '\r\n').encode())
        
        buf = b''
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = self.ser.read(4096)
            if chunk:
                buf += chunk
                deadline = time.time() + 0.5
            elif buf:
                break
        
        text = buf.decode('utf-8', errors='replace')
        lines = []
        for line in text.split('\r\n'):
            line = line.strip()
            if not line:
                continue
            if line == cmd:
                continue
            if line == 'ch>':
                continue
            if line.startswith('ch> '):
                line = line[4:]
                if not line:
                    continue
            lines.append(line)
        return lines
    
    def ls(self, path='/'):
        return self.send_cmd(f'ls {path}')
    
    def filesize(self, path):
        resp = self.send_cmd(f'filesize {path}')
        for line in resp:
            try:
                return int(line)
            except ValueError:
                continue
        return None
    
    def read_file(self, path):
        """Read a text file via fopen/fread/fclose."""
        size = self.filesize(path)
        if size is None or size == 0:
            return None
        
        resp = self.send_cmd(f'fopen {path} r')
        
        content_lines = []
        remaining = size
        chunk = 512
        while remaining > 0:
            read_size = min(chunk, remaining)
            resp = self.send_cmd(f'fread {read_size}', timeout=5)
            content_lines.extend(resp)
            remaining -= read_size
        
        self.send_cmd('fclose')
        return '\n'.join(content_lines)
    
    def close(self):
        self.ser.close()


def main():
    outdir = '/path/to/projects/hackrf-elk/device_dump'
    os.makedirs(outdir, exist_ok=True)
    
    print("Connecting to HackRF on /dev/ttyACM1...")
    h = HackRFSerial()
    
    # App list
    print("\n=== APP LIST ===")
    apps = h.send_cmd('applist', timeout=5)
    with open(f'{outdir}/applist.txt', 'w') as f:
        for a in apps:
            print(a)
            f.write(a + '\n')
    
    # SD root
    print("\n=== SD ROOT ===")
    root = h.ls('/')
    with open(f'{outdir}/sd_root_ls.txt', 'w') as f:
        for entry in root:
            print(entry)
            f.write(entry + '\n')
    
    # FREQMAN dir
    print("\n=== FREQMAN ===")
    fm = h.ls('/FREQMAN')
    with open(f'{outdir}/freqman_ls.txt', 'w') as f:
        for entry in fm:
            print(entry)
            f.write(entry + '\n')
    
    # SETTINGS dir
    print("\n=== SETTINGS ===")
    settings = h.ls('/SETTINGS')
    with open(f'{outdir}/settings_ls.txt', 'w') as f:
        for entry in settings:
            print(entry)
            f.write(entry + '\n')
    
    # CAPTURES dir
    print("\n=== CAPTURES ===")
    caps = h.ls('/CAPTURES')
    with open(f'{outdir}/captures_ls.txt', 'w') as f:
        for entry in caps:
            print(entry)
            f.write(entry + '\n')
    
    # Pull specific files
    files_to_pull = [
        '/SETTINGS/recon.ini',
        '/SETTINGS/recon.cfg', 
        '/FREQMAN/RECON.TXT',
        '/FREQMAN/RECON_RESULTS.TXT',
    ]
    
    # Also pull any .TXT files we find in FREQMAN
    for entry in fm:
        if '.TXT' in entry.upper() or '.txt' in entry:
            name = entry.split()[0] if ' ' in entry else entry
            fpath = f'/FREQMAN/{name}'
            if fpath not in files_to_pull:
                files_to_pull.append(fpath)
    
    # Pull any .ini/.cfg in SETTINGS that mention recon
    for entry in settings:
        name = entry.split()[0] if ' ' in entry else entry
        if 'recon' in name.lower():
            fpath = f'/SETTINGS/{name}'
            if fpath not in files_to_pull:
                files_to_pull.append(fpath)
    
    for fpath in files_to_pull:
        print(f"\n=== {fpath} ===")
        size = h.filesize(fpath)
        print(f"  size: {size}")
        if size and size > 0:
            content = h.read_file(fpath)
            if content:
                print(content[:3000])
                safe_name = fpath.replace('/', '_').lstrip('_')
                with open(f'{outdir}/{safe_name}', 'w') as f:
                    f.write(content + '\n')
        else:
            print("  (empty or not found)")
    
    h.close()
    print(f"\nDone. Files saved to {outdir}/")


if __name__ == '__main__':
    main()
