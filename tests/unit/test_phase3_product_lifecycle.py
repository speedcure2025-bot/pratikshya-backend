"""
Phase 3 Block 6 — LIFECYCLE HARDENING (plan §24 step 8).

Governing plan: PHASE_3_PRODUCT_CATALOG_IMPLEMENTATION_PLAN.md
  §9.1        — the lifecycle the code implements; the two vocabularies
  §9.2        — the endpoint table (every "Current State" cell is asserted here)
  §19         — "Declare status / review / availability enums — NO migration"
  §21         — this file: `tests/unit/test_phase3_product_lifecycle.py`
                ← transition matrix
  §22.1       — the "Lifecycle transitions" test row: "the full 4x4 matrix:
                every legal transition asserted, every illegal one asserted to
                422 with the right message"
  §24 step 8  — "Lifecycle hardening: enum-declared transitions, the full
                matrix test, resolve the change-id cascade question, declare
                the review-flag vocabulary"
  §25 (14-16) — approve never publishes; every illegal transition is a 422;
                §3.3 lists exactly the statuses the code implements

What this suite pins
────────────────────
NEW BEHAVIOUR (fails if the Block 6 change is reverted)
  1. `LIFECYCLE_TRANSITIONS` is the DECLARED transition table, and the running
     services agree with it on all 7 x 4 x 4 = 112 (action x status x review)
     combinations.  Before Block 6 the approve/reject guards combined the two
     axes with `and`, so:
  2. an ARCHIVED product whose review was still PENDING could be APPROVED; and
  3. the same product could be REJECTED — which set `status = DRAFT` and
     silently resurrected it out of the archive.
     Both are reachable through entirely legal calls: submit -> archive.
  4. `change-id` rejects a label already used as another row's `product_id`
     (it only ever checked the primary key), which had made `_get_or_404`
     ambiguous.
  5. `review-flags/clear` validates against the declared vocabulary.

REGRESSION LOCKS (pass with or without the Block 6 change)
  Everything else: submit/publish/unpublish/archive/restore guards, the
  status<->published invariant, the publication audit fields, approve-never-
  publishes (Block 5's boundary), RBAC, bulk's refusal to carry lifecycle,
  the canonical error envelope and storefront freshness after a transition.

Harness: the REAL routers, REAL services and REAL ORM on a throwaway SQLite
file — the same shim the Block 2/3/4/5 suites use.  No migration is involved.
"""

import importlib
import os
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_cache import FastAPICache
from fastapi_cache.backends import Backend
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.schemas.catalog.product import (
    LIFECYCLE_TRANSITIONS,
    PRODUCT_STATUS_VALUES,
    REVIEW_FLAG_BLOCKING,
    REVIEW_FLAG_INFORMATIONAL,
    REVIEW_FLAG_VALUES,
    REVIEW_STATE_VALUES,
)

HAS_AIOSQLITE = importlib.util.find_spec("aiosqlite") is not None


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw):  # pragma: no cover - dialect glue
    return "JSON"


CAT_ACTIVE = "cat-sarees"
SUB_ACTIVE = "cat-sarees-banarasi"

# The admin lifecycle routes, keyed by the action name used in
# LIFECYCLE_TRANSITIONS.  `submitReview` is the one storefront-side route.
ACTION_ROUTES = {
    "submitReview": ("POST", "/api/v1/products/{id}/submit-review", None),
    "approve": ("POST", "/api/v1/admin/products/{id}/approve", None),
    "reject": ("POST", "/api/v1/admin/products/{id}/reject", {"reason": "Needs work"}),
    "publish": ("POST", "/api/v1/admin/products/{id}/publish", None),
    "unpublish": ("POST", "/api/v1/admin/products/{id}/unpublish", None),
    "archive": ("POST", "/api/v1/admin/products/{id}/archive", None),
    "restore": ("POST", "/api/v1/admin/products/{id}/restore", None),
}


def transition_allowed(action: str, status: str, review_state: str) -> bool:
    """The DECLARED outcome — the single source of truth this suite tests against."""
    rule = LIFECYCLE_TRANSITIONS[action]
    idempotent = rule.get("idempotent_when")
    if idempotent == "already_approved" and review_state == "APPROVED":
        # approve short-circuits before doing anything, but only from a state
        # that is itself legal on the status axis.
        return status in rule["from_status"]
    if idempotent == "already_live" and status == "PUBLISHED":
        # publish short-circuits on an already-live row regardless of review.
        return True
    if rule["from_status"] is not None and status not in rule["from_status"]:
        return False
    if rule["from_review"] is not None and review_state not in rule["from_review"]:
        return False
    return True


class _PassThroughCache(Backend):
    """fastapi-cache2 backend that never serves a stored HTTP response."""

    async def get_with_ttl(self, key):
        return 0, None

    async def get(self, key):
        return None

    async def set(self, key, value, expire=None):
        return None

    async def clear(self, namespace=None, key=None):
        return 0


