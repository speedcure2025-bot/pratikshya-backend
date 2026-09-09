"""
app/storage/local.py — filesystem-backed object storage (Phase 6).

This is the provider the project runs on today: no AWS credentials, no
Docker, no MinIO, no Redis, no Celery. It behaves like an object store from
the application's point of view — you hand it an object key and bytes, and
it hands back a stored-object descriptor and an application-level URL —
while the bytes happen to live under `LOCAL_MEDIA_ROOT`.

Layout on disk mirrors the key space exactly:

    {LOCAL_MEDIA_ROOT}/products/PF-W-SAR-SIL-0001/primary.avif
    {LOCAL_MEDIA_ROOT}/collections/fabrics/silk/PF-COL-FAB-SIL-0001/01.avif
    {LOCAL_MEDIA_ROOT}/hero/hero001.avif

Security model
--------------
Every path is produced by `_resolve()`, which:
  1. validates the key with `normalize_object_key` (no `..`, no `\\`, no
     absolute or drive-letter form, no empty/dot segments, namespace
     allow-list, character allow-list), and then
  2. resolves the joined path and asserts it is still inside the root.

Step 2 is the backstop for symlink tricks and platform-specific path
semantics: `Path.resolve()` follows symlinks, so a link planted inside the
root that points outside is rejected rather than followed. Windows drive
letters and UNC forms are rejected in step 1 before `Path` ever sees them.

Writes are atomic (temp file + `os.replace`) so an interrupted import can
never leave a half-written object that later passes a checksum compare.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterator, Optional

from app.storage.base import (
    InvalidObjectKeyError,
    ObjectMetadata,
    ObjectNotFoundError,
    StorageProvider,
    StoredObject,
)
from app.storage.keys import ALLOWED_NAMESPACES, normalize_object_key
from app.storage.signatures import MAX_SNIFF_BYTES, sniff_content_type
from app.storage.urls import build_media_url

# Content types the local provider can serve. `mimetypes` is consulted first
# (it knows .avif on modern Python), then this table fills the gaps that the
# Windows registry-backed lookup historically gets wrong.
_CONTENT_TYPE_BY_EXTENSION: Dict[str, str] = {
    "avif": "image/avif",
    "webp": "image/webp",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "svg": "image/svg+xml",
}

DEFAULT_CONTENT_TYPE = "application/octet-stream"

# SHA-256 is read in 1 MiB chunks so a 4 MB asset never needs 4 MB of stack.
_HASH_CHUNK_SIZE = 1024 * 1024


def content_type_for_key(key: str, fallback: Optional[str] = None) -> str:
    """Best content type for an object key, from its extension."""
    extension = Path(str(key)).suffix.lower().lstrip(".")
    if extension in _CONTENT_TYPE_BY_EXTENSION:
        return _CONTENT_TYPE_BY_EXTENSION[extension]
    guessed, _encoding = mimetypes.guess_type(str(key))
    if guessed:
        return guessed
    return fallback or DEFAULT_CONTENT_TYPE


def content_type_for_object(path: Path, key: Optional[str] = None) -> str:
    """
    The TRUE content type of a stored object.

    The leading bytes win over the extension. This matters because part of
    the existing asset library is mislabelled (a `.avif` name carrying JPEG
    or PNG bytes — 126 of the 238 shipped assets, audited in Phase 6). The
    media route must send a `Content-Type` the bytes actually match, and no
    file is renamed or converted to achieve that.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(MAX_SNIFF_BYTES)
    except OSError:
        head = b""
    sniffed = sniff_content_type(head)
    return sniffed or content_type_for_key(key or path.name)


def sha256_bytes(data: bytes) -> str:
    """SHA-256 hex digest of an in-memory buffer."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """SHA-256 hex digest of a file, streamed."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_stream(stream: BinaryIO) -> str:
    """SHA-256 hex digest of a readable binary stream, streamed."""
    digest = hashlib.sha256()
    while True:
        chunk = stream.read(_HASH_CHUNK_SIZE)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


