"""
app/services/media/local_media_migration.py — safe local media import (Phase 6).

Copies the real product asset library into the local object store. It is a
COPY operation: the source folder is opened read-only and is never written,
renamed, moved, compressed, resized or deleted.

    frontend/public/images   →   {LOCAL_MEDIA_ROOT}

Why it is safe
--------------
  · source files are opened `"rb"` only; there is no write path to the source
  · every object is written through `StorageProvider.put_object`, which is
    atomic (temp file + `os.replace`), so an interrupted run cannot leave a
    half-written object that later passes a checksum compare
  · an existing object with identical bytes is reported `ALREADY_IDENTICAL`
    and NOT rewritten — re-running is a no-op
  · an existing object with DIFFERENT bytes is reported `COLLISION` and left
    untouched — nothing is ever silently overwritten
  · SHA-256 is computed on the source and again on the written destination;
    a mismatch is a `CHECKSUM_MISMATCH` failure and the run continues
  · one failing file can never affect another: each file is processed inside
    its own try/except and every outcome is recorded

Nothing here touches the database. No product row is modified, because
mapping migrated objects onto product records is only legitimate once the
media tables carry real columns (see §19 of the phase report).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config import settings
from app.core.logging import get_logger
from app.services.media.media_validation import (
    allowed_image_extensions,
    extension_of,
    max_image_bytes,
    mime_for_extension,
)
from app.storage.signatures import sniff_content_type
from app.storage import StorageProvider
from app.storage.base import InvalidObjectKeyError
from app.storage.keys import normalize_object_key
from app.storage.local import _HASH_CHUNK_SIZE

logger = get_logger("app.services.media.migration")

# ---------------------------------------------------------------------------
# Statuses — every migrated file gets exactly one
# ---------------------------------------------------------------------------

PLANNED = "PLANNED"                     # dry run: would be copied
COPIED = "COPIED"                       # written and checksum-verified
ALREADY_IDENTICAL = "ALREADY_IDENTICAL" # destination matches source bytes
COLLISION = "COLLISION"                 # destination exists with OTHER bytes
CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH" # written bytes differ from source
UNSUPPORTED = "UNSUPPORTED"             # not an allowed image type
INVALID = "INVALID"                     # no legal object key / empty file
FAILED = "FAILED"                       # unexpected I/O failure
SKIPPED = "SKIPPED"                     # excluded by the caller (--limit …)

ALL_STATUSES = (
    PLANNED,
    COPIED,
    ALREADY_IDENTICAL,
    COLLISION,
    CHECKSUM_MISMATCH,
    UNSUPPORTED,
    INVALID,
    FAILED,
    SKIPPED,
)

#: Keys in the summary counts block, in report order.
SUMMARY_KEYS = (
    "total_source_files",
    "copied",
    "already_identical",
    "planned",
    "collision",
    "checksum_mismatch",
    "unsupported",
    "invalid",
    "failed",
    "skipped",
    # Informational: files whose extension does not match their real bytes.
    # These are still migrated — the source is never renamed or converted —
    # but the object store serves the sniffed type and the report names them.
    "extension_mismatch",
    "source_bytes",
    "destination_bytes",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_of_file(path: Path) -> str:
    """Streamed SHA-256 of a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass
class MigrationEntry:
    """One source file and what happened to it."""

    source: str
    object_key: str = ""
    size: int = 0
    content_type: str = ""
    sha256_source: str = ""
    sha256_destination: str = ""
    status: str = PLANNED
    detail: str = ""
    #: Set when the file's extension claims a different type than its bytes.
    extension_mismatch: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "objectKey": self.object_key,
            "size": self.size,
            "mimeType": self.content_type,
            "sha256Source": self.sha256_source,
            "sha256Destination": self.sha256_destination,
            "status": self.status,
            "extensionMismatch": self.extension_mismatch,
            "detail": self.detail,
        }