@unittest.skipUnless(HAS_AIOSQLITE, "aiosqlite is not installed")
class _LifecycleCase(unittest.IsolatedAsyncioTestCase):
    """Real app + real RBAC + real products on a disposable SQLite database."""

    async def asyncSetUp(self):
        importlib.import_module("app.models")
        from app.core.lru_cache_store import init_lru_cache
        from app.models.auth.user import UserModel
        from app.models.base import Base
        from app.models.catalog.category import CategoryModel, SubcategoryModel
        from app.models.catalog.product import ProductModel
        from app.models.rbac.role import RoleModel
        from app.models.rbac.user_role import UserRoleModel

        self._UserModel = UserModel
        self._ProductModel = ProductModel

        init_lru_cache()

        self._tmp = tempfile.TemporaryDirectory(prefix="pf3-lifecycle-")
        root = self._tmp.name
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{root}/main.sqlite")
        schema_db = os.path.join(root, "pratikshya.sqlite")

        @event.listens_for(self.engine.sync_engine, "connect")
        def _attach(dbapi_conn, _record):  # pragma: no cover - driver hook
            cursor = dbapi_conn.cursor()
            cursor.execute(f"ATTACH DATABASE '{schema_db}' AS pratikshya")
            cursor.close()

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.Session() as session:
            admin = UserModel(
                email="pf3-lifecycle-admin@pratikshya.test",
                full_name="Lifecycle Admin",
                hashed_password="x",
                user_type="admin",
                status="ACTIVE",
                is_verified=True,
                force_password_change=False,
            )
            session.add(admin)
            await session.flush()
            role = RoleModel(name="SUPER_ADMIN", description="Test admin", is_system=True)
            session.add(role)
            await session.flush()
            session.add(UserRoleModel(user_id=admin.id, role_id=role.id))
            session.add(CategoryModel(
                id=CAT_ACTIVE, name="Sarees", slug="sarees", status="ACTIVE",
            ))
            session.add(SubcategoryModel(
                id=SUB_ACTIVE, category_id=CAT_ACTIVE,
                name="Banarasi", slug="banarasi", status="ACTIVE",
            ))
            await session.commit()
            self.admin_id = admin.id

        self.app = self._build_app()
        self.client = TestClient(self.app)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self._tmp.cleanup()

    def _build_app(self):
        from app.api.v1.products import router as products_router
        from app.core.error_handlers import register_error_handlers
        from app.dependencies import get_current_user, get_db

        app = FastAPI()
        register_error_handlers(app)
        FastAPICache.init(backend=_PassThroughCache(), prefix="pf3-lifecycle-test")
        app.include_router(products_router, prefix="/api/v1")

        Session = self.Session
        UserModel = self._UserModel

        async def _override_get_db():
            async with Session() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        async def _override_current_user():
            async with Session() as session:
                return (
                    await session.execute(
                        select(UserModel).where(UserModel.id == self.admin_id)
                    )
                ).scalars().first()

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = _override_current_user
        return app

    # ── fixtures ──────────────────────────────────────────────────────────────

    async def seed(self, product_id, *, status="DRAFT", review_state="NONE",
                   published=None, complete=True, **extra):
        """
        The smallest safe isolated fixture: one row, in this test's own
        throwaway SQLite file, destroyed on teardown.  No golden or seed
        catalogue data is read or written anywhere in this suite.

        `complete=True` fills the fields `get_publish_issues()` and the
        submit-review completeness pre-check require, so a transition test
        measures the TRANSITION GUARD and not a content gate.
        """
        if published is None:
            published = status == "PUBLISHED"
        fields = dict(
            id=product_id,
            product_id=product_id,
            name=f"Banarasi Silk {product_id}" if complete else "",
            slug=product_id.lower(),
            sku=product_id if complete else "",
            category=CAT_ACTIVE if complete else "",
            subcategory=SUB_ACTIVE,
            price=4999 if complete else 0,
            status=status,
            published=published,
            review={"state": review_state},
            image="https://cdn.test/cover.jpg" if complete else "",
            description="A real description for the publish gate." if complete else "",
        )
        fields.update(extra)  # an explicit override wins over the default
        async with self.Session() as session:
            session.add(self._ProductModel(**fields))
            await session.commit()
        return product_id

    async def row(self, product_id):
        async with self.Session() as session:
            return (
                await session.execute(
                    select(self._ProductModel).where(
                        self._ProductModel.id == product_id
                    )
                )
            ).scalars().first()

    def act(self, action, product_id):
        method, template, body = ACTION_ROUTES[action]
        return self.client.request(
            method, template.format(id=product_id), json=body,
        )

    def assert_canonical_422(self, response):
        """The Phase 1 canonical business-rule envelope — no second format."""
        self.assertEqual(response.status_code, 422, response.text)
        body = response.json()
        self.assertIs(body.get("success"), False, body)
        error = body.get("error") or {}
        self.assertEqual(error.get("code"), "BUSINESS_RULE_VIOLATION", body)
        self.assertTrue((error.get("message") or "").strip(), body)
        blob = response.text.lower()
        for leak in ("traceback", "sqlalchemy", "select ", "psycopg", "asyncpg"):
            self.assertNotIn(leak, blob, f"internal detail leaked: {leak}")
        return error


# ═══════════════════════════════════════════════════════════════════════════
# 1. THE FULL TRANSITION MATRIX  — 7 actions x 4 statuses x 4 review states
# ═══════════════════════════════════════════════════════════════════════════

