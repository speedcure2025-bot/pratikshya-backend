"""
Phase 6 FIX — supported image formats for the existing media pipeline.

The real product asset library (frontend/public/images) is AVIF-first: 228 of
the 238 shipped assets carry a `.avif` name (many of them holding legacy
JPEG/PNG bytes), the remaining 10 are `.webp`. If `.avif` / `.webp` are absent
from the allowed image type configuration, the migration rejects every single
one of those assets as `UNSUPPORTED`.

These focused tests lock the fix at the level of the EXISTING architecture —
one policy, every surface that must agree with it:

  1–5  .avif, .webp, .jpg, .jpeg, .png are accepted (upload validation AND
         migration classification share the same code path)
  6    anything else (gif, zip, svg, txt, mp4, no extension) stays rejected —
         nothing is bypassed, the policy remains configuration-driven
  7    content validation is still enforced: bytes are authoritative, a
         filename never grants entry; the sniffed type is what gets stored
         and served
  8    migration behaviour remains copy-only: dry runs write nothing, real
         runs never rename, convert, overwrite or delete a source file; a
         `.avif` name holding JPEG/PNG bytes is PRESERVED byte-for-byte and
         the report records the detected content type
  9    the real 238 source assets remain byte-identical, and a dry run
         against the real tree reports zero unsupported entries

The admin settings surface (`app.api.v1.admin.SETTINGS_DEFAULTS`) is also
asserted to be derived from the same policy, so the settings desk can never
again advertise a list that disagrees with what the backend enforces.

Style follows the Phase 1–6 suites: no live server, no PostgreSQL, no AWS.
Synthetic fixtures carry real magic bytes; the real asset tree is only ever
OPENED FOR READING.
"""

import hashlib
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Settings, settings
from app.services.media.local_media_migration import (
    COPIED,
    PLANNED,
    UNSUPPORTED,
    classify_source_file,
    discover_source_files,
    run_migration,
)
from app.services.media.media_validation import (
    MediaValidationError,
    allowed_image_extensions,
    allowed_image_mime_types,
    validate_image_bytes,
)
from app.storage.signatures import sniff_content_type

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_SOURCE_DIR = REPO_ROOT / "frontend" / "public" / "images"

#: The exact house image policy this phase must support — no more, no less.
SUPPORTED_IMAGE_EXTENSIONS = {".avif", ".jpeg", ".jpg", ".png", ".webp"}
SUPPORTED_IMAGE_MIME_TYPES = {"image/avif", "image/jpeg", "image/png", "image/webp"}

# ---------------------------------------------------------------------------
# Fixture bytes — real content signatures, built in memory.
# ---------------------------------------------------------------------------

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
JPEG_BYTES = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00"
    + b"\x00" * 64
    + b"\xff\xd9"
)
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
AVIF_BYTES = (
    b"\x00\x00\x00 ftypavif" + b"\x00" * 8 + b"avifmif1miaf" + b"\x00" * 48
)
HEIF_BRANDED_AVIF_BYTES = (
    b"\x00\x00\x00 ftypmif1" + b"\x00" * 8 + b"mif1avifmiaf" + b"\x00" * 48
)
GIF_BYTES = b"GIF89a\x01\x00\x01\x00\x80\x00\x00" + b"\x00" * 48
MP4_BYTES = b"\x00\x00\x00 ftypisom" + b"\x00" * 8 + b"isomiso2avc1mp41" + b"\x00" * 32
WAVE_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32  # RIFF, but not WEBP
ZIP_BYTES = b"PK\x03\x04this is a zip file, definitely not an image" + b"\x00" * 32


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ===========================================================================
# 1. The configuration itself
# ===========================================================================

class ImagePolicyConfigurationTests(unittest.TestCase):
    """The single allowed-image-types configuration covers all five formats."""

    def test_mime_policy_lists_the_supported_formats(self):
        # Five extensions map onto four MIME types (jpg + jpeg are one type).
        self.assertEqual(set(allowed_image_mime_types()), SUPPORTED_IMAGE_MIME_TYPES)

    def test_extension_policy_is_exactly_the_five_supported_formats(self):
        # Derived from the same configuration the upload validator uses —
        # `.avif` and `.webp` included; nothing else sneaks in (no `.gif`).
        self.assertEqual(set(allowed_image_extensions()), SUPPORTED_IMAGE_EXTENSIONS)

    def test_defaults_support_all_five_formats_without_an_env_file(self):
        # A development checkout must accept AVIF out of the box: this is the
        # configuration DEFAULT, not an environment override.
        fresh = Settings(_env_file=None)
        self.assertEqual(set(fresh.allowed_image_types), SUPPORTED_IMAGE_MIME_TYPES)

    def test_admin_settings_surface_cannot_drift_from_the_enforced_policy(self):
        # The settings desk used to hardcode ["jpg","jpeg","png","webp"] and
        # silently disagreed with the backend policy (avif missing). It is
        # derived from the house policy now.
        from app.api.v1.admin import SETTINGS_DEFAULTS

        advertised = SETTINGS_DEFAULTS["media"]["allowedImageTypes"]
        self.assertEqual(
            {f".{name}" for name in advertised},
            set(allowed_image_extensions()),
        )
        self.assertIn("avif", advertised)
        self.assertIn("webp", advertised)


