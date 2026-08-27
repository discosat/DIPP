#!/usr/bin/env python3
"""Fetch a file from the board over the console as a hex dump, verify by md5."""
import os, sys, time, termios, select, hashlib, re

DEV="/dev/ttyUSB0"
def open_port():
    fd=os.open(DEV, os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
    a=termios.tcgetattr(fd); a[0]=a[1]=a[3]=0
    a[2]=termios.CS8|termios.CREAD|termios.CLOCAL
    a[4]=a[5]=termios.B115200
    a[6]=list(a[6]); a[6][termios.VMIN]=0; a[6][termios.VTIME]=0
    termios.tcsetattr(fd, termios.TCSANOW, a); return fd

def drain_until(fd, marker, timeout):
    out=b""; end=time.time()+timeout
    while time.time()<end:
        r,_,_=select.select([fd],[],[],0.2)
        if r:
            try: out+=os.read(fd,8192)
            except BlockingIOError: pass
            if out.count(marker) >= 2: break   # 1st hit is the console echo
    return out.decode("utf-8","replace")

remote, local, want_md5 = sys.argv[1], sys.argv[2], sys.argv[3]
fd=open_port()
drain_until(fd,b"@@NOTHING@@",1.0)
os.write(fd, f"echo XSTARTX; od -v -An -tx1 {remote}; echo XENDX\r".encode())
txt=drain_until(fd,b"XENDX",240)

m=re.search(r"XSTARTX\r?\n(.*?)\r?\nXENDX", txt, re.S)
if not m: sys.exit("markers not found; got %d bytes" % len(txt))
body=m.group(1)
hexs=re.findall(r"\b[0-9a-f]{2}\b", body)
data=bytes(int(h,16) for h in hexs)
got=hashlib.md5(data).hexdigest()
print(f"{remote}: {len(data)} bytes, md5 {got}", end=" ")
if got==want_md5:
    open(local,"wb").write(data); print("-> MATCH, saved")
else:
    open(local+".bad","wb").write(data); print(f"-> MISMATCH (want {want_md5})")
    sys.exit(1)