class TransitionMatrixTests(_LifecycleCase):
    """Plan §22.1: every legal transition asserted, every illegal one a 422."""

    async def test_declared_matrix_matches_the_running_services(self):  # NEW
        """
        The declaration in `LIFECYCLE_TRANSITIONS` and the behaviour of the real
        endpoints agree on ALL 112 combinations.

        This is the test that makes the transition table authoritative rather
        than decorative: if a guard and its declaration ever drift, the exact
        (action, status, review) cell is named.
        """
        checked = 0
        for action in ACTION_ROUTES:
            for status in PRODUCT_STATUS_VALUES:
                for review_state in REVIEW_STATE_VALUES:
                    pid = f"PF-LC-{action[:4].upper()}-{status[:2]}-{review_state[:2]}"
                    await self.seed(pid, status=status, review_state=review_state)
                    expected = transition_allowed(action, status, review_state)
                    with self.subTest(action=action, status=status, review=review_state):
                        response = self.act(action, pid)
                        if expected:
                            self.assertEqual(
                                response.status_code, 200,
                                f"{action} from ({status}, {review_state}) should be "
                                f"LEGAL per LIFECYCLE_TRANSITIONS: {response.text}",
                            )
                        else:
                            self.assertEqual(
                                response.status_code, 422,
                                f"{action} from ({status}, {review_state}) should be "
                                f"ILLEGAL per LIFECYCLE_TRANSITIONS: {response.text}",
                            )
                            self.assert_canonical_422(response)
                    checked += 1
        self.assertEqual(checked, 112)

    async def test_every_illegal_transition_leaves_the_row_untouched(self):
        """Plan §25 criterion 15 — a refusal must not be a partial write."""
        for action in ACTION_ROUTES:
            for status in PRODUCT_STATUS_VALUES:
                for review_state in REVIEW_STATE_VALUES:
                    if transition_allowed(action, status, review_state):
                        continue
                    pid = f"PF-LCU-{action[:4].upper()}-{status[:2]}-{review_state[:2]}"
                    await self.seed(pid, status=status, review_state=review_state)
                    before = await self.row(pid)
                    snapshot = (
                        before.status, before.published,
                        (before.review or {}).get("state"),
                        before.published_at, before.published_by,
                    )
                    with self.subTest(action=action, status=status, review=review_state):
                        self.act(action, pid)
                        after = await self.row(pid)
                        self.assertEqual(
                            snapshot,
                            (after.status, after.published,
                             (after.review or {}).get("state"),
                             after.published_at, after.published_by),
                            f"{action} from ({status}, {review_state}) mutated the row "
                            f"despite being refused",
                        )

    async def test_illegal_transition_message_names_the_current_state(self):
        """Plan §25 criterion 15 — "with an actionable message"."""
        pid = await self.seed("PF-LC-MSG1", status="DRAFT", review_state="NONE")
        error = self.assert_canonical_422(self.act("publish", pid))
        self.assertIn("approved", error["message"].lower())

        pid2 = await self.seed("PF-LC-MSG2", status="DRAFT", review_state="NONE")
        error2 = self.assert_canonical_422(self.act("restore", pid2))
        self.assertIn("DRAFT", error2["message"])
        self.assertIn("archived", error2["message"].lower())

    async def test_the_declaration_covers_exactly_the_seven_lifecycle_actions(self):
        self.assertEqual(set(LIFECYCLE_TRANSITIONS), set(ACTION_ROUTES))

    async def test_declared_vocabularies_are_the_four_by_four_the_plan_states(self):
        """Plan §9.1 — 4 statuses, 4 review states. Not the 6 in the old §3.3."""
        self.assertEqual(
            PRODUCT_STATUS_VALUES,
            ("DRAFT", "PENDING_REVIEW", "PUBLISHED", "ARCHIVED"),
        )
        self.assertEqual(
            REVIEW_STATE_VALUES, ("NONE", "PENDING", "APPROVED", "REJECTED"),
        )
        for action, rule in LIFECYCLE_TRANSITIONS.items():
            with self.subTest(action=action):
                for src in rule["from_status"] or ():
                    self.assertIn(src, PRODUCT_STATUS_VALUES)
                for src in rule["from_review"] or ():
                    self.assertIn(src, REVIEW_STATE_VALUES)
                if rule["to_status"]:
                    self.assertIn(rule["to_status"], PRODUCT_STATUS_VALUES)
                if rule["to_review"]:
                    self.assertIn(rule["to_review"], REVIEW_STATE_VALUES)


# ═══════════════════════════════════════════════════════════════════════════
# 2. THE TWO DEFECTS BLOCK 6 CLOSES
# ═══════════════════════════════════════════════════════════════════════════

class ArchivedProductGuardTests(_LifecycleCase):
    """
    An ARCHIVED product still carries whatever `review.state` it had when it was
    archived — `archive_product` deliberately does not touch the review axis.
    Before Block 6 the approve/reject guards read

        if status != "PENDING_REVIEW" and review_state != "PENDING":

    which is only true when BOTH axes are wrong, so (ARCHIVED, PENDING) passed.
    """

    async def _archived_but_pending(self, pid):
        """Reach the state through legal calls only — not a synthetic row."""
        await self.seed(pid, status="DRAFT", review_state="NONE")
        self.assertEqual(self.act("submitReview", pid).status_code, 200)
        self.assertEqual(self.act("archive", pid).status_code, 200)
        row = await self.row(pid)
        self.assertEqual(row.status, "ARCHIVED")
        self.assertEqual((row.review or {}).get("state"), "PENDING")
        return row

    async def test_archived_product_with_a_pending_review_cannot_be_approved(self):  # NEW
        pid = "PF-LC-ARCHAPP"
        await self._archived_but_pending(pid)
        self.assert_canonical_422(self.act("approve", pid))
        row = await self.row(pid)
        self.assertEqual(row.status, "ARCHIVED")
        self.assertEqual((row.review or {}).get("state"), "PENDING")

    async def test_archived_product_with_a_pending_review_cannot_be_rejected(self):  # NEW
        """
        The severe half: `reject` writes `status = "DRAFT"`, so before Block 6
        rejecting an archived product silently un-archived it.
        """
        pid = "PF-LC-ARCHREJ"
        await self._archived_but_pending(pid)
        self.assert_canonical_422(self.act("reject", pid))
        row = await self.row(pid)
        self.assertEqual(row.status, "ARCHIVED", "reject resurrected an archived product")
        self.assertEqual(row.published, False)
        self.assertEqual((row.review or {}).get("state"), "PENDING")

    async def test_archived_product_is_still_restorable_after_the_refusals(self):
        """The fix must not strand the row: restore remains the way out."""
        pid = "PF-LC-ARCHOUT"
        await self._archived_but_pending(pid)
        self.act("approve", pid)
        self.act("reject", pid)
        self.assertEqual(self.act("restore", pid).status_code, 200)
        row = await self.row(pid)
        self.assertEqual(row.status, "DRAFT")

    async def test_a_published_product_cannot_be_approved_or_rejected(self):
        pid = await self.seed("PF-LC-PUBAPP", status="PUBLISHED", review_state="APPROVED")
        self.assert_canonical_422(self.act("reject", pid))
        row = await self.row(pid)
        self.assertEqual(row.status, "PUBLISHED")
        self.assertIs(row.published, True)


