"""
Phase 6 — local S3-compatible media storage & product image migration.

Covers the four test groups the phase contract asks for:

  A. Storage provider (1–16)   root creation, put/get/exists/delete, missing
                               object, content type, traversal / absolute /
                               drive-letter rejection, filename sanitisation,
                               collision handling, checksums, idempotent
                               re-run, provider configuration, and proof that
                               nothing outside the root is reachable.
  B. Media API (17–22)         valid object serves, 404 on missing, invalid
                               key rejected, unauthorised mutation rejected,
                               authorised admin mutation works, files outside
                               the media root cannot be read.
  C. Product image integration (23–30) reference normalisation, `/images/…`
                               compatibility during migration, local media
                               URL resolution, missing images, product detail,
                               cart/wishlist/recently-viewed/admin shapes.
  D. Migration (31–38)         dry run writes nothing, copy happens, source
                               untouched, SHA-256 matches, identical re-run
                               skips, collision reported, one failure does not
                               corrupt the rest, summary counts correct.

Style follows the Phase 1–5 suites: no live server, no PostgreSQL, no AWS.
Fixtures build their own tiny asset tree — the real 238 production assets are
never touched by automated tests.
"""

import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.core.exceptions import ForbiddenException
from app.services.media.local_media_migration import (
    ALREADY_IDENTICAL,
    COPIED,
    COLLISION,
    FAILED,
    discover_source_files,
    object_key_for_source_file,
    run_migration,
    verify_migration,
    verify_source_integrity,
)
from app.services.media.media_service import MediaService
from app.services.media.media_validation import (
    MediaValidationError,
    allowed_image_extensions,
    sniff_content_type,
    validate_image_bytes,
)
from app.services.media.product_media_resolver import (
    LEGACY_FALLBACK,
    PASSTHROUGH,
    RESOLVED,
    candidate_object_key,
    clear_resolution_cache,
    explain,
    resolve_product_image_list,
    resolve_product_image_reference,
)
from app.services.media.upload_service import UploadService
from app.storage import (
    InvalidObjectKeyError,
    LocalStorageProvider,
    ObjectCollisionError,
    ObjectNotFoundError,
    StorageProviderNotConfigured,
    create_storage_provider,
    get_storage_provider,
    is_safe_object_key,
    normalize_object_key,
    product_object_key,
    reset_storage_provider,
    sanitize_filename,
    storage_status,
)

# ---------------------------------------------------------------------------
# Fixture bytes — real signatures, generated in memory (never read from the
# production asset library).
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
NOT_AN_IMAGE = b"PK\x03\x04this is a zip file, definitely not an image" + b"\x00" * 32


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Base case: an isolated storage root per test
# ---------------------------------------------------------------------------