class LocalStorageProvider(StorageProvider):
    """
    Object storage backed by a directory on the local filesystem.

    The root is created on first use and persists across restarts; it is
    deliberately NOT a temp folder (see PHASE_6_IMPLEMENTATION_REPORT.md §23).
    """

    name = "local"

    def __init__(
        self,
        root: Path | str,
        *,
        create_root: bool = True,
        allowed_namespaces: Optional[tuple] = None,
    ):
        self._root = Path(root).expanduser().resolve()
        self._allowed_namespaces = allowed_namespaces
        if create_root:
            self.ensure_root()

    # -- construction helpers ------------------------------------------------

    def ensure_root(self) -> Path:
        """Create the storage root if absent. Idempotent."""
        self._root.mkdir(parents=True, exist_ok=True)
        return self._root

    @property
    def root(self) -> Path:
        return self._root

    def _namespaces(self) -> tuple:
        """Namespace allow-list in force for this instance."""
        return self._allowed_namespaces if self._allowed_namespaces is not None else ALLOWED_NAMESPACES

    # -- internal path mapping ----------------------------------------------

    def _resolve(self, key: str) -> Path:
        """
        Map an object key to an absolute path INSIDE the root, or raise.

        This is the single choke point for path safety.
        """
        safe_key = normalize_object_key(key, allowed_namespaces=self._namespaces())
        # `joinpath` with a validated relative key can only descend.
        candidate = (self._root.joinpath(*safe_key.split("/"))).resolve()
        if candidate == self._root:
            raise InvalidObjectKeyError("Object key must point at an object.", key=key)
        try:
            inside = candidate.is_relative_to(self._root)
        except AttributeError:  # pragma: no cover - Python < 3.9
            inside = str(candidate).startswith(str(self._root) + os.sep)
        if not inside:
            raise InvalidObjectKeyError(
                "Object key escapes the configured storage root.", key=key
            )
        return candidate

    # -- provider interface --------------------------------------------------

    def put_object(
        self,
        key: str,
        data: bytes,
        content_type: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> StoredObject:
        if data is None:
            raise InvalidObjectKeyError("Cannot store an empty payload.", key=key)
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)

        checksum = sha256_bytes(data)
        # Atomic replace: a reader never observes a partial object, and an
        # interrupted run leaves either the old object or the new one.
        handle = tempfile.NamedTemporaryFile(
            dir=str(target.parent), prefix=".upload-", suffix=".part", delete=False
        )
        temp_path = Path(handle.name)
        try:
            with handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

        stored_key = str(target.relative_to(self._root)).replace(os.sep, "/")
        object_metadata = ObjectMetadata(
            key=stored_key,
            size=len(data),
            content_type=content_type or content_type_for_key(stored_key),
            checksum_sha256=checksum,
            last_modified=_isoformat(target),
            etag=checksum[:32],
            provider=self.name,
        )
        return StoredObject(
            key=stored_key,
            url=build_media_url(stored_key),
            metadata=object_metadata,
            created=True,
            checksum_sha256=checksum,
        )

    def get_object(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.is_file():
            raise ObjectNotFoundError("Object not found.", key=path_key(self, path))
        return path.read_bytes()

    def open_object(self, key: str) -> BinaryIO:
        path = self._resolve(key)
        if not path.is_file():
            raise ObjectNotFoundError("Object not found.", key=path_key(self, path))
        return open(path, "rb")

    def local_path(self, key: str) -> Path:
        """
        Validated absolute path for an existing object.

        Exposed for `FileResponse` streaming only. It is never serialised
        into an API response.
        """
        path = self._resolve(key)
        if not path.is_file():
            raise ObjectNotFoundError("Object not found.", key=path_key(self, path))
        return path

    def delete_object(self, key: str) -> bool:
        path = self._resolve(key)
        if not path.is_file():
            return False
        path.unlink()
        _prune_empty_parents(self._root, path.parent)
        return True

    def object_exists(self, key: str) -> bool:
        try:
            path = self._resolve(key)
        except InvalidObjectKeyError:
            return False
        return path.is_file()

    def get_metadata(self, key: str) -> ObjectMetadata:
        path = self._resolve(key)
        if not path.is_file():
            raise ObjectNotFoundError("Object not found.", key=path_key(self, path))
        stored_key = str(path.relative_to(self._root)).replace(os.sep, "/")
        checksum = sha256_file(path)
        return ObjectMetadata(
            key=stored_key,
            size=path.stat().st_size,
            content_type=content_type_for_object(path, stored_key),
            checksum_sha256=checksum,
            last_modified=_isoformat(path),
            etag=checksum[:32],
            provider=self.name,
        )

    def url_for(self, key: str) -> str:
        return build_media_url(key)

    def list_objects(self, prefix: str = "") -> Iterator[str]:
        """Yield every object key under `prefix`, in stable sorted order."""
        if not self._root.exists():
            return
        normalized_prefix = ""
        if prefix:
            normalized_prefix = normalize_object_key(
                f"{prefix.rstrip('/')}/placeholder",
                allowed_namespaces=self._namespaces(),
            ).rsplit("/", 1)[0]
        base = self._root.joinpath(*normalized_prefix.split("/")) if normalized_prefix else self._root
        if not base.exists():
            return
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            relative = str(path.relative_to(self._root)).replace(os.sep, "/")
            if relative.startswith("."):
                continue
            yield relative

    def describe(self) -> Dict[str, Any]:
        """
        Non-secret provider description.

        The absolute root is deliberately omitted: nothing that reaches the
        browser may reveal where the bytes live on the server.
        """
        return {
            "provider": self.name,
            "urlPrefix": build_media_url_prefix_only(),
            "rootReady": self._root.exists(),
            "persistent": True,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def path_key(provider: LocalStorageProvider, path: Path) -> str:
    """
    Best-effort object key for an absolute path, for error reporting only.

    Never includes the storage root, so an error message cannot leak the
    server's filesystem layout.
    """
    try:
        return str(path.relative_to(provider.root)).replace(os.sep, "/")
    except ValueError:  # pragma: no cover - defensive
        return path.name


def build_media_url_prefix_only() -> str:
    """The media URL prefix without a key (`/api/v1/media/objects`)."""
    from app.storage.urls import media_url_prefix

    return media_url_prefix()


def _isoformat(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _prune_empty_parents(root: Path, start: Path) -> None:
    """
    Remove now-empty directories left behind by a delete.

    Bounded to the storage root and never touches a non-empty directory, so
    this can only ever tidy up after itself.
    """
    current = start
    while current != root:
        try:
            if any(current.iterdir()):
                return
            current.rmdir()
        except OSError:
            return
        current = current.parent