# ===========================================================================
# 2. Upload validation — acceptance (requirements 1–5) and rejection (6)
# ===========================================================================

class UploadFormatAcceptanceTests(unittest.TestCase):
    """Every supported format passes the house policy on its real signature."""

    CASES = (
        ("legacy-photo.avif", AVIF_BYTES, "image/avif"),
        ("product-front.webp", WEBP_BYTES, "image/webp"),
        ("sherwani.jpg", JPEG_BYTES, "image/jpeg"),
        ("sherwani.jpeg", JPEG_BYTES, "image/jpeg"),
        ("logo-plate.png", PNG_BYTES, "image/png"),
        ("upper-case.AVIF", AVIF_BYTES, "image/avif"),  # extension case-insensitive
    )

    def test_supported_formats_are_accepted(self):
        for name, blob, expected in self.CASES:
            with self.subTest(name=name):
                result = validate_image_bytes(name, blob)
                self.assertEqual(result.content_type, expected)
                self.assertFalse(result.extension_mismatch)
                self.assertEqual(result.checksum_sha256, sha256(blob))
                self.assertEqual(result.size, len(blob))

    def test_heif_family_brand_is_recognised_as_avif(self):
        # ISO-BMFF still-image brands resolve to image/avif — the same
        # signature table the provider uses to report Content-Type.
        self.assertEqual(sniff_content_type(HEIF_BRANDED_AVIF_BYTES), "image/avif")
        result = validate_image_bytes("plate.avif", HEIF_BRANDED_AVIF_BYTES)
        self.assertEqual(result.content_type, "image/avif")


class UploadFormatRejectionTests(unittest.TestCase):
    """Widening the list did NOT weaken the policy — everything else stays out."""

    def test_gif_is_still_rejected(self):
        # A perfectly valid GIF is still an invalid upload: the point of the
        # fix is to support five formats, not to accept all formats.
        with self.assertRaises(MediaValidationError):
            validate_image_bytes("animation.gif", GIF_BYTES)

    def test_arbitrary_extensions_are_rejected(self):
        for name in ("archive.zip", "diagram.svg", "notes.txt", "installer.exe", "clip.mp4"):
            with self.subTest(name=name):
                with self.assertRaises(MediaValidationError):
                    validate_image_bytes(name, ZIP_BYTES)

    def test_missing_extension_is_rejected(self):
        with self.assertRaises(MediaValidationError):
            validate_image_bytes("noextension", PNG_BYTES)

    def test_video_container_named_avif_is_rejected_by_its_bytes(self):
        # An MP4 renamed `.avif` must not enter an image store — the bytes
        # are checked, and `video/mp4` is not an allowed IMAGE type.
        with self.assertRaises(MediaValidationError):
            validate_image_bytes("sneaky.avif", MP4_BYTES)


# ===========================================================================
# 3. Content validation is still enforced (requirement 7)
# ===========================================================================

