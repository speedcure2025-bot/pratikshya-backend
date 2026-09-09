"""
app/storage/s3.py — interface-ready S3 adapter (NOT active in Phase 6).

Real AWS credentials have not been provided, so this adapter deliberately
does nothing that touches the network:

  · `boto3` is imported lazily, INSIDE `_client()`, and only when a provider
    has been fully configured with credentials. Importing this module costs
    nothing and performs no AWS calls.
  · Constructing it without credentials raises
    `StorageProviderNotConfigured`, so a mis-set `STORAGE_PROVIDER=s3` fails
    loudly at startup instead of silently doing something else.
  · No fake credentials are invented, and no bucket is contacted by tests.

It exists so that switching from local to S3 is a configuration change plus
a credential drop-in: the object keys, the URL contract and every caller in
`app/services/media` and the frontend stay exactly the same.

The method set mirrors `LocalStorageProvider` one-for-one —
put / get / open / delete / exists / metadata / url / list — which is the
whole point of `StorageProvider`.
"""

from __future__ import annotations

from typing import Any, BinaryIO, Dict, Iterator, Optional

from app.storage.base import (
    ObjectMetadata,
    ObjectNotFoundError,
    StorageProvider,
    StorageProviderNotConfigured,
    StoredObject,
)
from app.storage.keys import normalize_object_key
from app.storage.local import content_type_for_key, sha256_bytes, sha256_stream
from app.storage.urls import build_media_url

#: Marker values shipped in `.env.example`. Treated as "not configured" so a
#: copied-but-unfilled `.env` cannot be mistaken for real credentials.
PLACEHOLDER_CREDENTIAL_PREFIXES = ("your-", "changeme", "placeholder", "")


def _is_placeholder(value: Optional[str]) -> bool:
    text = (value or "").strip()
    if not text:
        return True
    lowered = text.lower()
    return any(lowered.startswith(prefix) for prefix in PLACEHOLDER_CREDENTIAL_PREFIXES)


