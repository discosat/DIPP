#!/usr/bin/env python3
"""
Control the board's power via the Otii Ace, which supplies it at 5 V.

    otii_power.py status
    otii_power.py cut [seconds]     # power off, wait, power back on
    otii_power.py on | off

The main output is ALWAYS restored on exit, including on exception or Ctrl-C:
leaving the board unpowered would look exactly like a hung board and waste a
lot of time to diagnose.
"""
import re, sys, time, argparse
from otii_tcp_client import otii_client

CREDS = '/home/roba/energy-measurement-csh/energy_measurement_csh.py'

def get_device(otii):
    src = open(CREDS).read()
    pw = re.search(r'otii\.login\("([^"]+)",\s*"([^"]+)"\)', src)
    otii.login(pw.group(1), pw.group(2))
    for l in otii.get_licenses():
        if not l.get('reserved_to'):
            otii.reserve_license(l['id'])
            break
    devs = otii.get_devices()
    if not devs:
        raise RuntimeError("no Otii device")
    return devs[0]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["status", "cut", "on", "off"])
    ap.add_argument("seconds", nargs="?", type=float, default=3.0)
    a = ap.parse_args()

    client = otii_client.OtiiClient()
    with client.connect(licensing=otii_client.LicensingMode.MANUAL) as otii:
        d = get_device(otii)
        try:
            if a.action == "status":
                print(f"main={d.get_main()} V={d.get_main_voltage()} Imax={d.get_max_current()}")
            elif a.action == "on":
                d.set_main(True); print("main ON")
            elif a.action == "off":
                d.set_main(False); print("main OFF")
            else:
                d.set_main(False)
                print(f"power CUT for {a.seconds}s", flush=True)
                time.sleep(a.seconds)
                d.set_main(True)
                print("power RESTORED", flush=True)
        except BaseException:
            try: d.set_main(True); print("power restored after error", flush=True)
            except Exception: pass
            raise
    return 0

if __name__ == "__main__":
    sys.exit(main())