class ContentValidationStillEnforcedTests(unittest.TestCase):
    """The fix touches WHICH MIME TYPES are allowed — never WHETHER the
    content is checked. Filenames remain untrusted."""

    def test_garbage_named_with_a_supported_extension_is_rejected(self):
        for name in ("lie.png", "lie.jpg", "lie.jpeg", "lie.webp", "lie.avif"):
            with self.subTest(name=name):
                with self.assertRaises(MediaValidationError):
                    validate_image_bytes(name, ZIP_BYTES)

    def test_riff_wave_is_not_mistaken_for_webp(self):
        # The sniffer must confirm the WEBP fourcc, not just the RIFF magic.
        self.assertIsNone(sniff_content_type(WAVE_BYTES))
        with self.assertRaises(MediaValidationError):
            validate_image_bytes("audio.webp", WAVE_BYTES)

    def test_empty_and_oversize_rejections_are_untouched(self):
        with self.assertRaises(MediaValidationError):
            validate_image_bytes("empty.avif", b"")
        with patch.object(settings, "MAX_IMAGE_SIZE_MB", 0):
            with self.assertRaises(MediaValidationError):
                validate_image_bytes("big.avif", AVIF_BYTES)

    def test_avif_name_holding_legacy_jpeg_is_accepted_and_reported_not_rewritten(self):
        # Exactly the situation inside frontend/public/images. The bytes are
        # a real, renderable image, so the asset is admitted — stored and
        # served as what it truly is, with the mismatch REPORTED. Nothing
        # here renames or converts the file; the original bytes are the
        # contract, and the migration test below proves they survive.
        result = validate_image_bytes("legacy.avif", JPEG_BYTES)
        self.assertEqual(result.content_type, "image/jpeg")
        self.assertTrue(result.extension_mismatch)
        self.assertEqual(result.declared_extension_type, "image/avif")
        result = validate_image_bytes("legacy.avif", PNG_BYTES)
        self.assertEqual(result.content_type, "image/png")
        self.assertTrue(result.extension_mismatch)

    def test_disabling_a_format_via_configuration_still_works(self):
        # The policy remains configuration-driven — the fix added formats to
        # the existing knob, it did not hardcode a bypass.
        with patch.object(settings, "ALLOWED_IMAGE_TYPES", "image/png,image/avif"):
            self.assertEqual(set(allowed_image_extensions()), {".png", ".avif"})
            with self.assertRaises(MediaValidationError):
                validate_image_bytes("a.webp", WEBP_BYTES)
            self.assertEqual(
                validate_image_bytes("a.avif", AVIF_BYTES).content_type, "image/avif"
            )


# ===========================================================================
# 4. Migration classification + copy-only guarantee (requirement 8)
# ===========================================================================

