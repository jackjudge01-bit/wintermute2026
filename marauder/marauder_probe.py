import serial, time, sys

port = "/dev/ttyACM0"
try:
    s = serial.Serial(port, 115200, timeout=1)
except Exception as e:
    print("OPEN FAILED:", e); sys.exit(1)

time.sleep(0.5)
s.reset_input_buffer()
s.write(b"\r\n")
time.sleep(1.2)
out = s.read(4096)
print("=== after newline ===")
print(out.decode(errors="replace")[:800])

s.write(b"help\r\n")
time.sleep(2.0)
out2 = s.read(8192)
print("=== after 'help' ===")
print(out2.decode(errors="replace")[:2000])

s.close()