class StorageTestCase(unittest.TestCase):
    """Every test gets its own temp root; the real store is never touched."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pf6-storage-")
        self.root = Path(self._tmp.name) / "media"
        self.provider = LocalStorageProvider(self.root)
        self._patches = [
            patch.object(settings, "LOCAL_MEDIA_ROOT", str(self.root)),
            patch.object(settings, "STORAGE_PROVIDER", "local"),
            patch.object(settings, "MEDIA_CDN_BASE_URL", None),
            patch.object(settings, "MEDIA_RESOLVE_PRODUCT_IMAGES", True),
        ]
        for active in self._patches:
            active.start()
        reset_storage_provider()
        clear_resolution_cache()

    def tearDown(self):
        for active in self._patches:
            active.stop()
        reset_storage_provider()
        clear_resolution_cache()
        self._tmp.cleanup()


# ===========================================================================
# A. STORAGE PROVIDER (tests 1–16)
# ===========================================================================

class LocalStorageProviderTests(StorageTestCase):
    """1–7, 12, 13 — the object-store contract."""

    def test_01_root_is_created_on_construction(self):
        self.assertTrue(self.root.is_dir(), "storage root must exist after construction")
        # Idempotent — a second provider over the same root must not fail.
        LocalStorageProvider(self.root)
        self.assertTrue(self.root.is_dir())

    def test_02_object_write_returns_descriptor_with_url(self):
        stored = self.provider.put_object("products/PF-A-1/primary.png", PNG_BYTES, "image/png")
        self.assertEqual(stored.key, "products/PF-A-1/primary.png")
        self.assertTrue(stored.created)
        self.assertEqual(stored.checksum_sha256, sha256(PNG_BYTES))
        self.assertEqual(
            stored.url,
            "/api/v1/media/objects/products/PF-A-1/primary.png",
        )
        self.assertTrue((self.root / "products" / "PF-A-1" / "primary.png").is_file())

    def test_03_object_read_round_trips_bytes_exactly(self):
        self.provider.put_object("products/PF-A-1/a.png", PNG_BYTES, "image/png")
        self.assertEqual(self.provider.get_object("products/PF-A-1/a.png"), PNG_BYTES)
        with self.provider.open_object("products/PF-A-1/a.png") as handle:
            self.assertEqual(handle.read(), PNG_BYTES)

    def test_04_object_exists(self):
        self.assertFalse(self.provider.object_exists("products/PF-A-1/a.png"))
        self.provider.put_object("products/PF-A-1/a.png", PNG_BYTES, "image/png")
        self.assertTrue(self.provider.object_exists("products/PF-A-1/a.png"))

    def test_05_object_deletion(self):
        self.provider.put_object("products/PF-A-1/a.png", PNG_BYTES, "image/png")
        self.assertTrue(self.provider.delete_object("products/PF-A-1/a.png"))
        self.assertFalse(self.provider.object_exists("products/PF-A-1/a.png"))
        # Second delete is a no-op, not an error.
        self.assertFalse(self.provider.delete_object("products/PF-A-1/a.png"))

    def test_06_missing_object_raises_and_never_leaks_a_path(self):
        with self.assertRaises(ObjectNotFoundError) as ctx:
            self.provider.get_object("products/PF-A-1/nope.png")
        self.assertNotIn(str(self.root), str(ctx.exception))
        with self.assertRaises(ObjectNotFoundError):
            self.provider.get_metadata("products/PF-A-1/nope.png")
        with self.assertRaises(ObjectNotFoundError):
            self.provider.open_object("products/PF-A-1/nope.png")

    def test_07_content_type_is_the_real_type_not_the_extension(self):
        # The real asset library contains .avif names holding JPEG bytes; the
        # provider must report what the bytes actually are.
        self.provider.put_object("products/PF-A-1/mislabel.avif", JPEG_BYTES, "image/avif")
        metadata = self.provider.get_metadata("products/PF-A-1/mislabel.avif")
        self.assertEqual(metadata.content_type, "image/jpeg")
        self.provider.put_object("products/PF-A-1/real.avif", AVIF_BYTES, "image/avif")
        self.assertEqual(
            self.provider.get_metadata("products/PF-A-1/real.avif").content_type,
            "image/avif",
        )
        self.provider.put_object("products/PF-A-1/pic.webp", WEBP_BYTES, "image/webp")
        self.assertEqual(
            self.provider.get_metadata("products/PF-A-1/pic.webp").content_type,
            "image/webp",
        )

    def test_12_collision_is_detected_not_silently_overwritten(self):
        media = MediaService(storage=self.provider)
        first = media.store_image(
            filename="primary.png", data=PNG_BYTES, product_id="PF-A-1"
        )
        self.assertTrue(first["created"])
        # Identical bytes → idempotent, nothing rewritten.
        again = media.store_image(
            filename="primary.png", data=PNG_BYTES, product_id="PF-A-1"
        )
        self.assertFalse(again["created"])
        self.assertTrue(again["alreadyExists"])
        # Different bytes → collision, existing object untouched.
        with self.assertRaises(ObjectCollisionError):
            media.store_image(filename="primary.png", data=JPEG_BYTES, product_id="PF-A-1")
        self.assertEqual(
            self.provider.get_object("products/PF-A-1/primary.png"), PNG_BYTES
        )

    def test_13_checksum_verification_matches_source(self):
        stored = self.provider.put_object("products/PF-A-1/a.png", PNG_BYTES)
        self.assertEqual(stored.metadata.checksum_sha256, sha256(PNG_BYTES))
        self.assertEqual(
            self.provider.get_metadata("products/PF-A-1/a.png").checksum_sha256,
            sha256(PNG_BYTES),
        )

    def test_listing_only_sees_objects_inside_the_root(self):
        self.provider.put_object("products/PF-A-1/a.png", PNG_BYTES)
        self.provider.put_object("hero/hero001.png", PNG_BYTES)
        keys = set(self.provider.list_objects())
        self.assertEqual(keys, {"products/PF-A-1/a.png", "hero/hero001.png"})
        self.assertEqual(set(self.provider.list_objects("hero")), {"hero/hero001.png"})

    def test_describe_never_exposes_the_filesystem_root(self):
        payload = json.dumps(self.provider.describe())
        self.assertNotIn(str(self.root), payload)
        self.assertEqual(self.provider.describe()["provider"], "local")


class ObjectKeySecurityTests(StorageTestCase):
    """8–11, 16 — path traversal, absolute paths, Windows drives, sanitising."""

    TRAVERSAL_KEYS = (
        "../etc/passwd",
        "../../etc/passwd",
        "products/../../etc/passwd",
        "..\\..\\windows\\win.ini",
        "products\\..\\..\\app\\config.py",
        "/etc/passwd",
        "//server/share/secret",
        "C:/Windows/win.ini",
        "D:\\pfv1\\backend\\app\\config.py",
        "products/PF-A/.env",
        "products/.hidden/x.png",
        "products//double.png",
        "products/PF-A/",
        "secrets/token.txt",
        "",
        "   ",
    )

    def test_08_and_09_and_10_dangerous_keys_are_rejected(self):
        for key in self.TRAVERSAL_KEYS:
            with self.subTest(key=key):
                self.assertFalse(is_safe_object_key(key), f"should reject {key!r}")
                with self.assertRaises(InvalidObjectKeyError):
                    normalize_object_key(key)

    def test_08b_encoded_traversal_is_rejected(self):
        """
        Percent-encoded traversal is decoded BEFORE validation, so the
        escape attempt is visible to the validator instead of slipping past
        it as an innocent-looking string.
        """
        from app.storage.keys import decode_object_key

        for encoded in (
            "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
            "..%2f..%2fetc%2fpasswd",
            "products%2f..%2f..%2fapp%2fconfig.py",
            "C%3A/Windows/win.ini",
        ):
            with self.subTest(encoded=encoded):
                self.assertFalse(is_safe_object_key(encoded))
                with self.assertRaises(InvalidObjectKeyError):
                    normalize_object_key(encoded)
        # The decode step is what exposes the traversal segment.
        self.assertIn("..", decode_object_key("%2e%2e%2fx%2fy").split("/"))

    def test_08c_provider_refuses_every_dangerous_key_before_any_io(self):
        for key in self.TRAVERSAL_KEYS:
            with self.subTest(key=key):
                with self.assertRaises(InvalidObjectKeyError):
                    self.provider.put_object(key, PNG_BYTES)
                with self.assertRaises(InvalidObjectKeyError):
                    self.provider.get_object(key)
                self.assertFalse(self.provider.object_exists(key))
        self.assertEqual(list(self.provider.list_objects()), [])

    def test_11_filename_sanitisation(self):
        cases = {
            # Only the leaf survives — a traversal prefix is dropped, not kept.
            "../../etc/passwd": "passwd",
            "..\\..\\windows\\win.ini": "win.ini",
            "C:/temp/x.PNG": "x.png",
            "C:\\Windows\\win.ini": "win.ini",
            "products/PF-A/../../secret.txt": "secret.txt",
            "My Photo (final) #2.JPG": "my-photo-final-2.jpg",
            "  spaced  out . webp ": "spaced-out.webp",
            "ünicode-näme.png": "unicode-name.png",
            "a" * 400 + ".png": ("a" * 124) + ".png",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                cleaned = sanitize_filename(raw)
                self.assertEqual(cleaned, expected)
                self.assertNotIn("/", cleaned)
                self.assertNotIn("\\", cleaned)
                self.assertNotIn("\x00", cleaned)
                self.assertFalse(cleaned.startswith("."))

    def test_11b_sanitiser_rejects_names_with_nothing_safe_left(self):
        for raw in ("", "   ", "....", "///", "\x00"):
            with self.subTest(raw=raw):
                with self.assertRaises(InvalidObjectKeyError):
                    sanitize_filename(raw)

    def test_11c_product_object_key_is_deterministic_and_namespaced(self):
        self.assertEqual(
            product_object_key("PF-W-SAR-SIL-0001", "Primary.PNG"),
            "products/PF-W-SAR-SIL-0001/primary.png",
        )
        # Same input twice → same key (no random temp names anywhere).
        self.assertEqual(
            product_object_key("PF-W-SAR-SIL-0001", "Primary.PNG"),
            product_object_key("pf-w-sar-sil-0001", "primary.png"),
        )
        with self.assertRaises(InvalidObjectKeyError):
            product_object_key("../evil", "a.png")
        with self.assertRaises(InvalidObjectKeyError):
            product_object_key("not a product id!", "a.png")

    def test_16_symlink_escape_is_refused(self):
        outside = Path(self._tmp.name) / "outside.png"
        outside.write_bytes(PNG_BYTES)
        (self.root / "products").mkdir(parents=True, exist_ok=True)
        link = self.root / "products" / "PF-A" 
        link.mkdir(parents=True, exist_ok=True)
        target = link / "escape.png"
        try:
            os.symlink(outside, target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this platform")
        # The link resolves outside the root, so containment must reject it.
        self.assertFalse(self.provider.object_exists("products/PF-A/escape.png"))
        with self.assertRaises((ObjectNotFoundError, InvalidObjectKeyError)):
            self.provider.get_object("products/PF-A/escape.png")
        with self.assertRaises((ObjectNotFoundError, InvalidObjectKeyError)):
            self.provider.get_metadata("products/PF-A/escape.png")

    def test_16b_no_file_is_ever_created_outside_the_root(self):
        for key in self.TRAVERSAL_KEYS:
            with self.assertRaises(InvalidObjectKeyError):
                self.provider.put_object(key, PNG_BYTES)
        outside_files = [
            p
            for p in Path(self._tmp.name).rglob("*")
            if p.is_file() and not str(p).startswith(str(self.root))
        ]
        self.assertEqual(outside_files, [])


class StorageProviderConfigurationTests(StorageTestCase):
    """15 — provider selection through configuration."""

    def test_15_local_is_the_default_provider(self):
        self.assertEqual(settings.storage_provider_name, "local")
        self.assertEqual(get_storage_provider().name, "local")
        self.assertIsInstance(get_storage_provider(), LocalStorageProvider)

    def test_15b_local_root_is_configurable_and_never_machine_specific(self):
        with tempfile.TemporaryDirectory(prefix="pf6-alt-") as alt:
            target = Path(alt) / "custom-root"
            with patch.object(settings, "LOCAL_MEDIA_ROOT", str(target)):
                reset_storage_provider()
                provider = get_storage_provider()
                self.assertEqual(Path(provider.root), target.resolve())
                provider.put_object("hero/x.png", PNG_BYTES)
                self.assertTrue((target / "hero" / "x.png").is_file())

    def test_15c_relative_root_resolves_against_the_backend_directory(self):
        resolved = settings.local_media_root_path
        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved.name, Path(settings.LOCAL_MEDIA_ROOT).name)
        # A relative default must NOT depend on the process working directory.
        with patch.object(settings, "LOCAL_MEDIA_ROOT", "storage/media"):
            self.assertTrue(settings.local_media_root_path.is_absolute())
            self.assertEqual(settings.local_media_root_path.name, "media")

    def test_15d_s3_provider_is_interface_ready_and_refuses_fake_credentials(self):
        with patch.object(settings, "STORAGE_PROVIDER", "s3"):
            reset_storage_provider()
            with self.assertRaises(StorageProviderNotConfigured):
                get_storage_provider()
        # The default placeholder values from .env.example count as missing.
        from app.storage.s3 import S3StorageProvider

        with self.assertRaises(StorageProviderNotConfigured):
            S3StorageProvider(
                bucket="pratikshya-fashon-media",
                region="ap-south-1",
                access_key_id="your-access-key",
                secret_access_key="your-secret-key",
            )

    def test_15e_unknown_provider_fails_loudly(self):
        with patch.object(settings, "STORAGE_PROVIDER", "minio-magic"):
            with self.assertRaises(StorageProviderNotConfigured):
                create_storage_provider()

    def test_15f_status_payload_carries_no_secrets_and_no_paths(self):
        payload = json.dumps(storage_status())
        self.assertNotIn(str(self.root), payload)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", payload)
        self.assertNotIn("your-secret-key", payload)
        status = storage_status()
        self.assertTrue(status["ok"])
        self.assertEqual(status["provider"], "local")
        self.assertEqual(status["urlPrefix"], "/api/v1/media/objects")
        self.assertIn("products", status["namespaces"])


# ===========================================================================
# B. MEDIA VALIDATION
# ===========================================================================

class MediaValidationTests(unittest.TestCase):
    """5–6 of the phase contract: type, size, empty, malformed uploads."""

    def test_allowed_extensions_come_from_configuration(self):
        extensions = allowed_image_extensions()
        self.assertIn(".avif", extensions)
        self.assertIn(".webp", extensions)
        self.assertIn(".png", extensions)
        with patch.object(settings, "ALLOWED_IMAGE_TYPES", "image/png"):
            self.assertEqual(allowed_image_extensions(), [".png"])

    def test_valid_images_pass(self):
        for name, blob, expected in (
            ("a.png", PNG_BYTES, "image/png"),
            ("a.jpg", JPEG_BYTES, "image/jpeg"),
            ("a.jpeg", JPEG_BYTES, "image/jpeg"),
            ("a.webp", WEBP_BYTES, "image/webp"),
            ("a.avif", AVIF_BYTES, "image/avif"),
        ):
            with self.subTest(name=name):
                result = validate_image_bytes(name, blob)
                self.assertEqual(result.content_type, expected)
                self.assertEqual(result.checksum_sha256, sha256(blob))

    def test_empty_file_is_rejected(self):
        with self.assertRaises(MediaValidationError):
            validate_image_bytes("a.png", b"")

    def test_oversize_file_is_rejected(self):
        with patch.object(settings, "MAX_IMAGE_SIZE_MB", 0):
            with self.assertRaises(MediaValidationError):
                validate_image_bytes("a.png", PNG_BYTES)

    def test_unknown_extension_is_rejected(self):
        with self.assertRaises(MediaValidationError):
            validate_image_bytes("archive.zip", NOT_AN_IMAGE)

    def test_no_extension_is_rejected(self):
        with self.assertRaises(MediaValidationError):
            validate_image_bytes("noextension", PNG_BYTES)

    def test_content_is_checked_not_just_the_filename(self):
        # A zip renamed .png must be refused — the filename alone is never trusted.
        with self.assertRaises(MediaValidationError):
            validate_image_bytes("lie.png", NOT_AN_IMAGE)
        with self.assertRaises(MediaValidationError):
            validate_image_bytes("lie.avif", NOT_AN_IMAGE)

    def test_disallowed_type_is_rejected_even_when_well_formed(self):
        with patch.object(settings, "ALLOWED_IMAGE_TYPES", "image/png"):
            with self.assertRaises(MediaValidationError):
                validate_image_bytes("a.jpg", JPEG_BYTES)

    def test_mislabelled_but_real_image_is_accepted_and_reported(self):
        result = validate_image_bytes("photo.avif", JPEG_BYTES)
        self.assertEqual(result.content_type, "image/jpeg")
        self.assertTrue(result.extension_mismatch)
        self.assertEqual(result.declared_extension_type, "image/avif")

    def test_sniffer_identifies_real_signatures(self):
        self.assertEqual(sniff_content_type(PNG_BYTES), "image/png")
        self.assertEqual(sniff_content_type(JPEG_BYTES), "image/jpeg")
        self.assertEqual(sniff_content_type(WEBP_BYTES), "image/webp")
        self.assertEqual(sniff_content_type(AVIF_BYTES), "image/avif")
        self.assertIsNone(sniff_content_type(NOT_AN_IMAGE))
        self.assertIsNone(sniff_content_type(b""))


# ===========================================================================
# C. MEDIA API (tests 17–22)
# ===========================================================================

class MediaApiTests(StorageTestCase):
    """17–22 — served through the real router, no database required."""

    def setUp(self):
        super().setUp()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.v1.media import router as media_router
        from app.core.error_handlers import register_error_handlers

        self.service_patch = patch(
            "app.api.v1.media._get_media_service",
            return_value=MediaService(storage=self.provider),
        )
        self.service_patch.start()

        # The same error-handler registration the real app uses, so the
        # AppException → HTTP mapping under test is the production one.
        app = FastAPI()
        register_error_handlers(app)
        app.include_router(media_router, prefix="/api/v1")
        self.client = TestClient(app)
        self.provider.put_object(
            "products/PF-A-1/primary.png", PNG_BYTES, "image/png"
        )
        self.provider.put_object(
            "collections/fabrics/silk/PF-COL-1/01.avif", JPEG_BYTES, "image/avif"
        )

    def tearDown(self):
        self.service_patch.stop()
        super().tearDown()

    def test_17_valid_media_is_served_with_the_correct_content_type(self):
        response = self.client.get("/api/v1/media/objects/products/PF-A-1/primary.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PNG_BYTES)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertIn("etag", {k.lower() for k in response.headers})
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_17b_content_type_follows_the_bytes_for_mislabelled_assets(self):
        response = self.client.get(
            "/api/v1/media/objects/collections/fabrics/silk/PF-COL-1/01.avif"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")

    def test_18_missing_media_returns_404(self):
        response = self.client.get("/api/v1/media/objects/products/PF-A-1/absent.png")
        self.assertEqual(response.status_code, 404)

    def test_19_invalid_object_keys_are_rejected(self):
        for key in (
            "../etc/passwd",
            "..%2f..%2fetc%2fpasswd",
            "products/../../etc/passwd",
            "C:/Windows/win.ini",
            "secrets/token.txt",
            "products/PF-A/.env",
        ):
            with self.subTest(key=key):
                response = self.client.get(f"/api/v1/media/objects/{key}")
                self.assertIn(response.status_code, (400, 404, 422), key)
                self.assertNotEqual(response.status_code, 200, key)

    def test_20_unauthorized_mutation_is_rejected(self):
        response = self.client.delete("/api/v1/media/objects/products/PF-A-1/primary.png")
        self.assertIn(response.status_code, (401, 403))
        # The object must survive the attempt.
        self.assertTrue(self.provider.object_exists("products/PF-A-1/primary.png"))

    def test_21_authorized_admin_mutation_works(self):
        """The upload route, exercised with the admin guard satisfied."""
        import asyncio

        from app.api.v1 import media as media_module

        upload = _fake_upload("extra.png", "image/png", PNG_BYTES)
        user = SimpleNamespace(id="u1", user_type="admin")
        db = AsyncMock()

        async def allow(*_args, **_kwargs):
            return None

        with patch.object(media_module, "require_admin_permission", allow), patch.object(
            media_module, "get_current_admin", lambda: user
        ), patch.object(media_module.UploadService, "__init__", lambda self, *_a, **_k: None):
            media_module.UploadService.db = db
            media_module.UploadService.media = MediaService(storage=self.provider)
            result = asyncio.run(
                media_module.upload_media_object(
                    file=upload,
                    namespace="products",
                    product_id="PF-A-1",
                    group=None,
                    db=db,
                    current_user=user,
                )
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["object"]["key"], "products/PF-A-1/extra.png")
        self.assertEqual(
            result["object"]["url"],
            "/api/v1/media/objects/products/PF-A-1/extra.png",
        )
        self.assertTrue(self.provider.object_exists("products/PF-A-1/extra.png"))

    def test_21b_admin_permission_is_actually_required(self):
        import asyncio

        from app.api.v1 import media as media_module

        upload = _fake_upload("extra.png", "image/png", PNG_BYTES)
        user = SimpleNamespace(id="u1", user_type="admin")

        async def deny(*_args, **_kwargs):
            raise ForbiddenException("Missing required permission: media.upload")

        with patch.object(media_module, "require_admin_permission", deny):
            with self.assertRaises(ForbiddenException):
                asyncio.run(
                    media_module.upload_media_object(
                        file=upload,
                        namespace="products",
                        product_id="PF-A-1",
                        group=None,
                        db=AsyncMock(),
                        current_user=user,
                    )
                )
        self.assertFalse(self.provider.object_exists("products/PF-A-1/extra.png"))

    def test_21c_admin_delete_removes_only_the_named_object(self):
        import asyncio

        from app.api.v1 import media as media_module

        self.provider.put_object("products/PF-A-1/second.png", PNG_BYTES)

        async def allow(*_args, **_kwargs):
            return None

        with patch.object(media_module, "require_admin_permission", allow):
            result = asyncio.run(
                media_module.delete_media_object(
                    object_key="products/PF-A-1/second.png",
                    db=AsyncMock(),
                    current_user=SimpleNamespace(id="u1", user_type="admin"),
                )
            )
        self.assertTrue(result["ok"])
        self.assertFalse(self.provider.object_exists("products/PF-A-1/second.png"))
        self.assertTrue(self.provider.object_exists("products/PF-A-1/primary.png"))

    def test_22_files_outside_the_media_root_cannot_be_read(self):
        secret = Path(self._tmp.name) / "secret.txt"
        secret.write_text("top secret")
        for attempt in (
            "../secret.txt",
            "..%2fsecret.txt",
            "products/../../secret.txt",
            "products/%2e%2e/%2e%2e/secret.txt",
        ):
            with self.subTest(attempt=attempt):
                response = self.client.get(f"/api/v1/media/objects/{attempt}")
                self.assertNotEqual(response.status_code, 200, attempt)
                self.assertNotIn(b"top secret", response.content)

    def test_storage_status_endpoint_exposes_no_secrets(self):
        response = self.client.get("/api/v1/media/storage/status")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["provider"], "local")
        self.assertEqual(body["urlPrefix"], "/api/v1/media/objects")
        self.assertNotIn(str(self.root), response.text)

    def test_resolve_endpoint_reports_the_fallback_honestly(self):
        response = self.client.post(
            "/api/v1/media/references/resolve",
            json={
                "references": [
                    "/images/products/PF-A-1/primary.png",
                    "/images/products/NOT-MIGRATED/01.png",
                    "/images/nope.png",
                ]
            },
        )
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual(items[0]["status"], "resolved")
        self.assertEqual(
            items[0]["url"], "/api/v1/media/objects/products/PF-A-1/primary.png"
        )
        # Not in the object store yet → the legacy reference is kept, and the
        # decision is reported rather than hidden.
        self.assertEqual(items[1]["status"], "legacy-fallback")
        self.assertEqual(items[1]["url"], "/images/products/NOT-MIGRATED/01.png")
        self.assertEqual(items[1]["objectKey"], "products/NOT-MIGRATED/01.png")
        # Not a namespaced media path at all → passed through untouched.
        self.assertEqual(items[2]["status"], "passthrough")
        self.assertEqual(items[2]["url"], "/images/nope.png")


class MediaServiceContractTests(StorageTestCase):
    """The service layer the routes delegate to."""

    def test_object_url_is_application_level_never_a_path(self):
        media = MediaService(storage=self.provider)
        url = media.object_url("products/PF-A-1/primary.png")
        self.assertEqual(url, "/api/v1/media/objects/products/PF-A-1/primary.png")
        self.assertNotIn(str(self.root), url)
        self.assertNotIn("\\", url)

    def test_store_image_rejects_non_image_uploads(self):
        media = MediaService(storage=self.provider)
        with self.assertRaises(MediaValidationError):
            media.store_image(filename="x.png", data=NOT_AN_IMAGE, product_id="PF-A-1")
        self.assertEqual(list(self.provider.list_objects()), [])

    def test_upload_service_rejects_oversize_before_buffering_the_rest(self):
        media = MediaService(storage=self.provider)
        service = UploadService(media_service=media)

        class BigFile:
            filename = "big.png"
            content_type = "image/png"

            def __init__(self):
                self.calls = 0

            def read(self, size):
                self.calls += 1
                if self.calls > 3:
                    return b""
                return b"\x00" * size

        import asyncio

        with patch.object(settings, "MAX_IMAGE_SIZE_MB", 1):
            with self.assertRaises(MediaValidationError):
                asyncio.run(
                    service.store_upload(
                        file_obj=BigFile(), filename="big.png", product_id="PF-A-1"
                    )
                )
        self.assertEqual(list(self.provider.list_objects()), [])

    def test_upload_service_reports_a_content_type_mismatch_without_failing(self):
        media = MediaService(storage=self.provider)
        service = UploadService(media_service=media)
        blob = io.BytesIO(PNG_BYTES)

        import asyncio

        result = asyncio.run(
            service.store_upload(
                file_obj=blob,
                filename="photo.avif",
                declared_content_type="image/avif",
                product_id="PF-A-1",
            )
        )
        self.assertEqual(result["contentType"], "image/png")
        self.assertEqual(result["declaredContentTypeMismatch"], "image/avif")

    def test_delete_is_narrow_and_never_cascades(self):
        media = MediaService(storage=self.provider)
        media.store_image(filename="a.png", data=PNG_BYTES, product_id="PF-A-1")
        media.store_image(filename="b.png", data=WEBP_BYTES, product_id="PF-A-1")
        self.assertTrue(media.delete_object("products/PF-A-1/a.png"))
        self.assertTrue(self.provider.object_exists("products/PF-A-1/b.png"))


# ===========================================================================
# D. PRODUCT IMAGE INTEGRATION (tests 23–30)
# ===========================================================================

class ProductImageResolutionTests(StorageTestCase):
    """23–30 — the backend, not the frontend, decides the canonical URL."""

    def setUp(self):
        super().setUp()
        self.provider.put_object(
            "products/PF-W-SAR-SIL-0001/primary.avif", AVIF_BYTES, "image/avif"
        )

    def test_23_product_image_references_normalize_correctly(self):
        self.assertEqual(
            resolve_product_image_reference(
                "/images/products/PF-W-SAR-SIL-0001/primary.avif"
            ),
            "/api/v1/media/objects/products/PF-W-SAR-SIL-0001/primary.avif",
        )
        self.assertEqual(
            resolve_product_image_reference(
                "products/PF-W-SAR-SIL-0001/primary.avif"
            ),
            "/api/v1/media/objects/products/PF-W-SAR-SIL-0001/primary.avif",
        )
        # Already canonical → unchanged (no double-prefixing).
        canonical = "/api/v1/media/objects/products/PF-W-SAR-SIL-0001/primary.avif"
        self.assertEqual(resolve_product_image_reference(canonical), canonical)
        # Empty / None → empty string, never a placeholder.
        self.assertEqual(resolve_product_image_reference(""), "")
        self.assertEqual(resolve_product_image_reference(None), "")
        self.assertEqual(resolve_product_image_reference("   "), "")

    def test_24_legacy_images_references_stay_compatible_during_migration(self):
        legacy = "/images/products/NOT-MIGRATED-YET/primary.avif"
        self.assertEqual(resolve_product_image_reference(legacy), legacy)
        self.assertEqual(explain(legacy)["status"], LEGACY_FALLBACK)
        # The decision is observable, not silent.
        self.assertEqual(
            explain(legacy)["objectKey"], "products/NOT-MIGRATED-YET/primary.avif"
        )

    def test_25_local_media_urls_resolve_correctly(self):
        decision = explain("/images/products/PF-W-SAR-SIL-0001/primary.avif")
        self.assertEqual(decision["status"], RESOLVED)
        self.assertTrue(self.provider.object_exists(decision["objectKey"]))

    def test_25b_absolute_and_remote_urls_are_preserved_verbatim(self):
        for value in (
            "https://cdn.example.com/x.jpg",
            "http://localhost:5173/x.jpg",
            "data:image/png;base64,AAAA",
        ):
            with self.subTest(value=value):
                self.assertEqual(resolve_product_image_reference(value), value)
                self.assertEqual(explain(value)["status"], PASSTHROUGH)

    def test_25c_an_unresolvable_media_id_is_never_guessed_at(self):
        self.assertEqual(resolve_product_image_reference("pm-lx8f2k-417"), "pm-lx8f2k-417")
        self.assertEqual(explain("pm-lx8f2k-417")["status"], PASSTHROUGH)

    def test_26_missing_images_do_not_crash_and_never_borrow_another_image(self):
        before = resolve_product_image_reference(
            "/images/products/PF-OTHER-PRODUCT/primary.avif"
        )
        self.assertEqual(before, "/images/products/PF-OTHER-PRODUCT/primary.avif")
        # A gallery with gaps collapses to the entries that resolve.
        resolved = resolve_product_image_list(
            [
                "/images/products/PF-W-SAR-SIL-0001/primary.avif",
                "",
                None,
                "/images/products/missing/01.avif",
            ]
        )
        self.assertEqual(len(resolved), 2)
        self.assertEqual(
            resolved[0],
            "/api/v1/media/objects/products/PF-W-SAR-SIL-0001/primary.avif",
        )
        self.assertEqual(resolved[1], "/images/products/missing/01.avif")

    def test_27_product_detail_projection_uses_the_canonical_media_url(self):
        from app.services.catalog.product_service import ProductService

        product = _product_stub(
            image="/images/products/PF-W-SAR-SIL-0001/primary.avif",
            hover_image="/images/products/PF-W-SAR-SIL-0001/02.avif",
            additional_images=[
                "/images/products/PF-W-SAR-SIL-0001/primary.avif",
                "/images/products/PF-W-SAR-SIL-0001/03.avif",
            ],
        )
        service = ProductService(db=AsyncMock())
        storefront = service._to_storefront(product)
        self.assertEqual(
            storefront.image,
            "/api/v1/media/objects/products/PF-W-SAR-SIL-0001/primary.avif",
        )
        # hover_image was not migrated → the legacy reference is preserved.
        self.assertEqual(
            storefront.hover_image, "/images/products/PF-W-SAR-SIL-0001/02.avif"
        )
        self.assertEqual(
            storefront.additional_images[0],
            "/api/v1/media/objects/products/PF-W-SAR-SIL-0001/primary.avif",
        )

    def test_30_admin_projection_uses_the_same_contract(self):
        from app.services.catalog.product_service import ProductService

        product = _product_stub(
            image="/images/products/PF-W-SAR-SIL-0001/primary.avif"
        )
        admin = ProductService(db=AsyncMock())._to_admin(product)
        self.assertEqual(
            admin.image,
            "/api/v1/media/objects/products/PF-W-SAR-SIL-0001/primary.avif",
        )
        # Media id columns are passed through untouched.
        self.assertEqual(admin.primary_media_id, "pm-primary")
        self.assertEqual(admin.media_ids, ["pm-primary", "pm-2"])

    def test_28_cart_and_wishlist_image_shapes_stay_compatible(self):
        """The resolved value is still a plain string the cart can store."""
        url = resolve_product_image_reference(
            "/images/products/PF-W-SAR-SIL-0001/primary.avif"
        )
        self.assertIsInstance(url, str)
        self.assertTrue(url.startswith("/api/v1/media/objects/"))
        # The order/cart read model copies `image` verbatim; nothing new is required.
        line = {"productId": "PF-W-SAR-SIL-0001", "image": url}
        self.assertEqual(
            line["image"],
            "/api/v1/media/objects/products/PF-W-SAR-SIL-0001/primary.avif",
        )

    def test_29_recently_viewed_uses_the_same_projection(self):
        from app.services.catalog.product_service import ProductService

        product = _product_stub(
            image="/images/products/PF-W-SAR-SIL-0001/primary.avif"
        )
        items = [
            ProductService(db=AsyncMock())._to_storefront(product) for _ in range(3)
        ]
        self.assertEqual(len({item.image for item in items}), 1)
        self.assertTrue(items[0].image.startswith("/api/v1/media/objects/"))

    def test_resolution_can_be_switched_off_without_a_code_change(self):
        legacy = "/images/products/PF-W-SAR-SIL-0001/primary.avif"
        with patch.object(settings, "MEDIA_RESOLVE_PRODUCT_IMAGES", False):
            clear_resolution_cache()
            self.assertEqual(resolve_product_image_reference(legacy), legacy)
            self.assertEqual(explain(legacy)["status"], "disabled")

    def test_cdn_configuration_changes_only_the_url_shape(self):
        legacy = "/images/products/PF-W-SAR-SIL-0001/primary.avif"
        with patch.object(settings, "MEDIA_CDN_BASE_URL", "https://cdn.example.com/"):
            clear_resolution_cache()
            self.assertEqual(
                resolve_product_image_reference(legacy),
                "https://cdn.example.com/products/PF-W-SAR-SIL-0001/primary.avif",
            )

    def test_candidate_object_key_mapping(self):
        self.assertEqual(
            candidate_object_key("/images/products/PF-A/x.png"), "products/PF-A/x.png"
        )
        self.assertEqual(
            candidate_object_key("products/PF-A/x.png"), "products/PF-A/x.png"
        )
        self.assertEqual(
            candidate_object_key(
                "/api/v1/media/objects/products/PF-A/x.png"
            ),
            "products/PF-A/x.png",
        )
        self.assertIsNone(candidate_object_key("/etc/passwd"))
        self.assertIsNone(candidate_object_key("pm-abc"))
        self.assertIsNone(candidate_object_key(""))

    def test_resolution_is_cached_but_refreshable(self):
        legacy = "/images/products/PF-CACHE/x.png"
        self.assertEqual(explain(legacy)["status"], LEGACY_FALLBACK)
        # The object appears (e.g. a migration run finished).
        self.provider.put_object("products/PF-CACHE/x.png", PNG_BYTES)
        clear_resolution_cache()
        self.assertEqual(explain(legacy)["status"], RESOLVED)


def _fake_upload(filename: str, content_type: str, data: bytes):
    """An `UploadFile` stand-in: bounded `read(size)`, real filename/type."""
    stream = io.BytesIO(data)
    return SimpleNamespace(
        filename=filename,
        content_type=content_type,
        read=stream.read,
    )


def _product_stub(**overrides):
    """A ProductModel-shaped stub carrying everything the projections read."""
    base = dict(
        id="PF-W-SAR-SIL-0001",
        product_id="PF-W-SAR-SIL-0001",
        name="Banarasi Silk Saree",
        slug="banarasi-silk-saree",
        sku="PF-W-SAR-SIL-0001",
        brand="Pratikshya Fashon",
        product_type="fashion",
        product_code="",
        barcode="",
        internal_reference="",
        category="sarees",
        subcategory="silk",
        gender="Women",
        short_description="",
        description="",
        highlights=[],
        specifications={},
        care_instructions=[],
        delivery_info="",
        return_info="",
        return_policy=None,
        fabric="silk",
        material="silk",
        primary_color="red",
        secondary_color="",
        colors=["red"],
        patterns=[],
        work=[],
        occasion=[],
        sizes=[],
        unavailable_colors=[],
        unavailable_sizes=[],
        season="",
        fit="",
        length="",
        collection="",
        collections=[],
        tags=[],
        badges=[],
        is_featured=False,
        is_bestseller=False,
        is_new=False,
        is_limited_edition=False,
        is_trending=False,
        flags={},
        price=5000,
        original_price=6000,
        compare_at_price=None,
        currency="INR",
        pricing=None,
        price_history=[],
        stock=4,
        availability="in-stock",
        inventory_tracked=False,
        low_stock_threshold=5,
        rating=None,
        review_count=0,
        seo=None,
        status="PUBLISHED",
        published=True,
        review=None,
        review_flags=[],
        assigned_employee_id=None,
        media_ids=["pm-primary", "pm-2"],
        primary_media_id="pm-primary",
        gallery_media_ids=[],
        image="",
        hover_image="",
        additional_images=[],
        created_by=None,
        created_at=None,
        updated_by=None,
        updated_at=None,
        published_by=None,
        published_at=None,
        history=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ===========================================================================
# E. MIGRATION (tests 31–38)
# ===========================================================================

class MigrationTestCase(unittest.TestCase):
    """Builds a tiny synthetic asset tree; never touches the real 238 files."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pf6-migrate-")
        self.base = Path(self._tmp.name)
        self.source = self.base / "source"
        self.store_root = self.base / "store"
        self.provider = LocalStorageProvider(self.store_root)
        (self.source / "products" / "PF-A-1").mkdir(parents=True)
        (self.source / "hero").mkdir(parents=True)
        # Names match bytes everywhere except the deliberate mismatch case,
        # so `extension_mismatch` stays a precise assertion.
        self._write("products/PF-A-1/primary.png", PNG_BYTES)
        self._write("products/PF-A-1/01.webp", WEBP_BYTES)
        self._write("hero/hero001.jpg", JPEG_BYTES)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, relative, data: bytes):
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def _source_hashes(self):
        return {
            str(p.relative_to(self.source)).replace("\\", "/"): sha256(p.read_bytes())
            for p in discover_source_files(self.source)
        }