@dataclass
class MigrationReport:
    """Full outcome of one migration run."""

    dry_run: bool
    source_root: str
    provider: str
    started_at: str
    finished_at: str = ""
    entries: List[MigrationEntry] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def counts(self) -> Dict[str, int]:
        tallies = {
            "copied": 0,
            "already_identical": 0,
            "planned": 0,
            "collision": 0,
            "checksum_mismatch": 0,
            "unsupported": 0,
            "invalid": 0,
            "failed": 0,
            "skipped": 0,
        }
        status_to_key = {
            COPIED: "copied",
            ALREADY_IDENTICAL: "already_identical",
            PLANNED: "planned",
            COLLISION: "collision",
            CHECKSUM_MISMATCH: "checksum_mismatch",
            UNSUPPORTED: "unsupported",
            INVALID: "invalid",
            FAILED: "failed",
            SKIPPED: "skipped",
        }
        for entry in self.entries:
            key = status_to_key.get(entry.status)
            if key:
                tallies[key] += 1
        tallies["total_source_files"] = len(self.entries)
        tallies["extension_mismatch"] = sum(1 for e in self.entries if e.extension_mismatch)
        tallies["source_bytes"] = sum(e.size for e in self.entries)
        tallies["destination_bytes"] = sum(
            e.size for e in self.entries if e.status in (COPIED, ALREADY_IDENTICAL, PLANNED)
        )
        return tallies

    def summary(self) -> Dict[str, Any]:
        counts = self.counts
        ordered = {key: counts.get(key, 0) for key in SUMMARY_KEYS}
        return {
            "dryRun": self.dry_run,
            "sourceRoot": self.source_root,
            "provider": self.provider,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "counts": ordered,
            "errors": list(self.errors),
            "checksumVerification": (
                "not-run (dry run)"
                if self.dry_run
                else "passed"
                if not counts["checksum_mismatch"] and not counts["failed"]
                else "FAILED"
            ),
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": "pratikshya.media.migration.v1",
            "summary": self.summary(),
            "entries": [entry.as_dict() for entry in self.entries],
        }

    def write_manifest(self, path: Path) -> Path:
        """Write the JSON manifest. Contains no secrets and no absolute roots."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.as_dict(), indent=2, sort_keys=False), encoding="utf-8"
        )
        return target


# ---------------------------------------------------------------------------
# Source discovery
# ---------------------------------------------------------------------------

def discover_source_files(source_root: Path) -> List[Path]:
    """Every regular file under `source_root`, in a stable sorted order."""
    root = Path(source_root)
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file())


def relative_source_path(source_root: Path, path: Path) -> str:
    """POSIX-style path of `path` relative to the source root."""
    return str(Path(path).relative_to(source_root)).replace("\\", "/")


def object_key_for_source_file(source_root: Path, path: Path) -> str:
    """
    Object key for one source file.

    The key is the asset's own path below the source root, which keeps it
    deterministic, collision-free, S3-portable and — critically — makes the
    legacy `/images/<key>` reference resolve onto the migrated object without
    any table lookup.
    """
    return normalize_object_key(relative_source_path(source_root, path))


def classify_source_file(
    path: Path,
    *,
    allowed_extensions: Optional[Iterable[str]] = None,
    allowed_mime_types: Optional[Iterable[str]] = None,
    max_bytes: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    Policy check for one source file. Returns `(ok, reason)`.

    Reuses the same house policy as uploads (extension + content signature +
    size) so the migration cannot quietly import something an upload would
    reject.
    """
    extensions = {item.lower() for item in (allowed_extensions or allowed_image_extensions())}
    mime_types = {item.lower() for item in (allowed_mime_types or settings.allowed_image_types)}
    ceiling = max_bytes if max_bytes is not None else max_image_bytes()

    try:
        size = path.stat().st_size
    except OSError as exc:
        return False, f"cannot stat: {exc.__class__.__name__}"

    if size == 0:
        return False, "file is empty"
    if size > ceiling:
        return False, f"file is {size / (1024 * 1024):.1f} MB, above the size ceiling"

    ext = extension_of(path.name)
    if ext not in extensions:
        return False, f"extension '{ext or '(none)'}' is not an allowed image type"

    with open(path, "rb") as handle:
        head = handle.read(64)
    sniffed = sniff_content_type(head)
    if sniffed is None:
        return False, "content does not match a supported image signature"
    if sniffed not in mime_types:
        return False, f"detected '{sniffed}' is not an allowed image type"

    # A disagreement between the extension and the bytes is NOT a reason to
    # refuse the asset: the bytes are a real, renderable image and the source
    # must not be renamed or converted. The sniffed type is authoritative for
    # the stored/served Content-Type, and the disagreement is recorded.
    return True, sniffed


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def run_migration(
    source_root: Path | str,
    storage: StorageProvider,
    *,
    dry_run: bool = False,
    limit: Optional[int] = None,
    allowed_extensions: Optional[Iterable[str]] = None,
    allowed_mime_types: Optional[Iterable[str]] = None,
    max_bytes: Optional[int] = None,
    progress: Optional[Any] = None,
) -> MigrationReport:
    """
    Copy the source asset library into `storage`.

    Never writes when `dry_run` is True. Never deletes or modifies a source
    file in either mode.
    """
    root = Path(source_root).expanduser().resolve()
    started = _now_iso()
    report = MigrationReport(
        dry_run=dry_run,
        source_root=relative_source_label(root),
        provider=getattr(storage, "name", "unknown"),
        started_at=started,
    )

    if not root.exists():
        report.errors.append(f"Source folder does not exist: {root.name}")
        report.finished_at = _now_iso()
        return report

    files = discover_source_files(root)
    for index, path in enumerate(files):
        relative = relative_source_path(root, path)
        entry = MigrationEntry(source=relative)
        report.entries.append(entry)

        if limit is not None and index >= limit:
            entry.status = SKIPPED
            entry.detail = f"excluded by --limit {limit}"
            continue

        try:
            ok, reason = classify_source_file(
                path,
                allowed_extensions=allowed_extensions,
                allowed_mime_types=allowed_mime_types,
                max_bytes=max_bytes,
            )
            entry.size = path.stat().st_size
            if not ok:
                entry.status = UNSUPPORTED if "extension" in reason or "image" in reason else INVALID
                entry.detail = reason
                continue

            entry.content_type = reason
            claimed = mime_for_extension(path.name) or ""
            if claimed and claimed != entry.content_type:
                entry.extension_mismatch = f"{claimed}->{entry.content_type}"
                entry.detail = (
                    f"extension claims {claimed} but the bytes are "
                    f"{entry.content_type}; stored and served as {entry.content_type}"
                )
            entry.object_key = object_key_for_source_file(root, path)
            entry.sha256_source = sha256_of_file(path)

            if dry_run:
                entry.status = PLANNED
                entry.detail = (
                    f"{entry.detail}; would be copied" if entry.detail else "would be copied"
                )
                continue

            if storage.object_exists(entry.object_key):
                existing = storage.get_metadata(entry.object_key)
                entry.sha256_destination = existing.checksum_sha256
                if existing.checksum_sha256 == entry.sha256_source:
                    entry.status = ALREADY_IDENTICAL
                    entry.detail = (
                        f"{entry.detail}; destination already holds identical bytes"
                        if entry.detail
                        else "destination already holds identical bytes"
                    )
                else:
                    entry.status = COLLISION
                    entry.detail = (
                        "destination object differs from source; existing object "
                        "left untouched (no silent overwrite)"
                    )
                continue

            with open(path, "rb") as handle:
                payload = handle.read()
            stored = storage.put_object(
                entry.object_key, payload, content_type=entry.content_type
            )
            entry.sha256_destination = storage.get_metadata(stored.key).checksum_sha256
            if entry.sha256_destination != entry.sha256_source:
                entry.status = CHECKSUM_MISMATCH
                entry.detail = (
                    "destination SHA-256 does not match source after write"
                )
                report.errors.append(f"{relative}: checksum mismatch after write")
                continue

            entry.status = COPIED
            entry.detail = (
                f"{entry.detail}; copied and checksum-verified"
                if entry.detail
                else "copied and checksum-verified"
            )
        except InvalidObjectKeyError as exc:
            entry.status = INVALID
            entry.detail = f"object key rejected: {exc}"
            report.errors.append(f"{relative}: {entry.detail}")
        except Exception as exc:  # noqa: BLE001 — one file must never stop the run
            entry.status = FAILED
            entry.detail = f"{exc.__class__.__name__}: {exc}"
            report.errors.append(f"{relative}: {entry.detail}")
            logger.warning("Migration failed for %s: %s", relative, exc)

        if progress is not None:
            progress(index + 1, len(files), entry)

    report.finished_at = _now_iso()
    return report