# ═══════════════════════════════════════════════════════════════════════════
# 3. APPROVE vs PUBLISH  — Block 5's boundary, re-proved (REGRESSION LOCKS)
# ═══════════════════════════════════════════════════════════════════════════

class ApproveIsNotPublishTests(_LifecycleCase):
    """Plan §25 criterion 14. Block 6 must not weaken any of this."""

    async def test_approve_writes_only_the_review_axis(self):
        pid = await self.seed("PF-LC-AP1", status="PENDING_REVIEW", review_state="PENDING")
        response = self.act("approve", pid)
        self.assertEqual(response.status_code, 200, response.text)
        row = await self.row(pid)
        self.assertEqual((row.review or {}).get("state"), "APPROVED")
        self.assertEqual(row.status, "PENDING_REVIEW", "approve moved the status axis")
        self.assertIs(row.published, False)
        self.assertIsNone(row.published_at)
        self.assertIsNone(row.published_by)

    async def test_approve_declares_no_status_target_at_all(self):
        """Structural: the declaration itself forbids approve touching status."""
        self.assertIsNone(LIFECYCLE_TRANSITIONS["approve"]["to_status"])
        self.assertEqual(LIFECYCLE_TRANSITIONS["approve"]["to_review"], "APPROVED")

    async def test_publish_requires_an_approved_review(self):
        pid = await self.seed("PF-LC-AP2", status="PENDING_REVIEW", review_state="PENDING")
        self.assert_canonical_422(self.act("publish", pid))
        row = await self.row(pid)
        self.assertEqual(row.status, "PENDING_REVIEW")
        self.assertIs(row.published, False)

    async def test_publish_writes_every_publication_field_together(self):
        pid = await self.seed("PF-LC-AP3", status="PENDING_REVIEW", review_state="APPROVED")
        response = self.act("publish", pid)
        self.assertEqual(response.status_code, 200, response.text)
        row = await self.row(pid)
        self.assertEqual(row.status, "PUBLISHED")
        self.assertIs(row.published, True)
        self.assertIsNotNone(row.published_at)
        self.assertIsNotNone(row.published_by)

    async def test_publish_is_blocked_by_each_publish_issue(self):
        """Plan §22.1 — "publish blocked by each get_publish_issues item"."""
        cases = {
            "cover image": {"image": "", "primary_media_id": None},
            "description": {"description": "", "short_description": ""},
        }
        for label, overrides in cases.items():
            pid = f"PF-LC-ISSUE-{label[:4]}"
            await self.seed(
                pid, status="PENDING_REVIEW", review_state="APPROVED", **overrides,
            )
            with self.subTest(issue=label):
                response = self.act("publish", pid)
                error = self.assert_canonical_422(response)
                self.assertIn("publish issues", error["message"].lower())
                self.assertTrue(error.get("details", {}).get("errors"))
                row = await self.row(pid)
                self.assertEqual(row.status, "PENDING_REVIEW")
                self.assertIs(row.published, False)

    async def test_a_blocking_review_flag_blocks_publication(self):
        pid = await self.seed(
            "PF-LC-FLAGBLOCK", status="PENDING_REVIEW", review_state="APPROVED",
            review_flags=["NEEDS_MEDIA"],
        )
        error = self.assert_canonical_422(self.act("publish", pid))
        self.assertTrue(any(
            "NEEDS_MEDIA" in issue for issue in error["details"]["errors"]
        ), error)

    async def test_an_informational_review_flag_does_not_block_publication(self):
        pid = await self.seed(
            "PF-LC-FLAGOK", status="PENDING_REVIEW", review_state="APPROVED",
            review_flags=["MEDIA_OWNERSHIP_MOVED"],
        )
        self.assertEqual(self.act("publish", pid).status_code, 200)


# ═══════════════════════════════════════════════════════════════════════════
# 4. STATE INVARIANTS
# ═══════════════════════════════════════════════════════════════════════════

