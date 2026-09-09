"""
Marketing media service — B-02.

Backend-managed marketing placements, including HOME_HERO. Each row is one
placement entry (e.g. HOME_HERO position 1) referencing a stored object key
(hero/hero001.avif) and optional editorial copy.

Separation:
  PRODUCT MEDIA      = products/{ID}/... → ProductMediaModel
  COLLECTION/EDITORIAL = collections/... → filesystem / legacy
  MARKETING/HERO     = hero/... or marketing/... → MarketingMediaModel

This service owns:
  - CRUD for marketing media entries
  - listing with placement filter
  - active-only ordered retrieval for HOME_HERO
  - reorder
  - validation (placement vocabulary, object_key safety, duplicate prevention)
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select, asc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ConflictException, NotFoundException, BusinessLogicException
from app.models.media.marketing_media import MarketingMediaModel
from app.schemas.media.marketing import (
    MARKETING_PLACEMENT_VALUES,
    HOME_HERO_PLACEMENT,
    MarketingMediaCreate,
    MarketingMediaUpdate,
)
from app.storage import build_media_url, normalize_object_key
from app.storage.base import InvalidObjectKeyError


def _validate_placement(placement: str) -> str:
    value = str(placement or "").strip().upper()
    if not value:
        raise BusinessLogicException("Placement is required.")
    if value not in MARKETING_PLACEMENT_VALUES:
        raise BusinessLogicException(
            f"Placement '{placement}' is not recognised. Allowed: {', '.join(MARKETING_PLACEMENT_VALUES)}"
        )
    return value


def _validate_object_key(object_key: str) -> str:
    raw = str(object_key or "").strip()
    if not raw:
        raise BusinessLogicException("objectKey is required.")
    try:
        # Normalize and validate via storage keys — prevents traversal, etc.
        safe = normalize_object_key(raw)
    except InvalidObjectKeyError as exc:
        raise BusinessLogicException(f"Invalid objectKey: {exc}") from exc
    return safe


def _to_response_dict(row: MarketingMediaModel) -> dict:
    """Convert ORM row to dict with resolved URL."""
    try:
        url = build_media_url(row.object_key)
    except Exception:
        url = f"/api/v1/media/objects/{row.object_key}"
    return {
        "id": row.id,
        "placement": row.placement,
        "objectKey": row.object_key,
        "object_key": row.object_key,
        "mediaAssetId": row.media_asset_id,
        "media_asset_id": row.media_asset_id,
        "title": row.title,
        "subtitle": row.subtitle,
        "ctaLabel": row.cta_label,
        "cta_label": row.cta_label,
        "ctaHref": row.cta_href,
        "cta_href": row.cta_href,
        "altText": row.alt_text,
        "alt_text": row.alt_text,
        "sortOrder": row.sort_order,
        "sort_order": row.sort_order,
        "isActive": row.is_active,
        "is_active": row.is_active,
        "createdAt": row.created_at,
        "created_at": row.created_at,
        "updatedAt": row.updated_at,
        "updated_at": row.updated_at,
        "createdBy": row.created_by,
        "created_by": row.created_by,
        "updatedBy": row.updated_by,
        "updated_by": row.updated_by,
        "url": url,
    }


class MarketingMediaService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── List ───────────────────────────────────────────────────────────────

    async def list(
        self, placement: Optional[str] = None, active_only: bool = False
    ) -> List[MarketingMediaModel]:
        stmt = select(MarketingMediaModel)
        if placement:
            stmt = stmt.where(MarketingMediaModel.placement == _validate_placement(placement))
        if active_only:
            stmt = stmt.where(MarketingMediaModel.is_active.is_(True))
        stmt = stmt.order_by(
            asc(MarketingMediaModel.sort_order), asc(MarketingMediaModel.created_at)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_active_home_hero(self) -> List[MarketingMediaModel]:
        """Ordered active HOME_HERO entries — used by GET /home."""
        return await self.list(placement=HOME_HERO_PLACEMENT, active_only=True)

    # ── Get one ────────────────────────────────────────────────────────────

    async def get_by_id(self, media_id: str) -> MarketingMediaModel:
        stmt = select(MarketingMediaModel).where(MarketingMediaModel.id == media_id)
        result = await self.db.execute(stmt)
        row = result.scalars().first()
        if not row:
            raise NotFoundException(f"Marketing media '{media_id}' not found.")
        return row

    # ── Create ─────────────────────────────────────────────────────────────

    async def create(
        self, payload: MarketingMediaCreate, *, actor_id: Optional[str] = None
    ) -> MarketingMediaModel:
        placement = _validate_placement(payload.placement)
        object_key = _validate_object_key(payload.object_key)

        row = MarketingMediaModel(
            placement=placement,
            object_key=object_key,
            media_asset_id=payload.media_asset_id,
            title=payload.title,
            subtitle=payload.subtitle,
            cta_label=payload.cta_label,
            cta_href=payload.cta_href,
            alt_text=payload.alt_text,
            sort_order=payload.sort_order,
            is_active=payload.is_active,
            created_by=actor_id,
            updated_by=actor_id,
        )
        self.db.add(row)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            # Duplicate placement+object_key — handle both Postgres and SQLite messages
            orig = str(exc.orig).lower() if hasattr(exc, "orig") and exc.orig else str(exc).lower()
            if (
                "uq_marketing_media_placement_object_key" in orig
                or "unique constraint" in orig
                or "duplicate" in orig
            ):
                raise ConflictException(
                    f"Media '{object_key}' already exists in placement '{placement}'."
                ) from exc
            raise BusinessLogicException(f"Could not create marketing media: {exc}") from exc

        await self.db.commit()
        await self.db.refresh(row)
        return row

    # ── Update ─────────────────────────────────────────────────────────────

    async def update(
        self, media_id: str, payload: MarketingMediaUpdate, *, actor_id: Optional[str] = None
    ) -> MarketingMediaModel:
        row = await self.get_by_id(media_id)

        if payload.placement is not None:
            row.placement = _validate_placement(payload.placement)
        if payload.object_key is not None:
            row.object_key = _validate_object_key(payload.object_key)
        if payload.media_asset_id is not None:
            row.media_asset_id = payload.media_asset_id
        if payload.title is not None:
            row.title = payload.title
        if payload.subtitle is not None:
            row.subtitle = payload.subtitle
        if payload.cta_label is not None:
            row.cta_label = payload.cta_label
        if payload.cta_href is not None:
            row.cta_href = payload.cta_href
        if payload.alt_text is not None:
            row.alt_text = payload.alt_text
        if payload.sort_order is not None:
            row.sort_order = int(payload.sort_order)
        if payload.is_active is not None:
            row.is_active = bool(payload.is_active)

        row.updated_by = actor_id

        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            orig = str(exc.orig).lower() if hasattr(exc, "orig") and exc.orig else str(exc).lower()
            if (
                "uq_marketing_media_placement_object_key" in orig
                or "unique constraint" in orig
                or "duplicate" in orig
            ):
                raise ConflictException(
                    f"Media '{row.object_key}' already exists in placement '{row.placement}'."
                ) from exc
            raise BusinessLogicException(f"Could not update marketing media: {exc}") from exc

        await self.db.commit()
        await self.db.refresh(row)
        return row

    # ── Delete ─────────────────────────────────────────────────────────────

    async def delete(self, media_id: str) -> None:
        row = await self.get_by_id(media_id)
        await self.db.delete(row)
        await self.db.commit()

    # ── Reorder ────────────────────────────────────────────────────────────

    async def reorder(
        self, placement: str, items: List[dict], *, actor_id: Optional[str] = None
    ) -> List[MarketingMediaModel]:
        placement = _validate_placement(placement)
        # items: [{id, sort_order}, ...]
        # Validate all ids belong to placement
        ids = [str(i.get("id") or i.get("ID") or "") for i in items]
        if not ids:
            raise BusinessLogicException("Reorder items list is empty.")

        stmt = select(MarketingMediaModel).where(
            MarketingMediaModel.placement == placement,
            MarketingMediaModel.id.in_(ids),
        )
        result = await self.db.execute(stmt)
        existing = {row.id: row for row in result.scalars().all()}

        if len(existing) != len(ids):
            missing = set(ids) - set(existing.keys())
            raise NotFoundException(f"Marketing media not found for reorder: {', '.join(missing)}")

        for item in items:
            mid = str(item.get("id"))
            sort_order = item.get("sort_order") if "sort_order" in item else item.get("sortOrder")
            if mid in existing:
                existing[mid].sort_order = int(sort_order)
                existing[mid].updated_by = actor_id

        await self.db.flush()
        await self.db.commit()

        # Return fresh ordered list for placement
        return await self.list(placement=placement)

    # ── Serialization ──────────────────────────────────────────────────────

    @staticmethod
    def to_response(row: MarketingMediaModel) -> dict:
        return _to_response_dict(row)

    @staticmethod
    def to_response_list(rows: List[MarketingMediaModel]) -> List[dict]:
        return [_to_response_dict(r) for r in rows]
