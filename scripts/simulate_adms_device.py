"""Simulate a ZKTeco/eSSL terminal in ADMS push mode (no hardware needed).

Usage (PowerShell, from backend\pratikshya-backend):

    python scripts/simulate_adms_device.py --sn TEST0001 --pin 7 --in 09:31 --out 18:18
    python scripts/simulate_adms_device.py --sn TEST0001 --pin 7 --date 2026-10-07 --in 09:31 --out 13:00 --in 14:00 --out 18:18

Prerequisites: the API is running, the serial number is registered
(POST /api/v1/admin/attendance/devices) and the employee has a machine PIN
(PUT /api/v1/admin/attendance/device-pin/{employee}). Unmapped PINs are still
stored and show up under "unmapped punches".

--in / --out may be repeated and are paired in the order given. Pass
--direction-flags to send the machine's own IN(0)/OUT(1) codes; the server
ignores them for pairing either way.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date


def _request(url: str, data: bytes | None = None) -> str:
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    if data is not None:
        req.add_header("Content-Type", "text/plain")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return f"{resp.status} {resp.read().decode('utf-8', 'replace').strip()}"
    except urllib.error.HTTPError as exc:
        return f"{exc.code} {exc.read().decode('utf-8', 'replace').strip()}"
    except urllib.error.URLError as exc:
        return f"connection failed: {exc.reason}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://localhost:8000", help="API root (no /api/v1)")
    parser.add_argument("--sn", required=True, help="Registered device serial number")
    parser.add_argument("--pin", required=True, help="Employee machine PIN")
    parser.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD (IST)")
    parser.add_argument("--in", dest="ins", action="append", default=[], metavar="HH:MM")
    parser.add_argument("--out", dest="outs", action="append", default=[], metavar="HH:MM")
    parser.add_argument("--verify", default="15", help="Verify code (15 = face)")
    parser.add_argument("--direction-flags", action="store_true", help="Send 0/1 IN/OUT flags")
    args = parser.parse_args()

    stamps: list[tuple[str, str]] = []  # (HH:MM, flag)
    for value in args.ins:
        stamps.append((value, "0"))
    for value in args.outs:
        stamps.append((value, "1"))
    if not stamps:
        parser.error("give at least one --in/--out time")
    stamps.sort(key=lambda s: s[0])

    lines = []
    for hhmm, flag in stamps:
        status = flag if args.direction_flags else "0"
        lines.append(f"{args.pin}\t{args.date} {hhmm}:00\t{status}\t{args.verify}\t0\t0")
    body = ("\n".join(lines) + "\n").encode()

    sn = urllib.parse.quote(args.sn)
    base = args.base.rstrip("/")
    print("handshake:", _request(f"{base}/iclock/cdata?SN={sn}&options=all"))
    print("push     :", _request(f"{base}/iclock/cdata?SN={sn}&table=ATTLOG&Stamp=9999", body))
    print("poll     :", _request(f"{base}/iclock/getrequest?SN={sn}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
