"""
ADMS push-protocol parsing — PURE functions (no I/O).

ZKTeco / eSSL face terminals configured in "ADMS" server mode POST their logs
to `/iclock/cdata?SN=<serial>&table=ATTLOG` as plain text, one punch per line:

    <PIN>\t<YYYY-MM-DD HH:MM:SS>\t<status>\t<verify>\t<workcode>\t...

The protocol is vendor-proprietary (no official public spec). This parser is
deliberately forgiving about trailing columns and whitespace, and strict about
the two fields that matter: the PIN and the timestamp. The machine reports
STORE WALL-CLOCK time (IST); it is converted to an absolute instant here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from app.services.employee.workforce_rules import STORE_TZ

_PIN_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")
_SPACE_SPLIT_RE = re.compile(r"^(\S+)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*(.*)$")

#: ZKTeco attendance-state codes → direction. Anything else is UNKNOWN.
_DIRECTION = {"0": "IN", "1": "OUT", "4": "IN", "5": "OUT"}

#: ZKTeco verify-mode codes (best effort; vendor firmware varies).
_VERIFY = {
    "0": "PIN",
    "1": "FINGERPRINT",
    "2": "CARD",
    "3": "CARD",
    "4": "CARD",
    "15": "FACE",
}

MAX_LINES_PER_REQUEST = 2000


@dataclass(frozen=True)
class ParsedPunch:
    pin: str
    punched_at: datetime  # timezone-aware (STORE_TZ)
    direction: str
    verify_type: Optional[str]
    raw_line: str


def parse_attlog(body: str) -> Tuple[List[ParsedPunch], int]:
    """Parse an ATTLOG request body → (punches, rejected_line_count).

    Blank lines are ignored (not counted as rejected). A line is rejected when
    the PIN or timestamp is missing/invalid. At most MAX_LINES_PER_REQUEST
    lines are read; extra lines count as rejected so the caller can see them.
    """
    punches: List[ParsedPunch] = []
    rejected = 0
    lines = [ln for ln in (body or "").splitlines() if ln.strip()]
    if len(lines) > MAX_LINES_PER_REQUEST:
        rejected += len(lines) - MAX_LINES_PER_REQUEST
        lines = lines[:MAX_LINES_PER_REQUEST]

    for line in lines:
        parsed = _parse_line(line)
        if parsed is None:
            rejected += 1
        else:
            punches.append(parsed)
    return punches, rejected


def _parse_line(line: str) -> Optional[ParsedPunch]:
    raw = line.strip()
    if "\t" in raw:
        parts = [p.strip() for p in raw.split("\t")]
        if len(parts) < 2:
            return None
        pin, stamp = parts[0], parts[1]
        status = parts[2] if len(parts) > 2 else ""
        verify = parts[3] if len(parts) > 3 else ""
    else:
        match = _SPACE_SPLIT_RE.match(raw)
        if not match:
            return None
        pin, stamp = match.group(1), re.sub(r"\s+", " ", match.group(2))
        rest = match.group(3).split()
        status = rest[0] if rest else ""
        verify = rest[1] if len(rest) > 1 else ""

    if not _PIN_RE.match(pin):
        return None
    try:
        local = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return ParsedPunch(
        pin=pin,
        punched_at=local.replace(tzinfo=STORE_TZ),
        direction=_DIRECTION.get(status, "UNKNOWN"),
        verify_type=_VERIFY.get(verify, "OTHER" if verify else None),
        raw_line=raw[:500],
    )
