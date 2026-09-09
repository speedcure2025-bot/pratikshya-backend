"""
app/services/media/migrate_local.py — local media import CLI (Phase 6).

Usage (run from `backend/`):

    # 1. ALWAYS start here — writes nothing, deletes nothing
    python -m app.services.media.migrate_local --dry-run

    # 2. real import (safe to re-run; identical objects are recognised, not rewritten)
    python -m app.services.media.migrate_local

    # 3. optional: re-verify the source assets against a recorded baseline
    python -m app.services.media.migrate_local --verify-source <baseline.sha256> --no-migrate

Options:
    --source PATH      source asset folder (default: LOCAL_MEDIA_IMPORT_SOURCE)
    --root PATH        object-store root override (default: LOCAL_MEDIA_ROOT)
    --manifest PATH    where to write the JSON manifest
                       (default: <backend>/storage/migration/local-media-migration.json)
    --limit N          process only the first N source files (smoke test)
    --verify-source F  after migrating, re-check the source against baseline F
    --no-migrate       skip the copy (use with --verify-source)
    --json             print the machine-readable report instead of the summary

The command never touches the database, never writes to the source folder,
and never deletes anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

BACKEND_DIR = Path(__file__).resolve().parents[3]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.services.media.local_media_migration import (  # noqa: E402
    MigrationReport,
    relative_source_label,
    run_migration,
    verify_migration,
    verify_source_integrity,
)
from app.storage import LocalStorageProvider, create_storage_provider  # noqa: E402

DEFAULT_MANIFEST = BACKEND_DIR / "storage" / "migration" / "local-media-migration.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.services.media.migrate_local",
        description=(
            "Copy the real product asset library into the local object store. "
            "Copy-only: the source folder is never modified or deleted."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would happen without writing or deleting anything.",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Source asset folder (default: settings.LOCAL_MEDIA_IMPORT_SOURCE).",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Object-store root override (default: settings.LOCAL_MEDIA_ROOT).",
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="Where to write the JSON migration manifest.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N source files (smoke test).",
    )
    parser.add_argument(
        "--verify-source",
        default=None,
        metavar="BASELINE",
        help="sha256sum baseline file to re-verify the source assets against.",
    )
    parser.add_argument(
        "--no-migrate",
        action="store_true",
        help="Skip the copy step (use with --verify-source).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the machine-readable report instead of the summary.",
    )
    return parser


def resolve_source(explicit: Optional[str]) -> Path:
    if explicit:
        candidate = Path(explicit).expanduser()
    else:
        candidate = settings.local_media_import_source_path
    if candidate is None:
        raise SystemExit("No source folder configured (LOCAL_MEDIA_IMPORT_SOURCE).")
    if not candidate.is_absolute():
        candidate = (BACKEND_DIR / candidate).resolve()
    return candidate.resolve()


def resolve_provider(explicit_root: Optional[str]) -> LocalStorageProvider:
    if not explicit_root:
        return create_storage_provider()
    root = Path(explicit_root).expanduser()
    if not root.is_absolute():
        root = (BACKEND_DIR / root).resolve()
    return LocalStorageProvider(root)


def print_summary(report: MigrationReport, source: Path, storage_root: Path) -> None:
    counts = report.counts
    verb = "would copy" if report.dry_run else "copied"
    line = "-" * 68
    print(line)
    print("PRATIKSHYA FASHON — local media import" + ("  [DRY RUN]" if report.dry_run else ""))
    print(line)
    print(f"source            : {relative_source_label(source)}")
    print(f"provider          : {report.provider}")
    print(f"storage root      : {storage_root.name}/  (see LOCAL_MEDIA_ROOT)")
    print(f"mode              : {'dry run — nothing written' if report.dry_run else 'copy'}")
    print(line)
    print(f"total source files: {counts['total_source_files']}")
    print(f"{verb:<18}: {counts['copied'] if not report.dry_run else counts['planned']}")
    print(f"already identical : {counts['already_identical']}")
    print(f"collision         : {counts['collision']}")
    print(f"checksum mismatch : {counts['checksum_mismatch']}")
    print(f"unsupported       : {counts['unsupported']}")
    print(f"invalid           : {counts['invalid']}")
    print(f"failed            : {counts['failed']}")
    print(f"skipped           : {counts['skipped']}")
    print(f"source bytes      : {counts['source_bytes']:,}")
    print(line)
    if counts["extension_mismatch"]:
        print(
            f"NOTE: {counts['extension_mismatch']} source file(s) carry an extension "
            "that does not match their bytes (e.g. a .avif name holding JPEG/PNG data)."
        )
        print(
            "      They are copied unchanged — the source is never renamed or converted —"
        )
        print(
            "      and the object store serves the sniffed Content-Type. See the manifest."
        )
        print(line)

    non_planned = [
        entry
        for entry in report.entries
        if entry.status not in ("PLANNED", "COPIED", "ALREADY_IDENTICAL")
    ]
    if non_planned:
        print("Entries needing attention:")
        for entry in non_planned[:25]:
            print(f"  [{entry.status}] {entry.source} — {entry.detail}")
        if len(non_planned) > 25:
            print(f"  … and {len(non_planned) - 25} more (see the manifest)")
        print(line)

    if report.errors:
        print(f"errors: {len(report.errors)}")
        for message in report.errors[:10]:
            print(f"  ! {message}")
        print(line)

    print(f"checksum verification: {report.summary()['checksumVerification']}")


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)

    source = resolve_source(args.source)
    storage = resolve_provider(args.root)
    storage_root = getattr(storage, "root", None)

    exit_code = 0
    report: Optional[MigrationReport] = None

    if not args.no_migrate:
        if not source.exists():
            print(f"ERROR: source folder not found: {source}", file=sys.stderr)
            return 2

        report = run_migration(source, storage, dry_run=args.dry_run, limit=args.limit)

        manifest_path = Path(args.manifest).expanduser()
        if not manifest_path.is_absolute():
            manifest_path = (BACKEND_DIR / manifest_path).resolve()
        report.write_manifest(manifest_path)

        if args.as_json:
            print(json.dumps(report.as_dict(), indent=2))
        else:
            print_summary(report, source, storage_root)
            print(f"manifest written  : {manifest_path.name} (under storage/migration/)")

        counts = report.counts
        if counts["failed"] or counts["checksum_mismatch"]:
            exit_code = 1

        if not args.dry_run and not counts["failed"]:
            verification = verify_migration(source, storage, report)
            print(
                "re-verification   : "
                + (
                    f"passed ({verification['checked']} objects re-hashed)"
                    if verification["ok"]
                    else f"FAILED {verification}"
                )
            )
            if not verification["ok"]:
                exit_code = 1

    if args.verify_source:
        baseline = Path(args.verify_source).expanduser()
        if not baseline.is_absolute():
            baseline = (BACKEND_DIR / baseline).resolve()
        if not baseline.exists():
            print(f"ERROR: baseline file not found: {baseline}", file=sys.stderr)
            return 2
        result = verify_source_integrity(source, baseline)
        print("-" * 68)
        print("SOURCE INTEGRITY CHECK (frontend/public/images must be untouched)")
        print(f"  baseline files : {result['baselineFileCount']}")
        print(f"  current files  : {result['currentFileCount']}")
        print(f"  byte-identical : {result['identical']}")
        print(f"  missing        : {len(result['missing'])}")
        print(f"  added          : {len(result['added'])}")
        print(f"  changed        : {len(result['changed'])}")
        print(f"  result         : {'PASS — source untouched' if result['ok'] else 'FAIL'}")
        if not result["ok"]:
            for key in (result["missing"] + result["added"] + result["changed"])[:20]:
                print(f"    ! {key}")
            exit_code = 1

    return exit_code


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