class MigrationFormatTests(unittest.TestCase):
    """The importer reuses the SAME policy; its behaviour stays copy-only."""

    def setUp(self):
        from app.storage import LocalStorageProvider

        self._tmp = tempfile.TemporaryDirectory(prefix="pf6fix-")
        self.base = Path(self._tmp.name)
        self.source = self.base / "source"
        self.store_root = self.base / "store"
        self.provider = LocalStorageProvider(self.store_root)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, relative: str, data: bytes) -> Path:
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def _source_snapshot(self):
        return {
            str(p.relative_to(self.source)).replace("\\", "/"): (
                p.stat().st_size,
                sha256(p.read_bytes()),
            )
            for p in discover_source_files(self.source)
        }

    def _build_tree(self):
        # All five supported formats, including the legacy pattern from the
        # real library: a `.avif` name holding JPEG (and PNG) bytes.
        self._write("products/P-1/primary.avif", AVIF_BYTES)
        self._write("products/P-1/legacy-avif-name.jpg-bytes.avif", JPEG_BYTES)
        self._write("products/P-1/legacy-avif-name.png-bytes.avif", PNG_BYTES)
        self._write("products/P-1/front.webp", WEBP_BYTES)
        self._write("products/P-1/a.jpg", JPEG_BYTES)
        self._write("products/P-1/b.jpeg", JPEG_BYTES)
        self._write("hero/c.png", PNG_BYTES)
        # Not part of the image policy — must stay rejected, not smuggled in.
        self._write("products/P-1/animation.gif", GIF_BYTES)
        self._write("products/P-1/archive.avif", ZIP_BYTES)
        self._write("products/P-1/clip.mp4", MP4_BYTES)

    def test_classify_source_file_accepts_the_five_formats_with_real_bytes(self):
        self._build_tree()
        for relative, expected in (
            ("products/P-1/primary.avif", "image/avif"),
            ("products/P-1/front.webp", "image/webp"),
            ("products/P-1/a.jpg", "image/jpeg"),
            ("products/P-1/b.jpeg", "image/jpeg"),
            ("hero/c.png", "image/png"),
        ):
            with self.subTest(relative=relative):
                ok, reason = classify_source_file(self.source / relative)
                self.assertTrue(ok, reason)
                self.assertEqual(reason, expected)

    def test_classify_source_file_reports_detected_type_for_legacy_names(self):
        self._build_tree()
        # The `.avif`-named JPEG/PNG files are NOT rejected and NOT "fixed":
        # the migration reports the DETECTED type and preserves the bytes.
        for relative, detected in (
            ("products/P-1/legacy-avif-name.jpg-bytes.avif", "image/jpeg"),
            ("products/P-1/legacy-avif-name.png-bytes.avif", "image/png"),
        ):
            with self.subTest(relative=relative):
                ok, reason = classify_source_file(self.source / relative)
                self.assertTrue(ok, reason)
                self.assertEqual(reason, detected)

    def test_classify_source_file_still_rejects_everything_outside_the_policy(self):
        self._build_tree()
        for relative in (
            "products/P-1/animation.gif",  # .gif extension: not an image type
            "products/P-1/archive.avif",  # extension allowed, bytes are not an image
            "products/P-1/clip.mp4",  # valid video container, not an image policy
        ):
            with self.subTest(relative=relative):
                ok, reason = classify_source_file(self.source / relative)
                self.assertFalse(ok)
                self.assertTrue(reason, "rejections must carry a human-safe reason")

    def test_migration_reports_all_supported_formats_as_would_copy(self):
        self._build_tree()
        report = run_migration(self.source, self.provider, dry_run=True)
        counts = report.counts
        self.assertEqual(counts["total_source_files"], 10)
        self.assertEqual(counts["planned"], 7)  # 5 formats + 2 legacy-named
        self.assertEqual(counts["unsupported"], 3)
        self.assertEqual(counts["invalid"], 0)
        self.assertEqual(counts["failed"], 0)
        self.assertEqual(counts["collision"], 0)
        self.assertEqual(counts["checksum_mismatch"], 0)

        # No file with a SUPPORTED extension is rejected for its NAME — the
        # three rejections are the deliberately-out-of-policy fixtures (two
        # by extension, one by content despite the `.avif` name).
        for entry in report.entries:
            suffix = Path(entry.source).suffix.lower()
            if entry.status == UNSUPPORTED and suffix in SUPPORTED_IMAGE_EXTENSIONS:
                self.assertFalse(
                    entry.detail.startswith("extension"),
                    f"{entry.source}: supported extension rejected by name — {entry.detail}",
                )
        # Every accepted entry is PLANNED, and nothing else is reported.
        self.assertTrue(
            all(e.status in (PLANNED, UNSUPPORTED) for e in report.entries)
        )

        # The dry run wrote nothing to the object store.
        self.assertEqual(list(self.provider.list_objects()), [])
        self.assertEqual([p for p in self.store_root.rglob("*") if p.is_file()], [])

    def test_migration_is_copy_only_and_preserves_legacy_bytes_exactly(self):
        self._build_tree()
        before = self._source_snapshot()
        report = run_migration(self.source, self.provider)

        counts = report.counts
        self.assertEqual(counts["copied"], 7)
        self.assertEqual(counts["unsupported"], 3)
        self.assertEqual(counts["failed"], 0)
        self.assertEqual(counts["checksum_mismatch"], 0)

        # The source folder is untouched: same files, same sizes, same bytes.
        self.assertEqual(self._source_snapshot(), before)

        # The legacy `.avif`-named JPEG was copied BYTE-FOR-BYTE under its
        # original name (no rename, no conversion) and is served as JPEG.
        legacy_key = "products/P-1/legacy-avif-name.jpg-bytes.avif"
        self.assertEqual(self.provider.get_object(legacy_key), JPEG_BYTES)
        metadata = self.provider.get_metadata(legacy_key)
        self.assertEqual(metadata.content_type, "image/jpeg")
        legacy_entry = [e for e in report.entries if e.source == legacy_key][0]
        self.assertEqual(legacy_entry.status, COPIED)
        self.assertEqual(legacy_entry.extension_mismatch, "image/avif->image/jpeg")
        self.assertEqual(legacy_entry.sha256_source, legacy_entry.sha256_destination)
        self.assertEqual(legacy_entry.sha256_source, sha256(JPEG_BYTES))

        # Rejected files were never written to the object store; accepted
        # files copied, rejected files recorded — nothing in between.
        stored = set(self.provider.list_objects())
        self.assertNotIn("products/P-1/animation.gif", stored)
        self.assertNotIn("products/P-1/archive.avif", stored)
        self.assertNotIn("products/P-1/clip.mp4", stored)
        self.assertEqual(len(stored), 7)
        self.assertEqual(
            {e.status for e in report.entries},
            {COPIED, UNSUPPORTED},
        )

        # An identical re-run rewrites nothing.
        mtimes = {
            p: p.stat().st_mtime_ns for p in self.store_root.rglob("*") if p.is_file()
        }
        second = run_migration(self.source, self.provider)
        self.assertEqual(second.counts["copied"], 0)
        self.assertEqual(second.counts["already_identical"], 7)
        after = {
            p: p.stat().st_mtime_ns for p in self.store_root.rglob("*") if p.is_file()
        }
        self.assertEqual(mtimes, after)


