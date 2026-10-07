"""
ICLOCK — device-facing endpoints for ZKTeco / eSSL punching machines (ADMS push).

Mounted at the ROOT (`/iclock/*`), NOT under /api/v1: the machine calls fixed
paths on the server address configured in its Cloud-Server menu
(Server Mode = ADMS, Server Address = this host, Port = 80/443).

  GET  /iclock/cdata?SN=…&options=all   handshake — tells the machine what to send
  POST /iclock/cdata?SN=…&table=ATTLOG  punch logs (tab separated, one per line)
  GET  /iclock/getrequest?SN=…          machine asks for server commands (none)
  POST /iclock/devicecmd?SN=…           machine reports a command result (ignored)

Security (the machine cannot log in, so JWT does not apply):
  • only serial numbers registered by an admin are accepted (403 otherwise);
  • optional source-IP allowlist (`ADMS_ALLOWED_IPS`, comma separated);
  • request bodies are capped; at most 2000 lines are read per request;
  • the handshake asks for punch logs ONLY (no OPERLOG), so user records and
    face/fingerprint templates are never sent to this server — and anything
    that arrives for another table is ignored and discarded.

Always answer "OK" to a well-formed push: an error makes the machine retry the
same batch forever. Re-sent batches are harmless (punches are de-duplicated).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.services.employee.adms import parse_attlog
from app.services.employee.attendance_ingest_service import AttendanceIngestService

logger = logging.getLogger("pfv.iclock")

router = APIRouter(prefix="/iclock", tags=["Punching machines (ADMS)"], include_in_schema=False)

MAX_BODY_BYTES = 1_000_000

_HANDSHAKE = (
    "GET OPTION FROM:{sn}\n"
    "ATTLOGStamp=None\n"
    "OPERLOGStamp=None\n"
    "ErrorDelay=30\n"
    "Delay=30\n"
    "TransTimes=00:00;14:05\n"
    "TransInterval=1\n"
    "TransFlag=TransData AttLog\n"
    "Realtime=1\n"
    "Encrypt=0\n"
)


def _text(body: str, status_code: int = 200) -> PlainTextResponse:
    return PlainTextResponse(body, status_code=status_code)


def _ip_allowed(request: Request) -> bool:
    allowed = {ip.strip() for ip in (settings.ADMS_ALLOWED_IPS or "").split(",") if ip.strip()}
    if not allowed:
        return True
    client = request.client.host if request.client else ""
    return client in allowed


async def _known_device(request: Request, db: AsyncSession, serial: str):
    """(device, error_response). Unknown/inactive/blocked callers get a 403."""
    if not _ip_allowed(request):
        logger.warning("iclock: blocked source ip %s", request.client.host if request.client else "?")
        return None, _text("Forbidden", 403)
    device = await AttendanceIngestService(db).device_by_serial((serial or "").strip())
    if device is None or not device.is_active:
        logger.warning("iclock: unregistered or inactive device SN=%r", serial)
        return None, _text("Unknown device", 403)
    return device, None


@router.get("/cdata")
async def handshake(request: Request, SN: str = "", db: AsyncSession = Depends(get_db)):
    device, error = await _known_device(request, db, SN)
    if error:
        return error
    await AttendanceIngestService(db).touch(device)
    return _text(_HANDSHAKE.format(sn=device.serial_number))


@router.post("/cdata")
async def push(
    request: Request,
    SN: str = "",
    table: str = "",
    db: AsyncSession = Depends(get_db),
):
    device, error = await _known_device(request, db, SN)
    if error:
        return error

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return _text("Payload too large", 413)
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return _text("Payload too large", 413)

    service = AttendanceIngestService(db)
    if table.upper() != "ATTLOG":
        # OPERLOG / ATTPHOTO / user & template tables: acknowledge, store nothing.
        await service.touch(device)
        return _text("OK")

    punches, rejected = parse_attlog(raw.decode("utf-8", errors="ignore"))
    counts = await service.ingest(device, punches)
    logger.info(
        "iclock: SN=%s accepted=%s duplicates=%s unmapped=%s rejectedLines=%s futureRejected=%s",
        device.serial_number,
        counts["accepted"],
        counts["duplicates"],
        counts["unmapped"],
        rejected,
        counts["futureRejected"],
    )
    return _text("OK")


@router.get("/getrequest")
async def get_request(request: Request, SN: str = "", db: AsyncSession = Depends(get_db)):
    device, error = await _known_device(request, db, SN)
    if error:
        return error
    await AttendanceIngestService(db).touch(device)
    return _text("OK")


@router.post("/devicecmd")
async def device_command_result(request: Request, SN: str = "", db: AsyncSession = Depends(get_db)):
    device, error = await _known_device(request, db, SN)
    if error:
        return error
    return _text("OK")