def relative_source_label(root: Path) -> str:
    """
    A non-machine-specific label for the source root, for reports.

    The absolute path is deliberately not written into the manifest.
    """
    parts = root.parts
    if "frontend" in parts:
        index = parts.index("frontend")
        return "/".join(parts[index:])
    return root.name


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_source_integrity(
    source_root: Path | str,
    baseline: Path | str,
) -> Dict[str, Any]:
    """
    Re-check the source folder against a recorded `sha256sum` baseline.

    Baseline format is the standard `sha256sum` output: `<hash>  <path>`.
    Returns counts and the exact list of differences. Read-only.
    """
    root = Path(source_root).expanduser().resolve()
    baseline_path = Path(baseline).expanduser()

    raw_expected: Dict[str, str] = {}
    for line in baseline_path.read_text(encoding="utf-8").splitlines():
        line = line.rstrip("\n")
        if not line.strip():
            continue
        digest, sep, path = line.partition("  ")
        if not sep:
            digest, sep, path = line.partition(" *")
        raw_expected[path.strip().lstrip("*").strip()] = digest.strip()

    actual: Dict[str, str] = {}
    for path in discover_source_files(root):
        actual[relative_source_path(root, path)] = sha256_of_file(path)

    # Baseline paths are stored relative to the source root, but a baseline
    # recorded from a different working directory may carry a prefix
    # (`frontend/public/images/...`). Match on the longest suffix so the
    # comparison is about the ASSET, not about how the recorder was invoked.
    expected: Dict[str, str] = {}
    unmatched: List[str] = []
    for key, digest in raw_expected.items():
        resolved = _match_baseline_path(key, set(actual))
        if resolved is None:
            unmatched.append(key)
        else:
            expected[resolved] = digest

    missing = sorted(set(expected) - set(actual)) + sorted(unmatched)
    added = sorted(set(actual) - set(expected))
    changed = sorted(
        key for key in set(expected) & set(actual) if expected[key] != actual[key]
    )
    return {
        "ok": not (missing or added or changed),
        "baselineFileCount": len(expected),
        "currentFileCount": len(actual),
        "identical": len(set(expected) & set(actual)) - len(changed),
        "missing": missing,
        "added": added,
        "changed": changed,
    }


