"""
app/storage/base.py — the single storage abstraction (Phase 6).

Everything in the application that touches a binary asset goes through
`StorageProvider`. Nothing else in the codebase is allowed to build a
filesystem path or an S3 key on its own, which is what makes the local
development store and a future S3/CDN deployment interchangeable without
touching product, media or frontend business logic.

Interface (the same six verbs any object store offers):

    put_object(key, data, ...)   → StoredObject
    get_object(key)              → bytes
    open_object(key)             → binary file handle (for streaming/serving)
    delete_object(key)           → bool
    object_exists(key)           → bool
    get_metadata(key)            → ObjectMetadata
    url_for(key)                 → application-level (or CDN) URL

Object keys are portable, slash-delimited identifiers. They are never
filesystem paths: on the local provider a key maps to a file *inside* the
configured root; on S3 the same key is the object name.

The interface is deliberately synchronous (the filesystem and boto3 both
are). Async routes that write bytes should wrap the call in
`fastapi.concurrency.run_in_threadpool`, which `app/api/v1/media.py` does.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, BinaryIO, Dict, Iterator, Optional


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class StorageError(Exception):
    """Base class for every storage failure. Never leaks a filesystem path."""

    def __init__(self, message: str, *, key: Optional[str] = None):
        self.key = key
        super().__init__(message)


class ObjectNotFoundError(StorageError):
    """The requested object key does not exist in the store."""


class InvalidObjectKeyError(StorageError):
    """
    The supplied object key is not a legal key.

    Raised for traversal attempts (`../`, `..\\`), absolute or drive-letter
    paths, control characters, oversized keys, and keys outside the served
    namespaces. Callers should surface this as a 400, never as a path.
    """


class StorageProviderNotConfigured(StorageError):
    """The selected provider cannot run with the current configuration."""


class ObjectCollisionError(StorageError):
    """An object already exists at the key with different bytes."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ObjectMetadata:
    """Provider-independent description of a stored object."""

    key: str
    size: int
    content_type: str
    checksum_sha256: str
    last_modified: Optional[str] = None
    etag: Optional[str] = None
    provider: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "size": self.size,
            "contentType": self.content_type,
            "checksumSha256": self.checksum_sha256,
            "lastModified": self.last_modified,
            "etag": self.etag,
            "provider": self.provider,
        }


@dataclass(frozen=True)
class StoredObject:
    """Result of a successful `put_object`."""

    key: str
    url: str
    metadata: ObjectMetadata
    created: bool = True
    checksum_sha256: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "url": self.url,
            "created": self.created,
            "checksumSha256": self.checksum_sha256 or self.metadata.checksum_sha256,
            "metadata": self.metadata.as_dict(),
        }


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------

class StorageProvider(ABC):
    """
    Object-storage contract.

    A provider owns exactly one namespace root (a directory for the local
    provider, a bucket for S3). Keys are validated before any I/O, so no
    implementation can be coerced into reading or writing outside it.
    """

    #: Stable provider identifier surfaced by `GET /media/storage/status`.
    name: str = "abstract"

    @abstractmethod
    def put_object(
        self,
        key: str,
        data: bytes,
        content_type: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> StoredObject:
        """Write bytes at `key` and return the stored-object descriptor."""

    @abstractmethod
    def get_object(self, key: str) -> bytes:
        """Read the whole object. Raises ObjectNotFoundError when absent."""

    @abstractmethod
    def open_object(self, key: str) -> BinaryIO:
        """
        Open the object for streaming.

        The local provider returns a real file handle so the media route can
        serve it with `FileResponse` without buffering megabytes in memory.
        """

    @abstractmethod
    def delete_object(self, key: str) -> bool:
        """Delete the object. Returns False when it was already absent."""

    @abstractmethod
    def object_exists(self, key: str) -> bool:
        """True when an object exists at `key`."""

    @abstractmethod
    def get_metadata(self, key: str) -> ObjectMetadata:
        """Size / content type / SHA-256 / mtime for `key`."""

    @abstractmethod
    def url_for(self, key: str) -> str:
        """
        The application-level URL for `key`.

        Never a filesystem path. With a CDN configured this is the CDN URL;
        otherwise it is the backend media route, e.g.
        `/api/v1/media/objects/products/PF-.../primary.avif`.
        """

    def list_objects(self, prefix: str = "") -> Iterator[str]:
        """Yield object keys under `prefix`. Optional; local provider only."""
        raise StorageProviderNotConfigured(
            f"Provider '{self.name}' does not support listing."
        )

    def describe(self) -> Dict[str, Any]:
        """
        Non-secret description of the provider, safe to return over the API.

        Implementations must NOT include credentials, bucket secrets or
        absolute filesystem paths.
        """
        return {"provider": self.name}
