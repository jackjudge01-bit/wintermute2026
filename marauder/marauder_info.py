import serial, time

s = serial.Serial("/dev/ttyACM0", 115200, timeout=1)
time.sleep(0.4); s.reset_input_buffer()

def cmd(c, wait):
    s.reset_input_buffer()
    s.write(c.encode() + b"\r\n")
    time.sleep(wait)
    return s.read(65536).decode(errors="replace")

cmd("stopscan", 1.0)
cmd("scanall", 1.0)
time.sleep(14)
cmd("stopscan", 1.0)

for idx in (0, 1, 2):
    out = cmd(f"info -a {idx}", 2.0)
    print(f"===== info -a {idx} =====")
    print(out[:700])
    print()
s.close()
