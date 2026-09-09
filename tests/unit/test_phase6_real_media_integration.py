"""
Phase 6 — REAL-dataset media integration verification.

The Phase 6 fixture suite (`test_phase6_media_storage.py`) proves the media
machinery with synthetic in-memory bytes. This suite runs the SAME machinery
against the REAL migrated dataset — the 238 assets copied (copy-only) from
`frontend/public/images` into the configured local object store
(`LOCAL_MEDIA_ROOT`, i.e. `backend/storage/media`) — so the following are
proven on production bytes, not fixtures:

  1. local storage object lookup            (provider.exists / metadata / read)
  2. local media response                   (production media router, no patching)
  3. correct Content-Type                   (independent magic-byte table)
  4. byte integrity                         (store vs SOURCE, HTTP body vs SOURCE)
  5. missing-object behaviour               (404, no filesystem path leaked)
  6. product/media URL generation           (resolver + build_media_url)
  7. regression: the API NEVER falls back to `frontend/public/images` —
     with an empty store the same request 404s even though the file still
     exists under the frontend public folder.

Dataset expectations (Phase 6 migration audit)
----------------------------------------------
The 238 real assets are .avif / .webp files, of which 126 carry a mislabelled
extension (a `.avif` name holding JPEG or PNG bytes). They are copied
byte-identically — never renamed or converted — and the media route serves
the type the BYTES actually have. The expected Content-Type here is derived
from an independent magic-byte table written in THIS file, so a backend
sniffer regression cannot validate itself.

Skippable by design
-------------------
`backend/storage/media` is a runtime store (gitignored). On a fresh clone or
a CI runner without the migrated dataset the suite reports SKIP instead of
FAIL; in any environment where the migration has run (development machines,
this verification) every test executes.
"""

import filecmp
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.core.error_handlers import register_error_handlers
from app.api.v1.media import router as media_router
from app.storage import (
    LocalStorageProvider,
    ObjectNotFoundError,
    build_media_url,
    create_storage_provider,
    get_storage_provider,
)
from app.services.media.media_service import MediaService
from app.services.media.product_media_resolver import (
    LEGACY_FALLBACK,
    RESOLVED,
    candidate_object_key,
    clear_resolution_cache,
    explain,
)

# ---------------------------------------------------------------------------
# Real dataset locations (never mutated by this suite — read-only access)
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent

REAL_STORE = (BACKEND_DIR / settings.LOCAL_MEDIA_ROOT).resolve()
REAL_SOURCE = (BACKEND_DIR / settings.LOCAL_MEDIA_IMPORT_SOURCE).resolve()

STORE_READY = REAL_STORE.is_dir() and any(REAL_STORE.rglob("*"))
SOURCE_READY = REAL_SOURCE.is_dir() and any(REAL_SOURCE.rglob("*"))
DATASET_READY = STORE_READY and SOURCE_READY

#: The canonical, deterministic real test asset: the migrated primary image of
#: a real catalogue product (bridal / mehendi-haldi PF-BR-MEH-0001). Chosen
#: because the migration manifest records it and it exercises the interesting
#: mislabelled-extension case (.avif name, JPEG bytes). If a future dataset no
#: longer contains it, the first `products/**/primary.*` key is used instead.
CANONICAL_PRODUCT_KEY = (
    "products/bridal/celebrations/mehendi-haldi/PF-BR-MEH-0001/primary.avif"
)


def _object_files(root: Path):
    return sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith("."))


def pick_test_asset_key() -> str:
    """The real object key under test (canonical asset, or first product primary)."""
    preferred = REAL_STORE / CANONICAL_PRODUCT_KEY
    if preferred.is_file():
        return CANONICAL_PRODUCT_KEY
    for path in _object_files(REAL_STORE / "products") if (REAL_STORE / "products").is_dir() else []:
        if path.name.startswith("primary."):
            return path.relative_to(REAL_STORE).as_posix()
    first = _object_files(REAL_STORE)[0]
    return first.relative_to(REAL_STORE).as_posix()


# ---------------------------------------------------------------------------
# Independent content-signature table — deliberately NOT imported from
# app.storage.signatures, so a sniffer regression cannot validate itself.
# ---------------------------------------------------------------------------

def independent_mime_type(head: bytes) -> str:
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image/gif"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if len(head) >= 12 and head[4:8] == b"ftyp" and head[8:12] in (
        b"avif", b"avis", b"mif1", b"msf1",
    ):
        return "image/avif"
    return "application/octet-stream"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_path_for(key: str) -> Path:
    """The protected original under frontend/public/images for an object key."""
    return REAL_SOURCE / key