class MigrationTests(MigrationTestCase):
    def test_31_dry_run_writes_nothing(self):
        before = self._source_hashes()
        report = run_migration(self.source, self.provider, dry_run=True)
        self.assertTrue(report.dry_run)
        self.assertEqual(report.counts["total_source_files"], 3)
        self.assertEqual(report.counts["planned"], 3)
        self.assertEqual(report.counts["copied"], 0)
        self.assertEqual(list(self.provider.list_objects()), [])
        self.assertFalse(any(self.store_root.rglob("*.png")))
        self.assertEqual(self._source_hashes(), before)

    def test_32_migration_copies_files_under_the_object_key_convention(self):
        report = run_migration(self.source, self.provider)
        self.assertEqual(report.counts["copied"], 3)
        self.assertEqual(
            set(self.provider.list_objects()),
            {
                "products/PF-A-1/primary.png",
                "products/PF-A-1/01.webp",
                "hero/hero001.jpg",
            },
        )
        self.assertEqual(
            self.provider.get_object("products/PF-A-1/primary.png"), PNG_BYTES
        )

    def test_33_source_files_remain_unchanged(self):
        before = self._source_hashes()
        run_migration(self.source, self.provider)
        self.assertEqual(self._source_hashes(), before)
        self.assertEqual(len(discover_source_files(self.source)), 3)

    def test_34_sha256_matches_between_source_and_destination(self):
        report = run_migration(self.source, self.provider)
        sources = self._source_hashes()
        for entry in report.entries:
            self.assertEqual(entry.status, COPIED)
            self.assertEqual(entry.sha256_source, sources[entry.source])
            self.assertEqual(entry.sha256_destination, sources[entry.source])
        verification = verify_migration(self.source, self.provider, report)
        self.assertTrue(verification["ok"])
        self.assertEqual(verification["checked"], 3)

    def test_35_identical_rerun_skips_safely(self):
        first = run_migration(self.source, self.provider)
        self.assertEqual(first.counts["copied"], 3)
        mtimes = {
            p: p.stat().st_mtime_ns for p in self.store_root.rglob("*") if p.is_file()
        }
        second = run_migration(self.source, self.provider)
        self.assertEqual(second.counts["copied"], 0)
        self.assertEqual(second.counts["already_identical"], 3)
        self.assertEqual(second.counts["collision"], 0)
        after = {
            p: p.stat().st_mtime_ns for p in self.store_root.rglob("*") if p.is_file()
        }
        self.assertEqual(mtimes, after, "an identical re-run must not rewrite objects")

    def test_36_collision_is_reported_and_never_overwritten(self):
        run_migration(self.source, self.provider)
        # Somebody replaced the stored object with different bytes.
        self.provider.put_object("products/PF-A-1/primary.png", JPEG_BYTES)
        report = run_migration(self.source, self.provider)
        self.assertEqual(report.counts["collision"], 1)
        self.assertEqual(report.counts["already_identical"], 2)
        collision = [e for e in report.entries if e.status == COLLISION][0]
        self.assertEqual(collision.source, "products/PF-A-1/primary.png")
        self.assertEqual(
            self.provider.get_object("products/PF-A-1/primary.png"),
            JPEG_BYTES,
            "the existing object must be left untouched",
        )

    def test_37_one_failure_does_not_corrupt_the_others(self):
        # An unreadable source file must not stop or damage the rest.
        broken = self._write("products/PF-A-1/broken.png", PNG_BYTES)
        run_migration(self.source, self.provider)
        broken.chmod(0o000)
        try:
            report = run_migration(self.source, self.provider)
        finally:
            broken.chmod(0o644)
        statuses = {e.source: e.status for e in report.entries}
        self.assertEqual(statuses["products/PF-A-1/broken.png"], FAILED)
        self.assertEqual(statuses["products/PF-A-1/primary.png"], ALREADY_IDENTICAL)
        self.assertEqual(statuses["hero/hero001.jpg"], ALREADY_IDENTICAL)
        # The good objects are still byte-correct.
        self.assertEqual(
            self.provider.get_object("products/PF-A-1/primary.png"), PNG_BYTES
        )
        self.assertTrue(report.errors)

    def test_38_summary_counts_are_correct(self):
        # Add one unsupported file (a zip) and one empty file.
        self._write("products/PF-A-1/notes.zip", NOT_AN_IMAGE)
        self._write("products/PF-A-1/empty.png", b"")
        # 3 good + zip + empty
        report = run_migration(self.source, self.provider)
        counts = report.counts
        self.assertEqual(counts["total_source_files"], 5)
        self.assertEqual(counts["copied"], 3)
        self.assertEqual(counts["unsupported"] + counts["invalid"], 2)
        self.assertEqual(counts["failed"], 0)
        self.assertEqual(counts["collision"], 0)
        self.assertEqual(counts["source_bytes"], sum(
            p.stat().st_size for p in discover_source_files(self.source)
        ))
        summary = report.summary()
        self.assertEqual(summary["counts"]["total_source_files"], 5)
        self.assertEqual(summary["checksumVerification"], "passed")

    def test_38b_manifest_records_every_file_with_its_checksum(self):
        report = run_migration(self.source, self.provider)
        manifest = Path(self._tmp.name) / "manifest.json"
        report.write_manifest(manifest)
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "pratikshya.media.migration.v1")
        self.assertEqual(len(payload["entries"]), 3)
        for entry in payload["entries"]:
            self.assertTrue(entry["objectKey"])
            self.assertEqual(len(entry["sha256Source"]), 64)
            self.assertEqual(entry["sha256Source"], entry["sha256Destination"])
            self.assertTrue(entry["mimeType"].startswith("image/"))
        # The manifest must not leak the absolute source or storage path.
        text = manifest.read_text(encoding="utf-8")
        self.assertNotIn(str(self.source), text)
        self.assertNotIn(str(self.store_root), text)

    def test_object_key_convention_is_deterministic(self):
        path = self.source / "products" / "PF-A-1" / "primary.png"
        self.assertEqual(
            object_key_for_source_file(self.source, path), "products/PF-A-1/primary.png"
        )

    def test_extension_mismatch_is_recorded_but_still_migrated(self):
        # Mirrors the real library: 126 of 238 assets are .avif names holding
        # JPEG/PNG bytes. They must be copied unchanged, and reported.
        self._write("products/PF-A-1/mislabel.avif", JPEG_BYTES)
        report = run_migration(self.source, self.provider)
        entry = [e for e in report.entries if e.source.endswith("mislabel.avif")][0]
        self.assertEqual(entry.status, COPIED)
        self.assertEqual(entry.content_type, "image/jpeg")
        self.assertEqual(entry.extension_mismatch, "image/avif->image/jpeg")
        self.assertEqual(report.counts["extension_mismatch"], 1)
        # Served type follows the bytes, and the file was not converted.
        self.assertEqual(
            self.provider.get_metadata("products/PF-A-1/mislabel.avif").content_type,
            "image/jpeg",
        )
        self.assertEqual(
            self.provider.get_object("products/PF-A-1/mislabel.avif"), JPEG_BYTES
        )
        self.assertEqual(
            (self.source / "products/PF-A-1/mislabel.avif").read_bytes(), JPEG_BYTES
        )

    def test_interrupted_run_resumes_without_duplicate_corruption(self):
        # 3 files → limit 2 → rerun without the limit.
        partial = run_migration(self.source, self.provider, limit=2)
        self.assertEqual(partial.counts["copied"], 2)
        self.assertEqual(partial.counts["skipped"], 1)
        full = run_migration(self.source, self.provider)
        self.assertEqual(full.counts["copied"], 1)
        self.assertEqual(full.counts["already_identical"], 2)
        self.assertEqual(len(list(self.provider.list_objects())), 3)
        verification = verify_migration(self.source, self.provider, full)
        self.assertTrue(verification["ok"])

    def test_reverification_detects_source_drift(self):
        """The re-check hashes BOTH sides, so a moved source is reported."""
        report = run_migration(self.source, self.provider)
        self.assertTrue(verify_migration(self.source, self.provider, report)["ok"])
        (self.source / "hero" / "hero001.jpg").write_bytes(PNG_BYTES)
        verification = verify_migration(self.source, self.provider, report)
        self.assertFalse(verification["ok"])
        self.assertEqual(verification["sourceChanged"], ["hero/hero001.jpg"])
        # The stored object itself is still byte-correct.
        self.assertEqual(verification["mismatched"], [])

    def test_missing_source_root_is_reported_not_crashed(self):
        report = run_migration(self.base / "does-not-exist", self.provider)
        self.assertEqual(report.counts["total_source_files"], 0)
        self.assertTrue(report.errors)


