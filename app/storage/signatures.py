"""
app/storage/signatures.py — content-signature ("magic bytes") detection.

Lives in the storage layer because both the media policy
(`app/services/media/media_validation.py`) and the local provider's
`Content-Type` reporting need the same answer, and they must not be allowed
to drift apart.

Implemented directly rather than through `python-magic` because libmagic is
a system dependency that is routinely missing on Windows development
machines, and this phase must run with no extra infrastructure.

A filename is never trusted: `sniff_content_type` returns None for bytes it
does not recognise, and callers treat that as a rejection rather than a
guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

MAX_SNIFF_BYTES = 64


@dataclass(frozen=True)
class Signature:
    content_type: str
    extensions: Tuple[str, ...]
    prefixes: Tuple[Tuple[int, bytes], ...] = ()


SIGNATURES: Tuple[Signature, ...] = (
    Signature("image/jpeg", ("jpg", "jpeg"), ((0, b"\xff\xd8\xff"),)),
    Signature("image/png", ("png",), ((0, b"\x89PNG\r\n\x1a\n"),)),
    Signature("image/gif", ("gif",), ((0, b"GIF87a"), (0, b"GIF89a"))),
    Signature("image/webp", ("webp",)),
    Signature("image/avif", ("avif",)),
    Signature("video/mp4", ("mp4", "m4v")),
    Signature("video/webm", ("webm",), ((0, b"\x1a\x45\xdf\xa3"),)),
)

#: Brands that identify a still AVIF/HEIF image rather than a video container.
AVIF_IMAGE_BRANDS = frozenset({b"avif", b"avis", b"mif1", b"msf1", b"heic", b"heix"})

#: Brands that identify an MP4-family video (rejected by the image policy).
MP4_VIDEO_BRANDS = frozenset({
    b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"mp4v",
    b"M4V ", b"M4VH", b"M4VP", b"avc1", b"dash", b"XAVC", b"qt  ",
})


def webp_confirmed(head: bytes) -> bool:
    """`RIFF????WEBP` — the RIFF magic alone is not enough (WAV shares it)."""
    return len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def ftyp_brand(head: bytes) -> Optional[bytes]:
    """The ISO-BMFF major brand, or None when this is not an ISO container."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return head[8:12]
    return None


def sniff_content_type(data: bytes) -> Optional[str]:
    """Identify the real content type from the leading bytes, or None."""
    if not data:
        return None
    head = bytes(data[:MAX_SNIFF_BYTES])

    if webp_confirmed(head):
        return "image/webp"

    brand = ftyp_brand(head)
    if brand is not None:
        if brand in AVIF_IMAGE_BRANDS:
            return "image/avif"
        if brand in MP4_VIDEO_BRANDS:
            return "video/mp4"
        return None

    for signature in SIGNATURES:
        for offset, magic in signature.prefixes:
            if head[offset:offset + len(magic)] == magic:
                return signature.content_type
    return None


def extension_to_mime() -> dict:
    """`{'.avif': 'image/avif', …}` from the signature table."""
    table = {}
    for signature in SIGNATURES:
        for extension in signature.extensions:
            table.setdefault("." + extension, signature.content_type)
    return table


def mime_to_extensions() -> dict:
    table: dict = {}
    for signature in SIGNATURES:
        table.setdefault(signature.content_type, list(signature.extensions))
    return table


__all__ = [
    "MAX_SNIFF_BYTES",
    "Signature",
    "SIGNATURES",
    "AVIF_IMAGE_BRANDS",
    "MP4_VIDEO_BRANDS",
    "webp_confirmed",
    "ftyp_brand",
    "sniff_content_type",
    "extension_to_mime",
    "mime_to_extensions",
]
