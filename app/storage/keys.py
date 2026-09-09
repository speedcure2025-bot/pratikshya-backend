"""
app/storage/keys.py — object-key and filename safety (Phase 6).

This module is the ONLY place that decides what a legal object key looks
like. Every provider and every route calls it before touching I/O, so a
client request can never escape the configured storage root.

Object key convention (documented in PHASE_6_IMPLEMENTATION_REPORT.md §5)
------------------------------------------------------------------------
    {namespace}/{...path...}/{filename}

with `namespace` restricted to a closed vocabulary (NAMESPACE_PRODUCTS,
NAMESPACE_COLLECTIONS, …). Product media therefore lives at:

    products/{PRODUCT_ID}/{filename}

Properties:
  · deterministic  — derived from the asset's own identity, never random
  · collision-safe — the id segment is the product id, the leaf is a
                     sanitised filename; two different products can never
                     collide and the same product cannot silently overwrite
                     an unrelated file
  · S3-portable    — slash-delimited, lowercase, no drive letters, no
                     backslashes, no spaces, no leading dots
  · path-free      — a key never encodes a machine path, so it cannot leak
                     `C:\\...` or `/srv/...` to a client

Filenames are sanitised rather than trusted: uploaded names may contain
traversal, separators, null bytes or control characters, and none of that
is allowed to reach the filesystem.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Tuple
from urllib.parse import unquote

from app.storage.base import InvalidObjectKeyError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Closed namespace vocabulary. A key whose first segment is not one of these
#: is rejected outright, which is what stops a "media" route from becoming a
#: generic file server over the storage root.
NAMESPACE_PRODUCTS = "products"
NAMESPACE_COLLECTIONS = "collections"
NAMESPACE_HERO = "hero"
NAMESPACE_MARKETING = "marketing"
NAMESPACE_UPLOADS = "uploads"

ALLOWED_NAMESPACES: Tuple[str, ...] = (
    NAMESPACE_PRODUCTS,
    NAMESPACE_COLLECTIONS,
    NAMESPACE_HERO,
    NAMESPACE_MARKETING,
    NAMESPACE_UPLOADS,
)

OBJECT_KEY_MAX_LENGTH = 900
KEY_SEGMENT_MAX_LENGTH = 200
FILENAME_MAX_LENGTH = 128
MAX_EXTENSION_LENGTH = 8

#: A segment is alphanumeric plus `.`, `-`, `_`, starting with an alphanumeric.
#: Upper case is allowed because catalogue product ids are upper case
#: (`PF-W-SAR-SIL-0001`) and an object key must map 1:1 onto the asset it
#: names. No spaces, no unicode, no separators — the character set S3 handles
#: cleanly. Note `sanitize_filename`/`sanitize_key_segment` still LOWER-case
#: untrusted input; the upper case here only accepts already-controlled ids.
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
#: Windows drive prefix (`C:`) or UNC share.
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
#: Characters that must never survive into a filename.
_FORBIDDEN_FILENAME_CHARS_RE = re.compile(r"[^a-z0-9._-]+")
#: Product ids follow the existing catalogue pattern (see PRODUCT_ID_RE).
_PRODUCT_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,35}$")


# ---------------------------------------------------------------------------
# Filename sanitisation
# ---------------------------------------------------------------------------

def split_extension(name: str) -> Tuple[str, str]:
    """Split `photo.final.JPG` into (`photo.final`, `jpg`). No dot → `""`."""
    text = str(name or "")
    dot = text.rfind(".")
    if dot <= 0 or dot == len(text) - 1:
        return text, ""
    return text[:dot], text[dot + 1:]


def sanitize_filename(name: str, *, max_length: int = FILENAME_MAX_LENGTH) -> str:
    """
    Turn an untrusted filename into a safe storage leaf.

    Guarantees:
      · no path separators (POSIX or Windows) and no drive letter
      · no traversal segments, null bytes or control characters
      · lowercase `[a-z0-9._-]` only, no leading dot or dash
      · the useful extension is preserved when it is a sane token
      · collisions are prevented by construction (identical input → identical
        output; different input → different output unless they normalise to
        the same safe name, which the caller resolves via checksum compare)

    Raises InvalidObjectKeyError when nothing safe remains.
    """
    raw = str(name or "")
    if not raw.strip():
        raise InvalidObjectKeyError("Filename is empty.")
    if "\x00" in raw:
        raise InvalidObjectKeyError("Filename contains a null byte.")

    # Fold unicode, then take the basename using BOTH separators so a Windows
    # client cannot smuggle a directory in through a backslash.
    folded = unicodedata.normalize("NFKD", raw)
    folded = folded.encode("ascii", "ignore").decode("ascii")
    folded = folded.replace("\\", "/")
    basename = folded.rsplit("/", 1)[-1]

    stem, extension = split_extension(basename)
    stem = _FORBIDDEN_FILENAME_CHARS_RE.sub("-", stem.lower()).strip("._-")
    stem = re.sub(r"-{2,}", "-", stem)

    extension = _FORBIDDEN_FILENAME_CHARS_RE.sub("", extension.lower())
    if len(extension) > MAX_EXTENSION_LENGTH:
        extension = ""

    if not stem:
        # e.g. "...." or "///" — nothing safe is left.
        raise InvalidObjectKeyError("Filename contains no usable characters.")

    budget = max_length - (len(extension) + 1 if extension else 0)
    if budget < 1:
        raise InvalidObjectKeyError("Filename is too long.")
    stem = stem[:budget].rstrip("._-")
    if not stem:
        raise InvalidObjectKeyError("Filename is too long.")

    return f"{stem}.{extension}" if extension else stem


def sanitize_key_segment(segment: str) -> str:
    """Sanitise one key path segment (e.g. a product id) to `[a-z0-9._-]`."""
    text = unicodedata.normalize("NFKD", str(segment or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _FORBIDDEN_FILENAME_CHARS_RE.sub("-", text.lower()).strip("._-")
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    if not cleaned:
        raise InvalidObjectKeyError("Key segment contains no usable characters.")
    if len(cleaned) > KEY_SEGMENT_MAX_LENGTH:
        raise InvalidObjectKeyError("Key segment is too long.")
    return cleaned


def validate_product_id(product_id: str) -> str:
    """
    Validate a product id for use as a key segment.

    Uses the catalogue's own permanent-id pattern so a key can always be
    traced back to a real product record.
    """
    candidate = str(product_id or "").strip().upper()
    if not _PRODUCT_ID_RE.match(candidate):
        raise InvalidObjectKeyError(
            "Product id is not a valid catalogue id (expected ^[A-Z0-9][A-Z0-9-]{1,35}$)."
        )
    return candidate


def product_object_key(product_id: str, filename: str) -> str:
    """
    Canonical product-media object key: `products/{PRODUCT_ID}/{filename}`.
    """
    pid = validate_product_id(product_id)
    return f"{NAMESPACE_PRODUCTS}/{pid}/{sanitize_filename(filename)}"


def object_key_for_namespace(namespace: str, *segments: str, filename: str) -> str:
    """Build `{namespace}/{seg}/{seg}/{filename}` with every part sanitised."""
    if namespace not in ALLOWED_NAMESPACES:
        raise InvalidObjectKeyError(
            f"Namespace '{namespace}' is not a served media namespace."
        )
    parts = [namespace] + [sanitize_key_segment(seg) for seg in segments if str(seg).strip()]
    parts.append(sanitize_filename(filename))
    return "/".join(parts)


# ---------------------------------------------------------------------------
# Key validation
# ---------------------------------------------------------------------------

def decode_object_key(key: str) -> str:
    """
    Percent-decode a key exactly once before validation.

    A client can encode traversal (`%2e%2e%2f`). FastAPI already decodes the
    path once, so decoding again here means a double-encoded attempt is still
    caught. Legitimate keys never contain `%` (the sanitiser strips it), so
    this cannot corrupt a real key.
    """
    text = str(key or "")
    if "%" in text:
        try:
            decoded = unquote(text, errors="strict")
        except Exception:  # pragma: no cover - defensive
            decoded = text
        # Only accept the decode when it is idempotent-safe, i.e. it does not
        # introduce a new escape sequence that would need another pass.
        if decoded != text:
            text = decoded
    return text


def normalize_object_key(
    key: str,
    *,
    allowed_namespaces: Iterable[str] = ALLOWED_NAMESPACES,
) -> str:
    """
    Validate and canonicalise an object key. Returns the safe key.

    Raises InvalidObjectKeyError for anything unsafe. The returned value is
    what providers are allowed to join onto their root — it contains no
    traversal, no separator other than `/`, and no absolute or drive form.
    """
    raw = str(key or "")
    if not raw.strip():
        raise InvalidObjectKeyError("Object key is empty.")
    if len(raw) > OBJECT_KEY_MAX_LENGTH:
        raise InvalidObjectKeyError("Object key is too long.")

    text = decode_object_key(raw)

    # Control characters / null bytes.
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise InvalidObjectKeyError("Object key contains control characters.")
    if "\x00" in text:
        raise InvalidObjectKeyError("Object key contains a null byte.")

    # Backslashes are never legal in an object key: they are the Windows
    # separator, and accepting them would let `..\..\windows\win.ini` through.
    if "\\" in text:
        raise InvalidObjectKeyError("Object key must not contain backslashes.")

    # Absolute POSIX path or UNC/protocol-relative form.
    if text.startswith("/"):
        raise InvalidObjectKeyError("Object key must be relative to the storage root.")
    if text.startswith("//"):
        raise InvalidObjectKeyError("Object key must not be a UNC path.")

    # Windows drive letter (`C:\...`, `D:/...`).
    if _DRIVE_RE.match(text):
        raise InvalidObjectKeyError("Object key must not contain a drive letter.")

    # A trailing slash denotes a prefix, not an object.
    if text.endswith("/"):
        raise InvalidObjectKeyError("Object key must point at an object, not a prefix.")

    segments = text.split("/")
    if len(segments) < 2:
        raise InvalidObjectKeyError(
            "Object key must include a namespace segment (e.g. 'products/…')."
        )

    cleaned: list[str] = []
    for index, segment in enumerate(segments):
        if segment == "":
            raise InvalidObjectKeyError("Object key must not contain empty segments.")
        if segment in (".", ".."):
            raise InvalidObjectKeyError("Object key must not contain traversal segments.")
        if segment.startswith("."):
            raise InvalidObjectKeyError("Object key segments must not start with a dot.")
        if len(segment) > KEY_SEGMENT_MAX_LENGTH:
            raise InvalidObjectKeyError("Object key segment is too long.")
        if not _SEGMENT_RE.match(segment):
            raise InvalidObjectKeyError(
                f"Object key segment {index + 1} contains unsupported characters."
            )
        cleaned.append(segment)

    allowed = tuple(allowed_namespaces)
    if cleaned[0] not in allowed:
        raise InvalidObjectKeyError(
            f"Object key namespace '{cleaned[0]}' is not served by the media API."
        )

    normalized = "/".join(cleaned)
    if len(normalized) > OBJECT_KEY_MAX_LENGTH:
        raise InvalidObjectKeyError("Object key is too long.")
    return normalized


def is_safe_object_key(
    key: str,
    *,
    allowed_namespaces: Iterable[str] = ALLOWED_NAMESPACES,
) -> bool:
    """Non-raising form of `normalize_object_key`."""
    try:
        normalize_object_key(key, allowed_namespaces=allowed_namespaces)
        return True
    except InvalidObjectKeyError:
        return False


def object_key_extension(key: str) -> str:
    """Lowercase extension of a key, without the dot (`avif`)."""
    _stem, extension = split_extension(str(key or ""))
    return extension.lower()
