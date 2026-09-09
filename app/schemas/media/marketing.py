"""
Marketing media schemas — B-02.

Defines the API contract for backend-managed marketing placements,
including HOME_HERO. Product media remains separate (media_product_media),
collection/editorial remains filesystem-based, marketing/hero uses
media_marketing_media.

Each entry is one placement slot (e.g. HOME_HERO position 1) referencing a
stored object key (hero/hero001.avif) and optional editorial copy.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Allowed placements — derived from frontend's MARKETING_PLACEMENTS
# ---------------------------------------------------------------------------
MARKETING_PLACEMENT_VALUES = (
    "HOME_HERO",
    "WOMEN_SECTION",
    "SAREE_SECTION",
    "LEHENGA_SECTION",
    "BRIDAL_SECTION",
    "GROOM_SECTION",
    "KIDS_SECTION",
    "BANGLES_SECTION",
    "JEWELLERY_SECTION",
    "FESTIVE_SECTION",
    "NEW_ARRIVALS",
    "EDITORIAL",
    "PROMOTION",
)

HOME_HERO_PLACEMENT = "HOME_HERO"


def is_valid_placement(value: str) -> bool:
    return str(value or "").strip().upper() in MARKETING_PLACEMENT_VALUES


# ---------------------------------------------------------------------------
# Base models
# ---------------------------------------------------------------------------

class MarketingMediaBase(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    placement: str = Field(default=HOME_HERO_PLACEMENT, description="Marketing placement, e.g. HOME_HERO")
    object_key: str = Field(..., alias="objectKey", description="Storage object key, e.g. hero/hero001.avif")
    media_asset_id: Optional[str] = Field(None, alias="mediaAssetId")
    title: Optional[str] = None
    subtitle: Optional[str] = None
    cta_label: Optional[str] = Field(None, alias="ctaLabel")
    cta_href: Optional[str] = Field(None, alias="ctaHref")
    alt_text: Optional[str] = Field(None, alias="altText")
    sort_order: int = Field(default=0, alias="sortOrder")
    is_active: bool = Field(default=True, alias="isActive")


class MarketingMediaCreate(MarketingMediaBase):
    """POST /admin/marketing/media — create one entry."""


class MarketingMediaUpdate(BaseModel):
    """PATCH /admin/marketing/media/{id} — partial update."""

    model_config = ConfigDict(populate_by_name=True)

    placement: Optional[str] = None
    object_key: Optional[str] = Field(None, alias="objectKey")
    media_asset_id: Optional[str] = Field(None, alias="mediaAssetId")
    title: Optional[str] = None
    subtitle: Optional[str] = None
    cta_label: Optional[str] = Field(None, alias="ctaLabel")
    cta_href: Optional[str] = Field(None, alias="ctaHref")
    alt_text: Optional[str] = Field(None, alias="altText")
    sort_order: Optional[int] = Field(None, alias="sortOrder")
    is_active: Optional[bool] = Field(None, alias="isActive")


class MarketingMediaResponse(MarketingMediaBase):
    """Single marketing media entry with DB identity and URLs."""

    id: str
    created_at: Optional[datetime] = Field(None, alias="createdAt")
    updated_at: Optional[datetime] = Field(None, alias="updatedAt")
    url: Optional[str] = None  # resolved media URL via build_media_url
    created_by: Optional[str] = Field(None, alias="createdBy")
    updated_by: Optional[str] = Field(None, alias="updatedBy")


class MarketingMediaListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    items: List[MarketingMediaResponse] = []
    total: int = 0
    placement: Optional[str] = None


class MarketingMediaReorderItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    sort_order: int = Field(..., alias="sortOrder")


class MarketingMediaReorderRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    placement: str = Field(default=HOME_HERO_PLACEMENT)
    items: List[MarketingMediaReorderItem]


class MarketingMediaReorderResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    items: List[MarketingMediaResponse] = []
