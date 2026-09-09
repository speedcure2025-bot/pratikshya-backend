"""
app/services/media/media_validation.py — media policy enforcement (Phase 6).

The single place that decides whether a file may enter the object store.
It reuses the EXISTING backend configuration rather than inventing a second
policy:

    settings.allowed_image_types     → which MIME types are accepted
    settings.MAX_IMAGE_SIZE_MB       → size ceiling

The signature table itself lives in `app/storage/signatures.py` so that the
upload policy and the local provider's `Content-Type` reporting can never
disagree about what a file really is.

Filenames are never trusted. A file is accepted only when ALL of these hold:

  1. it is non-empty (an empty upload is a malformed request, not an image)
  2. it is within `MAX_IMAGE_SIZE_MB`
  3. its extension maps to one of the configured image MIME types
  4. its content signature maps to one of those MIME types

Note on step 3/4 disagreement: the AUTHORITY is the sniffed type — that is
what the object is stored and served as. `validate_image_bytes` reports the
disagreement (`extension_mismatch`) instead of silently trusting the
filename; it only rejects when either side is outside the configured policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.config import settings
from app.storage.signatures import (
    MAX_SNIFF_BYTES,
    extension_to_mime,
    mime_to_extensions,
    sniff_content_type,
)


class MediaValidationError(ValueError):
    """Raised when an upload violates the house media policy."""


def allowed_image_mime_types() -> List[str]:
    """The configured image policy, lower-cased."""
    return [item.lower() for item in settings.allowed_image_types]


def allowed_image_extensions() -> List[str]:
    """Extensions permitted by the configured image policy."""
    table = mime_to_extensions()
    allowed = set(allowed_image_mime_types())
    extensions: List[str] = []
    for mime, names in table.items():
        if mime in allowed:
            extensions.extend("." + name for name in names)
    return sorted(extensions)


def extension_of(filename: str) -> str:
    """Lower-cased extension with its dot, or `""`."""
    text = str(filename or "")
    dot = text.rfind(".")
    if dot < 0 or dot == len(text) - 1:
        return ""
    return text[dot:].lower()


def mime_for_extension(filename: str) -> Optional[str]:
    """MIME type implied by the extension, or None when unknown."""
    return extension_to_mime().get(extension_of(filename))


@dataclass(frozen=True)
class ValidatedImage:
    """Outcome of a successful policy check."""

    content_type: str
    extension: str
    size: int
    checksum_sha256: str
    #: True when the sniffed type differs from what the extension claims.
    extension_mismatch: bool = False
    declared_extension_type: str = ""


def max_image_bytes() -> int:
    return int(settings.MAX_IMAGE_SIZE_MB) * 1024 * 1024


def validate_image_bytes(
    filename: str,
    data: bytes,
    *,
    allowed_types: Optional[List[str]] = None,
    max_bytes: Optional[int] = None,
) -> ValidatedImage:
    """
    Apply the full media policy to an upload candidate.

    Raises MediaValidationError with a user-safe message (never a path).
    """
    allowed = [item.lower() for item in (allowed_types or allowed_image_mime_types())]
    ceiling = max_bytes if max_bytes is not None else max_image_bytes()

    if data is None:
        raise MediaValidationError("No file content was received.")
    if len(data) == 0:
        raise MediaValidationError("The uploaded file is empty.")
    if len(data) > ceiling:
        raise MediaValidationError(
            f"Image is {len(data) / (1024 * 1024):.1f} MB — the maximum allowed "
            f"size is {int(settings.MAX_IMAGE_SIZE_MB)} MB."
        )

    ext = extension_of(filename)
    if not ext:
        raise MediaValidationError(
            "The uploaded file has no extension, so its type cannot be verified."
        )

    table = extension_to_mime()
    if ext not in table:
        raise MediaValidationError(
            f"Unsupported file extension '{ext}'. Allowed: "
            f"{', '.join(allowed_image_extensions())}."
        )
    declared_type = table[ext]
    if declared_type not in allowed:
        raise MediaValidationError(
            f"'{declared_type}' is not an allowed image type. Allowed: "
            f"{', '.join(sorted(allowed))}."
        )

    sniffed = sniff_content_type(data)
    if sniffed is None:
        raise MediaValidationError(
            "The file content does not match any supported image format, so it "
            "was rejected. The filename alone is never trusted."
        )
    if sniffed not in allowed:
        raise MediaValidationError(
            f"Detected '{sniffed}', which is not an allowed image type. Allowed: "
            f"{', '.join(sorted(allowed))}."
        )

    from app.storage.local import sha256_bytes

    return ValidatedImage(
        content_type=sniffed,
        extension=ext,
        size=len(data),
        checksum_sha256=sha256_bytes(data),
        extension_mismatch=sniffed != declared_type,
        declared_extension_type=declared_type,
    )


__all__ = [
    "MediaValidationError",
    "ValidatedImage",
    "MAX_SNIFF_BYTES",
    "allowed_image_mime_types",
    "allowed_image_extensions",
    "extension_of",
    "mime_for_extension",
    "max_image_bytes",
    "sniff_content_type",
    "validate_image_bytes",
]