class SourceIntegrityTests(MigrationTestCase):
    """35 of the phase contract — the source must survive untouched."""

    def _baseline_file(self) -> Path:
        target = Path(self._tmp.name) / "baseline.sha256"
        lines = [
            f"{digest}  {relative}" for relative, digest in sorted(self._source_hashes().items())
        ]
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return target

    def test_source_integrity_passes_after_a_migration(self):
        baseline = self._baseline_file()
        run_migration(self.source, self.provider)
        result = verify_source_integrity(self.source, baseline)
        self.assertTrue(result["ok"])
        self.assertEqual(result["baselineFileCount"], 3)
        self.assertEqual(result["currentFileCount"], 3)
        self.assertEqual(result["identical"], 3)
        self.assertEqual(result["changed"], [])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["added"], [])

    def test_source_integrity_detects_a_modified_source_file(self):
        baseline = self._baseline_file()
        (self.source / "hero" / "hero001.jpg").write_bytes(PNG_BYTES)
        result = verify_source_integrity(self.source, baseline)
        self.assertFalse(result["ok"])
        self.assertEqual(result["changed"], ["hero/hero001.jpg"])

    def test_source_integrity_tolerates_a_prefixed_baseline_path(self):
        target = Path(self._tmp.name) / "prefixed.sha256"
        lines = [
            f"{digest}  frontend/public/images/{relative}"
            for relative, digest in sorted(self._source_hashes().items())
        ]
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = verify_source_integrity(self.source, target)
        self.assertTrue(result["ok"])
        self.assertEqual(result["identical"], 3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
