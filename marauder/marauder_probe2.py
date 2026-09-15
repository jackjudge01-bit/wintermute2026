import serial, time

def probe(cmds, header):
    try:
        s = serial.Serial("/dev/ttyACM0", 115200, timeout=1)
    except Exception as e:
        print("OPEN FAILED:", e); return
    time.sleep(0.4)
    s.reset_input_buffer()
    print(f"\n########## {header} ##########")
    for c in cmds:
        print(f"\n--- send: {c!r} ---")
        s.reset_input_buffer()
        s.write(c.encode() + b"\r\n")
        time.sleep(2.0)
        out = s.read(8192)
        txt = out.decode(errors="replace")
        print(txt[:1500] if txt.strip() else "(no output)")
    s.close()

# 1. protocolinfo plain
probe(["protocolinfo"], "protocolinfo (plain)")

# 2. protocolinfo --machine
probe(["protocolinfo --machine 1"], "protocolinfo --machine 1")

# 3. light GPS check only
probe(["gps"], "gps (light check)")

# 4. settings -r (what settings exist - one line tells us a lot)
probe(["settings -r"], "settings (read) - light")