# ===========================================================================
# 5. The real 238-file asset library (requirements 8–9, dry-run acceptance)
# ===========================================================================

@unittest.skipUnless(
    (REAL_SOURCE_DIR / "products").is_dir(),
    "real product asset library not present in this checkout",
)
class RealSourceDryRunTests(unittest.TestCase):
    """
    The acceptance test for this fix: the documented migration walk, run
    read-only against the real development assets.

    This test only ever OPENS the source files for reading (dry run); no
    object is written anywhere, and the tree is re-hashed afterwards to prove
    nothing changed.
    """

    @staticmethod
    def _tree_fingerprint():
        fingerprint = {}
        for path in discover_source_files(REAL_SOURCE_DIR):
            stat = path.stat()
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                while True:
                    chunk = handle.read(1 << 20)
                    if not chunk:
                        break
                    digest.update(chunk)
            key = str(path.relative_to(REAL_SOURCE_DIR)).replace("\\", "/")
            fingerprint[key] = (stat.st_size, stat.st_mode, digest.hexdigest())
        return fingerprint

    def test_dry_run_accepts_every_asset_and_leaves_the_tree_untouched(self):
        from app.storage import LocalStorageProvider

        before = self._tree_fingerprint()
        self.assertGreaterEqual(len(before), 1, "the asset library must exist for this test")

        with tempfile.TemporaryDirectory(prefix="pf6fix-real-") as tmp:
            provider = LocalStorageProvider(Path(tmp) / "media")
            report = run_migration(REAL_SOURCE_DIR, provider, dry_run=True)
            counts = report.counts

            # The reported failure was `unsupported: 238` because .avif and
            # .webp were missing from the allowed image types. With the fix,
            # NONE of that may remain:
            self.assertEqual(counts["total_source_files"], len(before))
            self.assertEqual(counts["planned"], counts["total_source_files"])
            self.assertEqual(counts["unsupported"], 0)
            self.assertEqual(counts["invalid"], 0)
            self.assertEqual(counts["failed"], 0)
            self.assertEqual(counts["collision"], 0)
            self.assertEqual(counts["checksum_mismatch"], 0)
            self.assertTrue(
                all(entry.status == PLANNED for entry in report.entries),
                "dry run: every real asset is a would-copy, none rejected",
            )

            # Every asset resolves to one of the five supported formats,
            # judged BY BYTES; legacy `.avif` names carrying JPEG/PNG are
            # reported with their detected type instead of being rejected.
            for entry in report.entries:
                self.assertIn(entry.content_type, SUPPORTED_IMAGE_MIME_TYPES)
                self.assertTrue(entry.object_key, "a legal object key is derived")
                self.assertEqual(entry.sha256_source, before[entry.source][2])
            mismatches = [e for e in report.entries if e.extension_mismatch]
            for entry in mismatches:
                # A mislabelled asset keeps its name; only the reported and
                # served type follows the bytes.
                self.assertTrue(entry.source.endswith(".avif"))
                self.assertTrue(
                    entry.extension_mismatch.startswith("image/avif->")
                )

            # Dry run: the object store received nothing at all.
            self.assertEqual(list(provider.list_objects()), [])
            self.assertEqual(
                [p for p in Path(tmp).rglob("*") if p.is_file()],
                [],
                "a dry run must not write any file anywhere under the store root",
            )

        # The real asset library is unchanged — same files, sizes, modes and
        # bytes. (Reads do not alter any of these.)
        self.assertEqual(self._tree_fingerprint(), before)

    def test_migration_engine_has_no_write_path_to_the_source_tree(self):
        """Structural guard for copy-only: every file the migration engine
        opens is opened read-binary, and the module contains no move /
        rename / delete / convert primitives at all."""
        from app.services.media import local_media_migration as engine

        source = Path(engine.__file__).read_text(encoding="utf-8")
        opens = re.findall(r"open\((.*?)\)", source)
        self.assertTrue(opens, "the engine reads source files")
        for call in opens:
            self.assertIn('"rb"', call, f"non-readonly file open in the engine: open({call})")
        for forbidden in ("os.remove(", "os.rename(", "os.replace(", "shutil", ".unlink(", ".write_bytes("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":  # pragma: no cover - direct execution helper
    unittest.main()
