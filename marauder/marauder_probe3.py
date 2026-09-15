import serial, time

def probe(cmds, header, wait=2.0):
    try:
        s = serial.Serial("/dev/ttyACM0", 115200, timeout=1)
    except Exception as e:
        print("OPEN FAILED:", e); return
    time.sleep(0.4); s.reset_input_buffer()
    print(f"\n########## {header} ##########")
    for c in cmds:
        s.reset_input_buffer()
        s.write(c.encode() + b"\r\n")
        time.sleep(wait)
        out = s.read(16384).decode(errors="replace")
        print(f"--- {c!r} ---")
        print(out[:900] if out.strip() else "(no output)")
    s.close()

# does the machine protocol work on list commands?
probe(["list -a --machine 1"], "list -a --machine 1")
probe(["list -b --machine 1"], "list -b --machine 1")
probe(["list -s --machine 1"], "list -s --machine 1")

# short real scan then dump, to see plain-text format with data
print("\n########## scanall then list -a (short scan) ##########")
s = serial.Serial("/dev/ttyACM0", 115200, timeout=1)
time.sleep(0.4); s.reset_input_buffer()
s.write(b"scanall\r\n"); time.sleep(18)
s.write(b"stopscan\r\n"); time.sleep(1.5)
s.reset_input_buffer()
s.write(b"list -a\r\n"); time.sleep(2.5)
print(s.read(16384).decode(errors="replace")[:1200])
print("\n--- list -a --machine 1 (with data) ---")
s.write(b"list -a --machine 1\r\n"); time.sleep(2.0)
print(s.read(16384).decode(errors="replace")[:1200])
s.close()