def _match_baseline_path(key: str, actual_keys: set) -> Optional[str]:
    """Resolve a baseline path onto a source-relative path, or None."""
    normalized = str(key).replace("\\", "/").lstrip("./")
    if normalized in actual_keys:
        return normalized
    parts = normalized.split("/")
    for index in range(1, len(parts)):
        candidate = "/".join(parts[index:])
        if candidate in actual_keys:
            return candidate
    return None


def verify_migration(
    source_root: Path | str,
    storage: StorageProvider,
    report: MigrationReport,
) -> Dict[str, Any]:
    """
    Re-hash the destination objects for everything the run claims to have
    copied, and confirm they still match the source.
    """
    root = Path(source_root).expanduser().resolve()
    checked = 0
    mismatched: List[str] = []
    missing: List[str] = []
    source_changed: List[str] = []

    for entry in report.entries:
        if entry.status not in (COPIED, ALREADY_IDENTICAL):
            continue
        if not storage.object_exists(entry.object_key):
            missing.append(entry.object_key)
            continue
        current = storage.get_metadata(entry.object_key).checksum_sha256
        if current != entry.sha256_source:
            mismatched.append(entry.object_key)
        # Re-hash the SOURCE too: a drift here means the asset library moved
        # under us, which must be reported rather than assumed stable.
        source_path = root / entry.source
        if source_path.is_file() and sha256_of_file(source_path) != entry.sha256_source:
            source_changed.append(entry.source)
        checked += 1

    return {
        "ok": not mismatched and not missing and not source_changed,
        "checked": checked,
        "mismatched": mismatched,
        "missing": missing,
        "sourceChanged": source_changed,
    }


__all__ = [
    "PLANNED",
    "COPIED",
    "ALREADY_IDENTICAL",
    "COLLISION",
    "CHECKSUM_MISMATCH",
    "UNSUPPORTED",
    "INVALID",
    "FAILED",
    "SKIPPED",
    "ALL_STATUSES",
    "MigrationEntry",
    "MigrationReport",
    "discover_source_files",
    "object_key_for_source_file",
    "classify_source_file",
    "run_migration",
    "verify_source_integrity",
    "verify_migration",
    "sha256_of_file",
]
