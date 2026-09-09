"""
app/services/media/upload_service.py — multipart upload orchestration (Phase 6).

`UploadService` is the thin, route-facing layer over `MediaService` for
"an admin handed us a file". It owns:

  · turning an `UploadFile` into bytes without loading several megabytes
    twice,
  · re-checking the size ceiling BEFORE reading past it (a 500 MB body is
    rejected, not buffered),
  · producing the deterministic object key from real product identity,
  · returning the canonical media URL the frontend stores and renders.

It deliberately does NOT create a media *record*. `media_media_asset` has no
business columns in the existing schema and Phase 6 may not invent them, so
an upload lands in the object store and returns a reference — persisting
that reference onto a product goes through the existing, real
`PATCH /admin/products/{id}` media fields. See §19 of the phase report.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.config import settings
from app.core.logging import get_logger
from app.services.media.media_service import MediaService
from app.services.media.media_validation import (
    MediaValidationError,
    max_image_bytes,
)

logger = get_logger("app.services.media.upload")

# Read the stream in 1 MiB blocks so a large upload never needs to be fully
# resident in memory twice.
_READ_CHUNK = 1024 * 1024


class UploadService:
    """Validate + store an uploaded image file."""

    def __init__(self, db_session=None, media_service: Optional[MediaService] = None):
        self.db = db_session
        self.media = media_service or MediaService(db_session)

    @staticmethod
    async def _read_limited(file_obj, max_bytes: int) -> bytes:
        """
        Read at most `max_bytes + 1` bytes.

        Returning one byte over the ceiling lets the caller distinguish
        "exactly at the limit" from "too big" without reading the rest.
        """
        read = getattr(file_obj, "read", None)
        if read is None:
            raise MediaValidationError("The upload stream could not be read.")

        chunks = []
        total = 0
        while total <= max_bytes:
            data = await read(_READ_CHUNK) if _is_awaitable_read(read) else read(_READ_CHUNK)
            if not data:
                break
            chunks.append(data)
            total += len(data)
        return b"".join(chunks)

    async def store_upload(
        self,
        *,
        file_obj,
        filename: Optional[str] = None,
        declared_content_type: Optional[str] = None,
        namespace: str = "products",
        product_id: Optional[str] = None,
        group: Optional[str] = None,
        replace_existing: bool = False,
    ) -> Dict[str, Any]:
        """
        Validate and persist one uploaded image.

        `declared_content_type` is accepted only as a cross-check: the
        authoritative type comes from the file's own signature, never from
        the client header or the filename.
        """
        ceiling = max_image_bytes()
        name = (filename or getattr(file_obj, "filename", "") or "").strip()
        if not name:
            raise MediaValidationError("The upload has no filename.")

        data = await self._read_limited(file_obj, ceiling)
        if len(data) > ceiling:
            raise MediaValidationError(
                f"Image exceeds the maximum allowed size of "
                f"{int(settings.MAX_IMAGE_SIZE_MB)} MB."
            )

        result = self.media.store_image(
            filename=name,
            data=data,
            namespace=namespace,
            product_id=product_id,
            group=group,
            replace_existing=replace_existing,
        )

        if declared_content_type:
            declared = declared_content_type.split(";", 1)[0].strip().lower()
            if declared and declared != result["contentType"]:
                # Not fatal — the server-side sniff wins — but it is worth
                # surfacing so a mislabelled client is visible rather than
                # silently corrected.
                result["declaredContentTypeMismatch"] = declared
                logger.warning(
                    "Upload content-type mismatch key=%s declared=%s detected=%s",
                    result["key"],
                    declared,
                    result["contentType"],
                )
        return result


def _is_awaitable_read(read) -> bool:
    """True for Starlette's async `UploadFile.read`."""
    import inspect

    return inspect.iscoroutinefunction(read)


__all__ = ["UploadService", "MediaValidationError"]
