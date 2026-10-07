"""
Workforce business rules — PURE functions (no I/O), server authority.

Mirrors the semantics the frontend workforce UI already applies
(`frontend/src/services/workforce/attendanceService.js` evaluateTiming /
statusAfterPunch, `leaveService.js` overlap + transitions,
`config/attendanceConfig.js` defaults). Kept deliberately small: the app has
no complex HR policy beyond these rules, so none is invented here.

WHETHER a mutation is valid is decided here; WHO may attempt it is decided by
RBAC (`app/core/rbac.py`). The two stay separate by design.

The store runs on a single wall clock (IST, UTC+05:30): punch timestamps are
stored/compared as store-local wall time so a 09:35 punch is "on time" at the
09:30 opening regardless of where the API server sits. Documented, not
configurable — inventing a timezone table here would be new infrastructure.
"""

from __future__ import annotations

from datetime import date as date_t, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: Single-store wall clock (see module docstring).
STORE_TZ = timezone(timedelta(hours=5, minutes=30))

#: Defaults mirror `SETTINGS_DEFAULTS["attendance"]` (app/core/settings_catalog)
#: and `frontend/src/config/attendanceConfig.js` ATTENDANCE_DEFAULTS.
DEFAULT_SETTINGS: Dict[str, Any] = {
    "workingStartTime": "09:30",
    "workingEndTime": "18:30",
    "lateThresholdMinutes": 10,
    "minimumHalfDayMinutes": 240,
    "fullDayMinutes": 540,
    "weekOffWeekdays": [0],  # JS getUTCDay numbering; 0 = Sunday
    "holidays": [],
    # While True, employees may also punch from the web page. Switch it off
    # once the machines are live so the machine is the single source.
    "webPunchEnabled": True,
}

#: Two taps closer than this on the same machine count as ONE punch.
PUNCH_DEBOUNCE_SECONDS = 120

ATTENDANCE_STATUSES = {
    "PRESENT", "LATE", "ABSENT", "HALF_DAY", "LEAVE", "HOLIDAY",
    "WEEK_OFF", "ON_DUTY", "PENDING_CORRECTION", "NOT_CHECKED_IN",
}

LEAVE_TYPES = {"CASUAL", "SICK", "EARNED", "EMERGENCY", "OTHER"}
LEAVE_STATUSES = {"PENDING", "APPROVED", "REJECTED", "CANCELLED"}

#: Only these transitions are legal; every other move is refused (409).
LEAVE_TRANSITIONS = {
    "PENDING": {"APPROVED", "REJECTED", "CANCELLED"},
    "APPROVED": {"CANCELLED"},   # withdrawal of granted leave before it starts
    "REJECTED": set(),
    "CANCELLED": set(),
}

#: A review row's rating scale is fixed by the existing model (1–5).
RATING_MIN, RATING_MAX = 1, 5
REVIEW_PERIODS = {"MONTHLY", "QUARTERLY", "ANNUAL"}


def store_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(STORE_TZ).replace(tzinfo=None)


def to_store_wallclock(raw: Optional[str]) -> datetime:
    """Parse an incoming ISO timestamp (naive = store wall clock already)."""
    if not raw:
        raise ValueError("timestamp required")
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp '{raw}'.") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(STORE_TZ).replace(tzinfo=None)
    return parsed


def day_of(value: datetime) -> date_t:
    return value.date()


def _clock(raw: Optional[str]) -> Optional[time]:
    if not raw:
        return None
    try:
        hours, minutes = str(raw).strip().split(":")[:2]
        return time(int(hours), int(minutes))
    except ValueError:
        return None