skip_no_dataset = unittest.skipUnless(
    DATASET_READY,
    f"real dataset not present (store={REAL_STORE.exists()}, "
    f"source={REAL_SOURCE.exists()}) — run the Phase 6 local media import first",
)


# ---------------------------------------------------------------------------
# 1–4. Provider lookup, media API response, Content-Type, byte integrity
# ---------------------------------------------------------------------------

@skip_no_dataset
class RealMediaEndToEndTests(unittest.TestCase):
    """One real migrated product image, the whole production path."""

    @classmethod
    def setUpClass(cls):
        clear_resolution_cache()
        cls.key = pick_test_asset_key()
        cls.store_path = REAL_STORE / cls.key
        cls.source_path = source_path_for(cls.key)
        # Production provider (settings-driven) — the same instance the live
        # server uses. No patching: this IS the production wiring.
        cls.provider = get_storage_provider()
        cls.service = MediaService(storage=cls.provider)

        # Production router + production error handlers, unpatched, so
        # GET /api/v1/media/objects/{key} runs the real code path.
        app = FastAPI()
        register_error_handlers(app)
        app.include_router(media_router, prefix=settings.API_V1_PREFIX)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        clear_resolution_cache()

    # -- 1. local storage object lookup --------------------------------------

    def test_01_real_object_is_located_by_the_local_provider(self):
        self.assertTrue(self.provider.object_exists(self.key))
        self.assertEqual(self.provider.name, "local")

    def test_02_provider_metadata_matches_the_source_asset(self):
        metadata = self.provider.get_metadata(self.key)
        self.assertEqual(metadata.key, self.key)
        self.assertEqual(metadata.size, self.source_path.stat().st_size)
        self.assertEqual(metadata.checksum_sha256, sha256_file(self.source_path))

    def test_03_provider_read_returns_the_source_bytes_exactly(self):
        self.assertEqual(self.provider.get_object(self.key), self.source_path.read_bytes())

    # -- 2/3/4. media API response, Content-Type, byte integrity -------------

    def test_04_media_api_serves_the_real_object_publicly(self):
        response = self.client.get(f"{settings.API_V1_PREFIX}/media/objects/{self.key}")
        self.assertEqual(response.status_code, 200)
        # Public product media must not demand authentication.
        self.assertNotIn("www-authenticate", {k.lower() for k in response.headers})
        self.assertNotIn("authorization", {k.lower() for k in response.headers})

    def test_05_media_api_content_type_is_the_true_type_of_the_bytes(self):
        response = self.client.get(f"{settings.API_V1_PREFIX}/media/objects/{self.key}")
        expected = independent_mime_type(self.source_path.read_bytes()[:64])
        self.assertNotEqual(expected, "application/octet-stream")
        self.assertEqual(response.headers["content-type"], expected)
        # And the metadata half agrees.
        metadata = self.provider.get_metadata(self.key)
        self.assertEqual(metadata.content_type, expected)

    def test_06_media_api_body_is_byte_identical_to_the_protected_source(self):
        response = self.client.get(f"{settings.API_V1_PREFIX}/media/objects/{self.key}")
        self.assertEqual(response.content, self.source_path.read_bytes())
        self.assertEqual(len(response.content), self.source_path.stat().st_size)
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), sha256_file(self.source_path))

    def test_07_media_api_headers_are_cache_friendly_and_safe(self):
        response = self.client.get(f"{settings.API_V1_PREFIX}/media/objects/{self.key}")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("etag", {k.lower() for k in response.headers})
        self.assertEqual(
            response.headers["etag"], f'"{sha256_file(self.store_path)[:32]}"'
        )
        self.assertEqual(response.headers["cache-control"], "public, max-age=3600")

    def test_08_head_request_reports_size_and_type_without_a_body(self):
        response = self.client.head(f"{settings.API_V1_PREFIX}/media/objects/{self.key}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(int(response.headers["content-length"]), self.source_path.stat().st_size)
        self.assertEqual(response.headers["content-type"], independent_mime_type(self.source_path.read_bytes()[:64]))

    def test_09_object_meta_endpoint_reports_the_real_checksum(self):
        response = self.client.get(f"{settings.API_V1_PREFIX}/media/object-meta/{self.key}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["object"]["checksumSha256"], sha256_file(self.source_path))
        self.assertEqual(payload["object"]["size"], self.source_path.stat().st_size)
        self.assertEqual(
            payload["url"],
            f"{settings.API_V1_PREFIX}/media/objects/{self.key}",
        )

    # -- 5. missing-object behaviour ------------------------------------------

    def test_10_missing_object_is_a_clean_404_that_leaks_no_path(self):
        response = self.client.get(
            f"{settings.API_V1_PREFIX}/media/objects/products/PF-DOES-NOT-EXIST/primary.avif"
        )
        self.assertEqual(response.status_code, 404)
        body = response.text
        self.assertNotIn(str(REAL_STORE), body)
        self.assertNotIn("storage/media", body)
        self.assertNotIn("frontend", body)

    def test_11_provider_raises_object_not_found_for_a_missing_real_key(self):
        with self.assertRaises(ObjectNotFoundError):
            self.provider.get_object("products/PF-DOES-NOT-EXIST/primary.avif")

    def test_12_traversal_is_rejected_before_any_io(self):
        # Encoded so the HTTP client cannot silently normalise it away —
        # the same client-side trick a real attacker would use.
        response = self.client.get(
            f"{settings.API_V1_PREFIX}/media/objects/products/%2e%2e/%2e%2e/.env"
        )
        self.assertEqual(response.status_code, 422)

    # -- 6. product/media URL generation --------------------------------------

    def test_13_real_legacy_reference_resolves_to_the_canonical_media_url(self):
        reference = f"/images/{self.key}"
        decision = explain(reference, storage=self.provider)
        self.assertEqual(decision["status"], RESOLVED)
        self.assertEqual(decision["objectKey"], self.key)
        self.assertEqual(
            decision["url"], f"{settings.API_V1_PREFIX}/media/objects/{self.key}"
        )

    def test_14_generated_url_is_application_level_and_fetchable(self):
        url = self.provider.url_for(self.key)
        self.assertTrue(url.startswith(f"{settings.API_V1_PREFIX}/media/objects/"))
        self.assertNotIn(str(REAL_STORE), url)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.source_path.read_bytes())

    def test_15_every_migrated_product_reference_resolves(self):
        """Dual-read complete: no real product asset is stuck on legacy-fallback."""
        product_keys = [
            p.relative_to(REAL_STORE).as_posix()
            for p in _object_files(REAL_STORE / "products")
            if (REAL_STORE / "products").is_dir()
        ]
        self.assertTrue(product_keys)
        unresolved = []
        for key in product_keys:
            decision = explain(f"/images/{key}", storage=self.provider)
            if decision["status"] != RESOLVED:
                unresolved.append((key, decision["status"]))
        self.assertEqual(unresolved, [])


# ---------------------------------------------------------------------------
# Dataset integrity — the copy is complete, byte-identical and untransformed
# ---------------------------------------------------------------------------

@skip_no_dataset
class RealDatasetIntegrityTests(unittest.TestCase):
    """Every stored object matches its protected source, byte for byte."""

    @classmethod
    def setUpClass(cls):
        clear_resolution_cache()
        cls.provider = get_storage_provider()

    @classmethod
    def tearDownClass(cls):
        clear_resolution_cache()

    def test_20_store_and_source_hold_the_same_file_population(self):
        source_files = _object_files(REAL_SOURCE)
        store_files = _object_files(REAL_STORE)
        source_keys = {p.relative_to(REAL_SOURCE).as_posix() for p in source_files}
        store_keys = {p.relative_to(REAL_STORE).as_posix() for p in store_files}
        self.assertEqual(source_keys, store_keys)
        # The Phase 6 migrated dataset is 238 assets.
        self.assertEqual(len(store_keys), 238)

    def test_21_every_object_is_byte_identical_to_its_source(self):
        mismatched = []
        for store_path in _object_files(REAL_STORE):
            key = store_path.relative_to(REAL_STORE).as_posix()
            if not filecmp.cmp(store_path, REAL_SOURCE / key, shallow=False):
                mismatched.append(key)
        self.assertEqual(mismatched, [])

    def test_22_served_content_type_matches_independent_magic_bytes_for_every_object(self):
        wrong = []
        for store_path in _object_files(REAL_STORE):
            key = store_path.relative_to(REAL_STORE).as_posix()
            expected = independent_mime_type((REAL_SOURCE / key).read_bytes()[:64])
            self.assertNotEqual(expected, "application/octet-stream", key)
            actual = self.provider.get_metadata(key).content_type
            if actual != expected:
                wrong.append((key, expected, actual))
        self.assertEqual(wrong, [])

    def test_23_no_object_was_transformed_during_migration(self):
        """Extension/content pairs survive untouched (126 known mislabelled assets stay)."""
        mislabelled = []
        for store_path in _object_files(REAL_STORE):
            key = store_path.relative_to(REAL_STORE).as_posix()
            source_bytes = (REAL_SOURCE / key).read_bytes()[:64]
            extension = key.rsplit(".", 1)[-1].lower()
            expected = independent_mime_type(source_bytes)
            extension_type = {
                "avif": "image/avif", "webp": "image/webp", "jpg": "image/jpeg",
                "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
            }.get(extension)
            if extension_type and extension_type != expected:
                mislabelled.append(key)
        # The audited dataset carries exactly 126 mislabelled assets, copied
        # unchanged. They must still be mislabelled — renaming would be a
        # violation of the asset rule.
        self.assertEqual(len(mislabelled), 126)
        mislabelled_again = [k for k in mislabelled if filecmp.cmp(REAL_STORE / k, REAL_SOURCE / k, shallow=False)]
        self.assertEqual(mislabelled_again, mislabelled)

    def test_24_sampling_real_assets_through_http_confers_byte_integrity(self):
        """HTTP bodies byte-match the source for one asset of each real byte type."""
        wanted = {"image/jpeg": None, "image/avif": None, "image/webp": None, "image/png": None}
        for store_path in _object_files(REAL_STORE):
            mime = independent_mime_type(store_path.read_bytes()[:64])
            if mime in wanted and wanted[mime] is None:
                wanted[mime] = store_path.relative_to(REAL_STORE).as_posix()
        sampled = {mime: key for mime, key in wanted.items() if key}
        self.assertGreaterEqual(len(sampled), 2)

        app = FastAPI()
        register_error_handlers(app)
        app.include_router(media_router, prefix=settings.API_V1_PREFIX)
        client = TestClient(app)

        for mime, key in sorted(sampled.items()):
            response = client.get(f"{settings.API_V1_PREFIX}/media/objects/{key}")
            self.assertEqual(response.status_code, 200, key)
            self.assertEqual(response.headers["content-type"], mime, key)
            self.assertEqual(response.content, (REAL_SOURCE / key).read_bytes(), key)


# ---------------------------------------------------------------------------
# 7. Regression — the API must never fall back to frontend/public/images
# ---------------------------------------------------------------------------

@skip_no_dataset
class NoFrontendPublicFallbackTests(unittest.TestCase):
    """
    Product media is served from the CONFIGURED OBJECT STORE only.

    `frontend/public/images` is a read-only migration source; it is not a
    backend fallback. With the store empty, the exact same request must 404 —
    even though the bytes still exist under the frontend public folder.
    """

    def setUp(self):
        clear_resolution_cache()
        self.key = pick_test_asset_key()
        self.source_path = source_path_for(self.key)
        self.assertTrue(self.source_path.is_file())

    def tearDown(self):
        clear_resolution_cache()

    def test_30_empty_store_yields_404_even_though_the_public_asset_exists(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            empty_provider = LocalStorageProvider(Path(tmp))
            # The file the request names still exists in frontend/public/images.
            self.assertTrue((REAL_SOURCE / self.key).is_file())
            self.assertFalse(empty_provider.object_exists(self.key))

            app = FastAPI()
            register_error_handlers(app)
            app.include_router(media_router, prefix=settings.API_V1_PREFIX)
            with patch(
                "app.api.v1.media._get_media_service",
                return_value=MediaService(storage=empty_provider),
            ):
                client = TestClient(app)
                response = client.get(f"{settings.API_V1_PREFIX}/media/objects/{self.key}")
            self.assertEqual(response.status_code, 404)

    def test_31_resolver_with_an_empty_store_keeps_the_legacy_path(self):
        """Dual-read contract: unresolved references stay untouched, never invented."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            empty_provider = LocalStorageProvider(Path(tmp))
            decision = explain(f"/images/{self.key}", storage=empty_provider)
            self.assertEqual(decision["status"], LEGACY_FALLBACK)
            self.assertEqual(decision["url"], f"/images/{self.key}")
            self.assertEqual(decision["objectKey"], self.key)

    def test_32_key_mapping_is_provider_independent(self):
        self.assertEqual(candidate_object_key(f"/images/{self.key}"), self.key)
        self.assertEqual(
            build_media_url(self.key),
            f"{settings.API_V1_PREFIX}/media/objects/{self.key}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