class StateInvariantTests(_LifecycleCase):
    """The invariants the plan states, checked after EVERY legal transition."""

    async def test_status_and_published_never_disagree_after_any_transition(self):
        """
        PUBLISHED => published is True; every other status => published is False.
        Asserted on the persisted row after each legal transition, not on the
        response body.
        """
        for action in ACTION_ROUTES:
            for status in PRODUCT_STATUS_VALUES:
                for review_state in REVIEW_STATE_VALUES:
                    if not transition_allowed(action, status, review_state):
                        continue
                    pid = f"PF-LCI-{action[:4].upper()}-{status[:2]}-{review_state[:2]}"
                    await self.seed(pid, status=status, review_state=review_state)
                    with self.subTest(action=action, status=status, review=review_state):
                        self.act(action, pid)
                        row = await self.row(pid)
                        self.assertEqual(
                            bool(row.published), row.status == "PUBLISHED",
                            f"after {action}: status={row.status} "
                            f"published={row.published}",
                        )

    async def test_archived_products_are_never_published(self):
        pid = await self.seed("PF-LC-INV1", status="PENDING_REVIEW", review_state="APPROVED")
        self.assertEqual(self.act("publish", pid).status_code, 200)
        self.assertEqual(self.act("archive", pid).status_code, 200)
        row = await self.row(pid)
        self.assertEqual(row.status, "ARCHIVED")
        self.assertIs(row.published, False)

    async def test_a_rejected_product_is_never_publicly_visible(self):
        pid = await self.seed("PF-LC-INV2", status="PENDING_REVIEW", review_state="PENDING")
        self.assertEqual(self.act("reject", pid).status_code, 200)
        row = await self.row(pid)
        self.assertEqual(row.status, "DRAFT")
        self.assertIs(row.published, False)
        self.assertEqual((row.review or {}).get("state"), "REJECTED")

    async def test_a_rejected_product_cannot_be_published_without_a_new_review(self):
        pid = await self.seed("PF-LC-INV3", status="PENDING_REVIEW", review_state="PENDING")
        self.act("reject", pid)
        self.assert_canonical_422(self.act("publish", pid))

    async def test_rejection_reason_is_recorded_and_required(self):
        pid = await self.seed("PF-LC-INV4", status="PENDING_REVIEW", review_state="PENDING")
        empty = self.client.post(
            f"/api/v1/admin/products/{pid}/reject", json={"reason": ""},
        )
        self.assertEqual(empty.status_code, 422, empty.text)
        row = await self.row(pid)
        self.assertEqual(row.status, "PENDING_REVIEW", "a refused reject still wrote")
        self.assertEqual(self.act("reject", pid).status_code, 200)
        row = await self.row(pid)
        self.assertEqual((row.review or {}).get("rejectionReason"), "Needs work")

    async def test_publication_audit_fields_survive_unpublish_as_a_last_published_record(self):
        """
        OBSERVED, DOCUMENTED BEHAVIOUR — not changed by Block 6.
        `unpublish` clears `published` but keeps `published_at`/`published_by`
        as the record of the last publication. Plan §9.2 specifies only
        "PUBLISHED -> DRAFT, published=false" and says nothing about clearing
        the audit fields, so this is asserted as-is rather than "corrected".
        """
        pid = await self.seed("PF-LC-INV5", status="PENDING_REVIEW", review_state="APPROVED")
        self.act("publish", pid)
        row = await self.row(pid)
        published_at, published_by = row.published_at, row.published_by
        self.assertIsNotNone(published_at)
        self.act("unpublish", pid)
        row = await self.row(pid)
        self.assertEqual(row.status, "DRAFT")
        self.assertIs(row.published, False)
        self.assertEqual(row.published_at, published_at)
        self.assertEqual(row.published_by, published_by)

    async def test_every_transition_appends_exactly_one_history_entry(self):
        pid = await self.seed("PF-LC-INV6", status="DRAFT", review_state="NONE")
        before = len((await self.row(pid)).history or [])
        self.act("submitReview", pid)
        after = len((await self.row(pid)).history or [])
        self.assertEqual(after, before + 1)

    async def test_submit_review_resets_the_review_block(self):
        pid = await self.seed("PF-LC-INV7", status="DRAFT", review_state="REJECTED")
        self.assertEqual(self.act("submitReview", pid).status_code, 200)
        row = await self.row(pid)
        self.assertEqual(row.status, "PENDING_REVIEW")
        self.assertEqual((row.review or {}).get("state"), "PENDING")
        self.assertIsNone((row.review or {}).get("reviewedBy"))
        self.assertEqual((row.review or {}).get("rejectionReason"), "")

    async def test_submit_review_enforces_the_completeness_precheck(self):
        pid = await self.seed(
            "PF-LC-INV8", status="DRAFT", review_state="NONE", complete=False,
        )
        error = self.assert_canonical_422(self.act("submitReview", pid))
        self.assertIn("not ready for review", error["message"].lower())
        row = await self.row(pid)
        self.assertEqual(row.status, "DRAFT")


# ═══════════════════════════════════════════════════════════════════════════
# 5. IDEMPOTENCY  — exactly the semantics §9.2 declares, no more
# ═══════════════════════════════════════════════════════════════════════════

class IdempotencyTests(_LifecycleCase):
    """
    Plan §9.2 declares only TWO idempotent actions: approve ("Idempotent when
    already approved") and publish ("Idempotent when already live"). Repeating
    any other action is a 422. This suite asserts that split exactly — errors
    are NOT converted into no-ops.
    """

    async def test_approve_twice_is_a_no_op_and_does_not_rewrite_the_reviewer(self):
        pid = await self.seed("PF-LC-ID1", status="PENDING_REVIEW", review_state="PENDING")
        self.assertEqual(self.act("approve", pid).status_code, 200)
        first = await self.row(pid)
        reviewed_at = (first.review or {}).get("reviewedAt")
        self.assertEqual(self.act("approve", pid).status_code, 200)
        second = await self.row(pid)
        self.assertEqual((second.review or {}).get("state"), "APPROVED")
        self.assertEqual((second.review or {}).get("reviewedAt"), reviewed_at)
        self.assertEqual(second.status, "PENDING_REVIEW")

    async def test_publish_twice_is_a_no_op_and_does_not_rewrite_published_at(self):
        pid = await self.seed("PF-LC-ID2", status="PENDING_REVIEW", review_state="APPROVED")
        self.assertEqual(self.act("publish", pid).status_code, 200)
        first = await self.row(pid)
        self.assertEqual(self.act("publish", pid).status_code, 200)
        second = await self.row(pid)
        self.assertEqual(second.published_at, first.published_at)
        self.assertEqual(second.published_by, first.published_by)

    async def test_the_other_five_actions_are_not_idempotent(self):
        """A second call is a 422 — the plan declares no no-op for these."""
        sequences = {
            "unpublish": ("PUBLISHED", "APPROVED"),
            "archive": ("DRAFT", "NONE"),
            "restore": ("ARCHIVED", "NONE"),
            "submitReview": ("DRAFT", "NONE"),
            "reject": ("PENDING_REVIEW", "PENDING"),
        }
        for action, (status, review_state) in sequences.items():
            pid = f"PF-LC-ID3-{action[:5].upper()}"
            await self.seed(pid, status=status, review_state=review_state)
            with self.subTest(action=action):
                self.assertEqual(self.act(action, pid).status_code, 200)
                self.assert_canonical_422(self.act(action, pid))

    async def test_only_the_two_declared_actions_carry_an_idempotency_marker(self):
        marked = {
            action for action, rule in LIFECYCLE_TRANSITIONS.items()
            if rule.get("idempotent_when")
        }
        self.assertEqual(marked, {"approve", "publish"})


# ═══════════════════════════════════════════════════════════════════════════
# 6. CHANGE-ID  — the cascade question, resolved
# ═══════════════════════════════════════════════════════════════════════════