def resolve_settings(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize the stored `attendance` settings section (alias-tolerant:
    the Admin settings surface has been written with both `startTime` and
    `workingStartTime` key shapes; numbers clamp like the frontend)."""
    source = dict(raw or {})
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in source.items() if v is not None})
    out = dict(DEFAULT_SETTINGS)
    out["workingStartTime"] = str(
        source.get("workingStartTime") or source.get("startTime") or DEFAULT_SETTINGS["workingStartTime"]
    )
    out["workingEndTime"] = str(
        source.get("workingEndTime") or source.get("endTime") or DEFAULT_SETTINGS["workingEndTime"]
    )
    out["lateThresholdMinutes"] = max(0, int(source.get("lateThresholdMinutes", DEFAULT_SETTINGS["lateThresholdMinutes"]) or 0))
    out["minimumHalfDayMinutes"] = max(0, int(source.get("minimumHalfDayMinutes", DEFAULT_SETTINGS["minimumHalfDayMinutes"]) or 0))
    out["fullDayMinutes"] = max(1, int(source.get("fullDayMinutes", DEFAULT_SETTINGS["fullDayMinutes"]) or 1))
    weekdays = source.get("weekOffWeekdays")
    if isinstance(weekdays, (list, tuple)):
        cleaned = [int(d) for d in weekdays if isinstance(d, (int, float)) and 0 <= int(d) <= 6]
        out["weekOffWeekdays"] = cleaned or DEFAULT_SETTINGS["weekOffWeekdays"]
    holidays = source.get("holidays")
    out["holidays"] = [
        {"date": str(item.get("date")), "name": str(item.get("name") or "Holiday")}
        for item in (holidays or [])
        if isinstance(item, dict) and item.get("date")
    ]
    out["webPunchEnabled"] = bool(source.get("webPunchEnabled", DEFAULT_SETTINGS["webPunchEnabled"]))
    return out


def holiday_dates(settings: Dict[str, Any]) -> List[str]:
    return [h["date"] for h in settings.get("holidays", []) if h.get("date")]


def calendar_status(day: date_t, settings: Dict[str, Any]) -> Optional[str]:
    """HOLIDAY / WEEK_OFF marker for a date, else None (a working day)."""
    if day.isoformat() in set(holiday_dates(settings)):
        return "HOLIDAY"
    # Python weekday(): Mon=0..Sun=6; the config uses JS getUTCDay (Sun=0..).
    if (day.weekday() + 1) % 7 in set(settings.get("weekOffWeekdays") or []):
        return "WEEK_OFF"
    return None


def evaluate_timing(
    day: date_t,
    check_in: Optional[datetime],
    check_out: Optional[datetime],
    settings: Dict[str, Any],
) -> Dict[str, int]:
    start = _clock(settings["workingStartTime"])
    end = _clock(settings["workingEndTime"])
    start_dt = datetime.combine(day, start) if start else None
    end_dt = datetime.combine(day, end) if end else None
    threshold = (
        start_dt + timedelta(minutes=settings["lateThresholdMinutes"]) if start_dt else None
    )

    late_minutes = 0
    if check_in and start_dt and threshold and check_in > threshold:
        late_minutes = round((check_in - start_dt).total_seconds() / 60)

    work_minutes = 0
    early_leave_minutes = 0
    if check_in and check_out:
        work_minutes = max(0, round((check_out - check_in).total_seconds() / 60))
        if end_dt and check_out < end_dt:
            early_leave_minutes = round((end_dt - check_out).total_seconds() / 60)

    return {
        "lateMinutes": max(0, late_minutes),
        "workMinutes": work_minutes,
        "earlyLeaveMinutes": max(0, early_leave_minutes),
    }


def status_after_punch(
    day: date_t,
    check_in: Optional[datetime],
    check_out: Optional[datetime],
    settings: Dict[str, Any],
    *,
    on_leave: bool = False,
    work_minutes: Optional[int] = None,
) -> str:
    """Exactly the frontend statusAfterPunch cascade (leave wins, then the
    holiday/week-off frame, then work-time math).

    `work_minutes` overrides the check_in→check_out span (machine days subtract
    the time between a lunch OUT and the following IN)."""
    if on_leave:
        return "LEAVE"
    calendar = calendar_status(day, settings)
    if calendar in ("HOLIDAY", "WEEK_OFF"):
        return "ON_DUTY" if check_in else calendar
    if not check_in:
        return "NOT_CHECKED_IN"
    timing = evaluate_timing(day, check_in, check_out, settings)
    worked = timing["workMinutes"] if work_minutes is None else work_minutes
    if check_out and 0 < worked < settings["minimumHalfDayMinutes"]:
        return "HALF_DAY"
    if timing["lateMinutes"] > 0:
        return "LATE"
    return "PRESENT"


# ── Machine punches → one daily record ──────────────────────────────────────


def to_store_naive(instant: datetime) -> datetime:
    """Absolute instant (aware) → store wall-clock (naive)."""
    if instant.tzinfo is None:
        return instant
    return instant.astimezone(STORE_TZ).replace(tzinfo=None)


def store_day_bounds_utc(day: date_t) -> Tuple[datetime, datetime]:
    """[start, end) of a store day as aware UTC instants, for SQL range filters."""
    start_local = datetime.combine(day, time(0, 0), tzinfo=STORE_TZ)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def debounce_punches(
    punches: Iterable[datetime], window_seconds: int = PUNCH_DEBOUNCE_SECONDS
) -> List[datetime]:
    """Sort and collapse double-taps: a punch within `window_seconds` of the
    previously KEPT punch is dropped."""
    kept: List[datetime] = []
    for stamp in sorted(punches):
        if kept and (stamp - kept[-1]).total_seconds() < window_seconds:
            continue
        kept.append(stamp)
    return kept


def paired_work_minutes(punches: List[datetime]) -> int:
    """Sum of IN→OUT spans for an EVEN list of punches (0-1, 2-3, ...). The gap
    between a lunch OUT and the next IN is not work time."""
    total = 0.0
    for index in range(0, len(punches) - 1, 2):
        total += (punches[index + 1] - punches[index]).total_seconds() / 60
    return max(0, round(total))


def summarize_punches(
    day: date_t,
    punches: Iterable[datetime],
    settings: Dict[str, Any],
    *,
    today: date_t,
) -> Optional[Dict[str, Any]]:
    """Raw store-wall-clock punches for ONE day → the daily attendance fields.

    Pairing is by time order (not the machine's IN/OUT flag): first punch is
    the check-in; the last punch is the check-out only when the punch count is
    even. An odd count means someone is still in (today) or a punch is missing
    (past day → exception for an admin to resolve; payroll never guesses).

    Returns None when there are no punches.
    """
    kept = debounce_punches(punches)
    if not kept:
        return None

    count = len(kept)
    check_in = kept[0]
    even = count % 2 == 0
    check_out = kept[-1] if even else None
    worked = paired_work_minutes(kept) if even else 0

    exception: Optional[str] = None
    if day < today and not even:
        exception = "MISSING_OUT" if count == 1 else "ODD_PUNCHES"

    timing = evaluate_timing(day, check_in, check_out, settings)
    if exception:
        status = "PENDING_CORRECTION"
    else:
        status = status_after_punch(
            day, check_in, check_out, settings, work_minutes=worked if check_out else None
        )

    return {
        "check_in": check_in,
        "check_out": check_out,
        "punch_count": count,
        "worked_minutes": worked,
        "late_minutes": timing["lateMinutes"],
        "early_leave_minutes": timing["earlyLeaveMinutes"] if check_out else 0,
        "exception": exception,
        "status": status,
    }


# ── Leave ──────────────────────────────────────────────────────────────────

def inclusive_day_count(start: date_t, end: date_t) -> int:
    return max(1, (end - start).days + 1)


def ranges_overlap(a_start: date_t, a_end: date_t, b_start: date_t, b_end: date_t) -> bool:
    return a_start <= b_end and b_start <= a_end


def leave_covers_day(entries: Iterable[Dict[str, Any]], day: date_t, *, statuses=("APPROVED",)) -> bool:
    iso = day.isoformat()
    for entry in entries:
        if entry.get("status") in statuses and str(entry.get("startDate")) <= iso <= str(entry.get("endDate")):
            return True
    return False


def validate_leave_transition(current: str, requested: str) -> None:
    """Raises ValueError for illegal transitions (router maps to 409)."""
    if requested not in LEAVE_STATUSES:
        raise ValueError(f"Unknown leave status '{requested}'.")
    if requested == current:
        raise ValueError(f"Request is already {current}.")
    if requested not in LEAVE_TRANSITIONS.get(current, set()):
        raise ValueError(f"A {current} leave request cannot move to {requested}.")


def is_idempotent_decision(current: str, requested: str) -> bool:
    """Already decided the same way — surfaced as ok+idempotent, not an error
    (mirrors the frontend review/cancel behavior for repeated submits)."""
    return requested in LEAVE_STATUSES and requested == current


def validate_review_note(decision: str, note: Optional[str]) -> None:
    """A rejection reason is required (existing product rule in leaveService)."""
    if decision == "REJECTED" and not (note or "").strip():
        raise ValueError("A reason is required when rejecting leave.")


def month_bounds(month: str) -> Tuple[date_t, date_t]:
    """`YYYY-MM` → (first day, first day of next month) for SQL range filters."""
    try:
        year, mon, _rest = (month + "-01").split("-")
        start = date_t(int(year), int(mon), 1)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid month '{month}' (expected YYYY-MM).") from exc
    end = date_t(start.year + 1, 1, 1) if start.month == 12 else date_t(start.year, start.month + 1, 1)
    return start, end


# ── Performance ──────────────────────────────────────────────────────────────

def validate_rating(rating: Any) -> int:
    try:
        value = int(rating)
    except (TypeError, ValueError) as exc:
        raise ValueError("rating must be an integer.") from exc
    if not RATING_MIN <= value <= RATING_MAX:
        raise ValueError(f"rating must be between {RATING_MIN} and {RATING_MAX}.")
    return value


def performance_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate an employee's reviews: count + average rating (or None)."""
    ratings = [r["rating"] for r in rows if r.get("rating") is not None]
    return {
        "reviews": len(rows),
        "averageRating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "latestPeriod": rows[0]["reviewPeriod"] if rows else None,
        "latestReviewDate": rows[0]["reviewDate"] if rows else None,
    }