class S3StorageProvider(StorageProvider):
    """
    S3-compatible object storage.

    Configuration contract (documented in `.env.example`, never required in
    this phase):

        STORAGE_PROVIDER=s3
        AWS_BUCKET_NAME=…
        AWS_REGION=…
        AWS_ACCESS_KEY_ID=…
        AWS_SECRET_ACCESS_KEY=…
        MEDIA_CDN_BASE_URL=https://cdn.example.com   # optional

    Object keys are used verbatim as S3 object names — the same
    `products/{PRODUCT_ID}/{filename}` convention the local provider uses —
    so a migrated bucket is a straight copy of the local root.
    """

    name = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        access_key_id: Optional[str],
        secret_access_key: Optional[str],
        endpoint_url: Optional[str] = None,
    ):
        self._bucket = (bucket or "").strip()
        self._region = (region or "").strip()
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._endpoint_url = (endpoint_url or "").strip() or None
        self._client = None

        missing = [
            label
            for label, ok in (
                ("AWS_BUCKET_NAME", bool(self._bucket)),
                ("AWS_REGION", bool(self._region)),
                ("AWS_ACCESS_KEY_ID", not _is_placeholder(self._access_key_id)),
                ("AWS_SECRET_ACCESS_KEY", not _is_placeholder(self._secret_access_key)),
            )
            if not ok
        ]
        if missing:
            raise StorageProviderNotConfigured(
                "STORAGE_PROVIDER=s3 was selected but the S3 configuration is "
                "incomplete: " + ", ".join(missing) + ". "
                "Phase 6 runs on STORAGE_PROVIDER=local; real AWS credentials "
                "have not been provisioned."
            )

    # -- internals -----------------------------------------------------------

    def _require_client(self):
        """Lazily import boto3. Never called in this phase."""
        if self._client is not None:
            return self._client
        try:
            import boto3  # noqa: PLC0415 — intentionally lazy
        except ImportError as exc:  # pragma: no cover - boto3 is in requirements
            raise StorageProviderNotConfigured(
                "boto3 is not installed; the S3 provider cannot start."
            ) from exc
        self._client = boto3.client(
            "s3",
            region_name=self._region,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
            endpoint_url=self._endpoint_url,
        )
        return self._client

    @staticmethod
    def _not_found(key: str) -> ObjectNotFoundError:
        return ObjectNotFoundError("Object not found in S3.", key=key)

    # -- provider interface --------------------------------------------------

    def put_object(
        self,
        key: str,
        data: bytes,
        content_type: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> StoredObject:  # pragma: no cover - requires real AWS credentials
        safe_key = normalize_object_key(key)
        client = self._require_client()
        resolved_type = content_type or content_type_for_key(safe_key)
        client.put_object(
            Bucket=self._bucket,
            Key=safe_key,
            Body=data,
            ContentType=resolved_type,
            Metadata=metadata or {},
        )
        checksum = sha256_bytes(data)
        object_metadata = ObjectMetadata(
            key=safe_key,
            size=len(data),
            content_type=resolved_type,
            checksum_sha256=checksum,
            provider=self.name,
        )
        return StoredObject(
            key=safe_key,
            url=build_media_url(safe_key),
            metadata=object_metadata,
            created=True,
            checksum_sha256=checksum,
        )

    def get_object(self, key: str) -> bytes:  # pragma: no cover
        safe_key = normalize_object_key(key)
        client = self._require_client()
        try:
            response = client.get_object(Bucket=self._bucket, Key=safe_key)
        except client.exceptions.NoSuchKey as exc:
            raise self._not_found(safe_key) from exc
        return response["Body"].read()

    def open_object(self, key: str) -> BinaryIO:  # pragma: no cover
        safe_key = normalize_object_key(key)
        client = self._require_client()
        try:
            response = client.get_object(Bucket=self._bucket, Key=safe_key)
        except client.exceptions.NoSuchKey as exc:
            raise self._not_found(safe_key) from exc
        return response["Body"]

    def delete_object(self, key: str) -> bool:  # pragma: no cover
        safe_key = normalize_object_key(key)
        if not self.object_exists(safe_key):
            return False
        client = self._require_client()
        client.delete_object(Bucket=self._bucket, Key=safe_key)
        return True

    def object_exists(self, key: str) -> bool:  # pragma: no cover
        safe_key = normalize_object_key(key)
        client = self._require_client()
        try:
            client.head_object(Bucket=self._bucket, Key=safe_key)
            return True
        except Exception:
            return False

    def get_metadata(self, key: str) -> ObjectMetadata:  # pragma: no cover
        safe_key = normalize_object_key(key)
        client = self._require_client()
        try:
            head = client.head_object(Bucket=self._bucket, Key=safe_key)
        except Exception as exc:
            raise self._not_found(safe_key) from exc
        stream = self.open_object(safe_key)
        try:
            checksum = sha256_stream(stream)
        finally:
            stream.close()
        last_modified = head.get("LastModified")
        return ObjectMetadata(
            key=safe_key,
            size=int(head.get("ContentLength") or 0),
            content_type=head.get("ContentType") or content_type_for_key(safe_key),
            checksum_sha256=checksum,
            last_modified=last_modified.isoformat() if last_modified else None,
            etag=(head.get("ETag") or "").strip('"') or None,
            provider=self.name,
        )

    def url_for(self, key: str) -> str:  # pragma: no cover
        return build_media_url(key)

    def list_objects(self, prefix: str = "") -> Iterator[str]:  # pragma: no cover
        client = self._require_client()
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix or ""):
            for entry in page.get("Contents", []) or []:
                yield entry["Key"]

    def describe(self) -> Dict[str, Any]:
        """
        Safe description — bucket NAME and region only, never the keys.

        Credentials are never part of this payload and never logged.
        """
        from app.storage.urls import cdn_base_url

        return {
            "provider": self.name,
            "bucket": self._bucket,
            "region": self._region,
            "cdnConfigured": bool(cdn_base_url()),
        }