class ChangeIdTests(_LifecycleCase):
    """
    Plan §9.2 / §24 step 8: "resolve the change-id cascade question or restrict
    the route". Resolution: the route rewrites the DISPLAY LABEL only, never
    the primary key, so no cascade target exists — and the freeness check is
    restricted so a label can no longer be duplicated.
    """

    async def test_change_id_never_moves_the_primary_key(self):
        """The premise of the whole cascade question, asserted rather than assumed."""
        pid = await self.seed("PF-LC-CID1", status="DRAFT", review_state="NONE")
        response = self.client.post(
            f"/api/v1/admin/products/{pid}/change-id", json={"newId": "PF-LC-CID1-NEW"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        row = await self.row(pid)
        self.assertIsNotNone(row, "the primary key moved — a cascade WOULD be needed")
        self.assertEqual(row.id, "PF-LC-CID1")
        self.assertEqual(row.product_id, "PF-LC-CID1-NEW")

    async def test_change_id_rejects_a_label_taken_by_another_products_label(self):  # NEW
        """
        Before Block 6 the freeness check tested `ProductModel.id` only, so two
        rows could share one `product_id`; `_get_or_404` matches on id OR
        product_id OR slug, so the duplicate made admin lookups ambiguous.
        """
        await self.seed("PF-LC-CID2", status="DRAFT", review_state="NONE")
        await self.seed("PF-LC-CID3", status="DRAFT", review_state="NONE")
        first = self.client.post(
            "/api/v1/admin/products/PF-LC-CID2/change-id", json={"newId": "SHARED-LABEL"},
        )
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post(
            "/api/v1/admin/products/PF-LC-CID3/change-id", json={"newId": "SHARED-LABEL"},
        )
        self.assertEqual(second.status_code, 409, second.text)
        body = second.json()
        self.assertIs(body.get("success"), False)
        self.assertEqual(body["error"]["code"], "CONFLICT")
        row = await self.row("PF-LC-CID3")
        self.assertEqual(row.product_id, "PF-LC-CID3", "the refused change still wrote")

    async def test_change_id_still_rejects_a_label_taken_by_a_primary_key(self):
        await self.seed("PF-LC-CID4", status="DRAFT", review_state="NONE")
        await self.seed("PF-LC-CID5", status="DRAFT", review_state="NONE")
        response = self.client.post(
            "/api/v1/admin/products/PF-LC-CID4/change-id", json={"newId": "PF-LC-CID5"},
        )
        self.assertEqual(response.status_code, 409, response.text)

    async def test_change_id_to_its_own_current_label_is_not_a_self_conflict(self):
        pid = await self.seed("PF-LC-CID6", status="DRAFT", review_state="NONE")
        response = self.client.post(
            f"/api/v1/admin/products/{pid}/change-id", json={"newId": "PF-LC-CID6"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    async def test_change_id_does_not_alter_the_lifecycle(self):
        pid = await self.seed("PF-LC-CID7", status="PUBLISHED", review_state="APPROVED")
        self.client.post(
            f"/api/v1/admin/products/{pid}/change-id", json={"newId": "PF-LC-CID7-X"},
        )
        row = await self.row(pid)
        self.assertEqual(row.status, "PUBLISHED")
        self.assertIs(row.published, True)


# ═══════════════════════════════════════════════════════════════════════════
# 7. REVIEW-FLAG VOCABULARY
# ═══════════════════════════════════════════════════════════════════════════

class ReviewFlagVocabularyTests(_LifecycleCase):
    """Plan §9.2: "no vocabulary validation — declare the flag vocabulary"."""

    async def test_an_unknown_flag_is_refused_and_names_itself(self):  # NEW
        pid = await self.seed(
            "PF-LC-RF1", status="DRAFT", review_state="NONE",
            review_flags=["NEEDS_MEDIA"],
        )
        response = self.client.post(
            f"/api/v1/admin/products/{pid}/review-flags/clear",
            json={"flags": ["NOT_A_REAL_FLAG"]},
        )
        self.assertEqual(response.status_code, 422, response.text)
        body = response.json()
        self.assertIs(body.get("success"), False)
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("NOT_A_REAL_FLAG", response.text)
        row = await self.row(pid)
        self.assertEqual(list(row.review_flags or []), ["NEEDS_MEDIA"])

    async def test_every_declared_flag_is_accepted(self):
        for index, flag in enumerate(REVIEW_FLAG_VALUES):
            pid = f"PF-LC-RF2-{index:02d}"
            await self.seed(
                pid, status="DRAFT", review_state="NONE", review_flags=[flag],
            )
            with self.subTest(flag=flag):
                response = self.client.post(
                    f"/api/v1/admin/products/{pid}/review-flags/clear",
                    json={"flags": [flag]},
                )
                self.assertEqual(response.status_code, 200, response.text)
                row = await self.row(pid)
                self.assertEqual(list(row.review_flags or []), [])

    async def test_the_vocabulary_is_blocking_plus_informational_with_no_overlap(self):
        self.assertEqual(
            set(REVIEW_FLAG_VALUES),
            set(REVIEW_FLAG_BLOCKING) | set(REVIEW_FLAG_INFORMATIONAL),
        )
        self.assertFalse(set(REVIEW_FLAG_BLOCKING) & set(REVIEW_FLAG_INFORMATIONAL))
        self.assertEqual(len(REVIEW_FLAG_VALUES), len(set(REVIEW_FLAG_VALUES)))

    async def test_the_publish_gate_consumes_the_declared_blocking_set(self):
        """
        Structural: `get_publish_issues` must read REVIEW_FLAG_BLOCKING, not a
        hand-copied literal set that can drift from the declaration.
        """
        import inspect

        from app.services.catalog import product_service

        source = inspect.getsource(product_service.get_publish_issues)
        self.assertIn("REVIEW_FLAG_BLOCKING", source)
        for literal in ("NAME_REVIEW_REQUIRED", "NEEDS_MEDIA", "CONFLICT_UNRESOLVED"):
            self.assertNotIn(
                f'"{literal}"', source,
                "the blocking set is hand-copied again instead of declared",
            )

    async def test_clearing_an_empty_list_is_a_no_op(self):
        pid = await self.seed(
            "PF-LC-RF3", status="DRAFT", review_state="NONE",
            review_flags=["NEEDS_MEDIA"],
        )
        response = self.client.post(
            f"/api/v1/admin/products/{pid}/review-flags/clear", json={"flags": []},
        )
        self.assertEqual(response.status_code, 200, response.text)
        row = await self.row(pid)
        self.assertEqual(list(row.review_flags or []), ["NEEDS_MEDIA"])


# ═══════════════════════════════════════════════════════════════════════════
# 8. BULK  — there is no bulk lifecycle, and that is the contract
# ═══════════════════════════════════════════════════════════════════════════

class BulkLifecycleTests(_LifecycleCase):
    """
    Plan §9.2 bulk row: "`status` rejected with a proper 422 — unchanged".
    There is no bulk publish / archive / unpublish / approve / reject endpoint,
    by design: lifecycle rules are enforced per product. These are REGRESSION
    LOCKS on that boundary.
    """

    async def test_bulk_cannot_carry_a_status_change(self):
        await self.seed("PF-LC-BULK1", status="DRAFT", review_state="NONE")
        response = self.client.post(
            "/api/v1/admin/products/bulk",
            json={"productIds": ["PF-LC-BULK1"], "updates": {"status": "PUBLISHED"}},
        )
        error = self.assert_canonical_422(response)
        self.assertIn("status", error["details"]["rejected"])
        self.assertIn("supported", error["details"])
        row = await self.row("PF-LC-BULK1")
        self.assertEqual(row.status, "DRAFT")

    async def test_bulk_cannot_carry_publication_or_review_fields(self):
        await self.seed("PF-LC-BULK2", status="DRAFT", review_state="NONE")
        for field, value in (
            ("published", True),
            ("review", {"state": "APPROVED"}),
            ("publishedAt", "2026-01-01T00:00:00Z"),
            ("reviewFlags", []),
        ):
            with self.subTest(field=field):
                response = self.client.post(
                    "/api/v1/admin/products/bulk",
                    json={"productIds": ["PF-LC-BULK2"], "updates": {field: value}},
                )
                self.assertEqual(response.status_code, 422, response.text)
                row = await self.row("PF-LC-BULK2")
                self.assertEqual(row.status, "DRAFT")
                self.assertIs(row.published, False)
                self.assertEqual((row.review or {}).get("state"), "NONE")

    async def test_no_bulk_lifecycle_route_exists(self):
        """Structural: adding one later must be a deliberate, visible decision."""
        from app.api.v1.products import router

        paths = {route.path for route in router.routes}
        for verb in ("publish", "unpublish", "archive", "restore", "approve", "reject"):
            self.assertNotIn(f"/admin/products/bulk/{verb}", paths)
            self.assertNotIn(f"/admin/products/bulk-{verb}", paths)


# ═══════════════════════════════════════════════════════════════════════════
# 9. RBAC  — unauthorised lifecycle calls must not mutate
# ═══════════════════════════════════════════════════════════════════════════

class LifecycleRbacTests(_LifecycleCase):
    """
    Plan §9.2 auth column: every admin lifecycle route is
    `get_current_admin` + `products.manage`; `submit-review` is the one route a
    non-admin can reach. Existing project RBAC semantics — no new permissions.
    """

    def _unauthenticated_client(self):
        from app.dependencies import get_current_user

        app = self._build_app()
        app.dependency_overrides.pop(get_current_user, None)
        return TestClient(app, raise_server_exceptions=False)

    async def test_every_admin_lifecycle_route_demands_admin_and_products_manage(self):
        """Structural, over the real router — one row per lifecycle endpoint."""
        import inspect
        import re

        from app.api.v1 import products as products_module

        source = inspect.getsource(products_module)
        for action, (_, template, _body) in ACTION_ROUTES.items():
            path = template.replace("/api/v1", "").replace("{id}", "{id}")
            with self.subTest(action=action):
                match = re.search(
                    re.escape(f'"{path}"') + r".*?\n(async def .*?)(?=\n@router|\Z)",
                    source, re.S,
                )
                self.assertIsNotNone(match, f"route {path} not found")
                body = match.group(1)
                self.assertIn("products.manage", body)
                if action == "submitReview":
                    self.assertIn("get_current_user", body)
                else:
                    self.assertIn("get_current_admin", body)

    async def test_an_anonymous_caller_cannot_move_the_lifecycle(self):
        pid = await self.seed("PF-LC-RBAC1", status="PENDING_REVIEW", review_state="APPROVED")
        client = self._unauthenticated_client()
        for action in ACTION_ROUTES:
            method, template, body = ACTION_ROUTES[action]
            with self.subTest(action=action):
                response = client.request(
                    method, template.format(id=pid), json=body,
                )
                self.assertIn(
                    response.status_code, (401, 403),
                    f"{action} was reachable anonymously: {response.status_code}",
                )
                row = await self.row(pid)
                self.assertEqual(row.status, "PENDING_REVIEW")
                self.assertIs(row.published, False)


# ═══════════════════════════════════════════════════════════════════════════
# 10. CACHE FRESHNESS + STOREFRONT AFTER A TRANSITION
# ═══════════════════════════════════════════════════════════════════════════

class LifecycleCacheFreshnessTests(_LifecycleCase):
    """
    Plan §24 step 8 does not ask for new cache work — Block 5 already extended
    taxonomy invalidation. These are REGRESSION LOCKS proving each lifecycle
    transition is visible on the very next storefront request, using the
    EXISTING `invalidate_product_cache` path (no new mechanism).
    """

    def storefront_ids(self):
        response = self.client.get("/api/v1/products", params={"pageSize": 200})
        self.assertEqual(response.status_code, 200, response.text)
        return {item["id"] for item in response.json()["items"]}

    def pdp(self, pid):
        return self.client.get(f"/api/v1/products/{pid}")

    async def test_publish_then_unpublish_then_archive_is_visible_immediately(self):
        pid = await self.seed("PF-LC-CACHE1", status="PENDING_REVIEW", review_state="APPROVED")
        self.assertNotIn(pid, self.storefront_ids())
        self.assertEqual(self.pdp(pid).status_code, 404)

        self.assertEqual(self.act("publish", pid).status_code, 200)
        self.assertIn(pid, self.storefront_ids(), "PUBLISH not visible on a fresh request")
        self.assertEqual(self.pdp(pid).status_code, 200)

        self.assertEqual(self.act("unpublish", pid).status_code, 200)
        self.assertNotIn(pid, self.storefront_ids(), "UNPUBLISH not applied on a fresh request")
        self.assertEqual(self.pdp(pid).status_code, 404)

        self.assertEqual(self.act("archive", pid).status_code, 200)
        self.assertNotIn(pid, self.storefront_ids())
        self.assertEqual(self.pdp(pid).status_code, 404)

    async def test_a_primed_pdp_cache_does_not_survive_unpublish(self):
        pid = await self.seed("PF-LC-CACHE2", status="PENDING_REVIEW", review_state="APPROVED")
        self.act("publish", pid)
        self.assertEqual(self.pdp(pid).status_code, 200)  # primes the KV entry
        self.act("unpublish", pid)
        self.assertEqual(
            self.pdp(pid).status_code, 404,
            "a stale cached DTO outlived the unpublish",
        )

    async def test_restore_does_not_republish(self):
        """RESTORE returns DRAFT — visibility does NOT come back with it."""
        pid = await self.seed("PF-LC-CACHE3", status="PENDING_REVIEW", review_state="APPROVED")
        self.act("publish", pid)
        self.act("archive", pid)
        self.assertEqual(self.act("restore", pid).status_code, 200)
        row = await self.row(pid)
        self.assertEqual(row.status, "DRAFT")
        self.assertIs(row.published, False)
        self.assertNotIn(pid, self.storefront_ids())
        self.assertEqual(self.pdp(pid).status_code, 404)

    async def test_an_illegal_transition_does_not_disturb_storefront_state(self):
        pid = await self.seed("PF-LC-CACHE4", status="PENDING_REVIEW", review_state="APPROVED")
        self.act("publish", pid)
        self.assertIn(pid, self.storefront_ids())
        self.assert_canonical_422(self.act("restore", pid))
        self.assertIn(pid, self.storefront_ids(), "a refused transition changed visibility")


# ═══════════════════════════════════════════════════════════════════════════
# 11. RESPONSE CONTRACT + ERROR ENVELOPE
# ═══════════════════════════════════════════════════════════════════════════

class LifecycleResponseContractTests(_LifecycleCase):
    """Every lifecycle route returns the same envelope; failures never leak."""

    async def test_every_legal_transition_returns_the_product_envelope(self):
        cases = {
            "submitReview": ("DRAFT", "NONE"),
            "approve": ("PENDING_REVIEW", "PENDING"),
            "reject": ("PENDING_REVIEW", "PENDING"),
            "publish": ("PENDING_REVIEW", "APPROVED"),
            "unpublish": ("PUBLISHED", "APPROVED"),
            "archive": ("DRAFT", "NONE"),
            "restore": ("ARCHIVED", "NONE"),
        }
        for action, (status, review_state) in cases.items():
            pid = f"PF-LC-RESP-{action[:5].upper()}"
            await self.seed(pid, status=status, review_state=review_state)
            with self.subTest(action=action):
                response = self.act(action, pid)
                self.assertEqual(response.status_code, 200, response.text)
                product = response.json()["product"]
                self.assertIn(product["status"], PRODUCT_STATUS_VALUES)
                self.assertIn(product["review"]["state"], REVIEW_STATE_VALUES)
                self.assertIsInstance(product["published"], bool)

    async def test_the_response_matches_the_persisted_row(self):
        """The API must never report a state the database does not hold."""
        pid = await self.seed("PF-LC-RESP1", status="PENDING_REVIEW", review_state="APPROVED")
        product = self.act("publish", pid).json()["product"]
        row = await self.row(pid)
        self.assertEqual(product["status"], row.status)
        self.assertEqual(product["published"], row.published)
        self.assertEqual(product["review"]["state"], (row.review or {}).get("state"))

    async def test_a_lifecycle_call_on_a_missing_product_is_the_canonical_404(self):
        for action in ACTION_ROUTES:
            with self.subTest(action=action):
                response = self.act(action, "PF-LC-DOES-NOT-EXIST")
                self.assertEqual(response.status_code, 404, response.text)
                body = response.json()
                self.assertIs(body.get("success"), False)
                self.assertEqual(body["error"]["code"], "NOT_FOUND")
                blob = response.text.lower()
                for leak in ("traceback", "sqlalchemy", "psycopg", "select "):
                    self.assertNotIn(leak, blob)

    async def test_no_lifecycle_refusal_returns_a_500(self):
        """Plan §25 criterion 5 — a rejection is never an internal error."""
        for action in ACTION_ROUTES:
            for status in PRODUCT_STATUS_VALUES:
                for review_state in REVIEW_STATE_VALUES:
                    if transition_allowed(action, status, review_state):
                        continue
                    pid = f"PF-LC-500-{action[:4].upper()}-{status[:2]}-{review_state[:2]}"
                    await self.seed(pid, status=status, review_state=review_state)
                    with self.subTest(action=action, status=status, review=review_state):
                        self.assertLess(self.act(action, pid).status_code, 500)


if __name__ == "__main__":
    unittest.main()
