"""
Explore Pydantic schemas.

Covers:
  GET /explore        — paginated explore stream with interleaved promo/editorial cards
  GET /explore/offers — offer strip inside the stream
  GET /home           — homepage assembly

Envelope convention mirrors the rest of the application:
  { ok: true, ... }

Constants (from frontend explore.js / config):
  EXPLORE_PAGE_SIZE     = 20
  EXPLORE_PROMO_AFTER   = 4   (insert a promo card after every 4th product card)
  EXPLORE_EDITORIAL_AFTER = 8 (insert an editorial card after every 8th product card)
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.catalog.product import (
    SORT_ALIASES,
    VALID_SORTS,
    StorefrontProduct,
)


# ── Interleaving constants ────────────────────────────────────────────────────

EXPLORE_PAGE_SIZE = 20
EXPLORE_PROMO_AFTER = 4       # insert a promo card after every Nth product
EXPLORE_EDITORIAL_AFTER = 8   # insert an editorial card after every Nth product


# ── Stream card shapes ────────────────────────────────────────────────────────

class ProductCard(BaseModel):
    """A product entry inside the explore stream."""

    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["product"] = "product"
    product: StorefrontProduct


class PromoCard(BaseModel):
    """A promotional banner / offer card interleaved in the stream."""

    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["promo"] = "promo"
    id: str
    title: str = ""
    subtitle: str = ""
    cta: str = ""
    href: str = ""
    image: str = ""
    badge: str = ""
    # Position in the stream where this card was inserted (0-based)
    position: int = 0


class EditorialCard(BaseModel):
    """An editorial / curated story card interleaved in the stream."""

    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["editorial"] = "editorial"
    id: str
    title: str = ""
    subtitle: str = ""
    body: str = ""
    cta: str = ""
    href: str = ""
    image: str = ""
    # Position in the stream where this card was inserted (0-based)
    position: int = 0


# Union type for a single stream entry
StreamCard = Union[ProductCard, PromoCard, EditorialCard]


# ── Offer shape ───────────────────────────────────────────────────────────────

class ExploreOffer(BaseModel):
    """A single offer entry in the GET /explore/offers strip."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str = ""
    description: str = ""
    code: str = ""
    discount_type: str = Field("percentage", alias="discountType")
    discount_value: float = Field(0.0, alias="discountValue")
    min_order_value: int = Field(0, alias="minOrderValue")
    valid_until: Optional[str] = Field(None, alias="validUntil")
    badge: str = ""
    image: str = ""
    href: str = ""


# ── Homepage section shapes ───────────────────────────────────────────────────

class HeroSlide(BaseModel):
    """A single hero / carousel slide."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str = ""
    subtitle: str = ""
    cta: str = ""
    href: str = ""
    image: str = ""
    mobile_image: str = Field("", alias="mobileImage")
    media_id: Optional[str] = Field(None, alias="mediaId")


class CategoryCard(BaseModel):
    """Shop-by-category card on the homepage."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str = ""
    slug: str = ""
    image: str = ""
    href: str = ""
    eyebrow: str = ""


class HomepageSection(BaseModel):
    """A named editorial / product seam on the homepage."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str = ""
    subtitle: str = ""
    image: str = ""
    href: str = ""
    products: List[StorefrontProduct] = []


class HomepageSaleBanner(BaseModel):
    """The sale banner seam."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = "sale-banner"
    title: str = ""
    subtitle: str = ""
    cta: str = ""
    href: str = ""
    image: str = ""
    media_id: Optional[str] = Field(None, alias="mediaId")


# ── Query params ──────────────────────────────────────────────────────────────

class ExploreQuery(BaseModel):
    """Query parameters for GET /explore."""

    model_config = ConfigDict(populate_by_name=True)

    # Optional search term
    q: Optional[str] = None

    # 12 facets (multi-value)
    category: Optional[Union[str, List[str]]] = None
    subcategory: Optional[Union[str, List[str]]] = None
    gender: Optional[Union[str, List[str]]] = None
    price: Optional[Union[str, List[str]]] = None
    size: Optional[Union[str, List[str]]] = None
    color: Optional[Union[str, List[str]]] = None
    fabric: Optional[Union[str, List[str]]] = None
    material: Optional[Union[str, List[str]]] = None
    occasion: Optional[Union[str, List[str]]] = None
    collection: Optional[Union[str, List[str]]] = None
    rating: Optional[Union[str, List[str]]] = None
    availability: Optional[Union[str, List[str]]] = None

    sort: str = "recommended"
    page: int = 1
    page_size: int = Field(EXPLORE_PAGE_SIZE, alias="pageSize")

    @field_validator("sort")
    @classmethod
    def resolve_sort(cls, v: str) -> str:
        resolved = SORT_ALIASES.get(v, v)
        return resolved if resolved in VALID_SORTS else "recommended"


# ── Response envelopes ────────────────────────────────────────────────────────

class ExploreResponse(BaseModel):
    """Response for GET /explore."""

    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    items: List[StorefrontProduct] = []
    total: int = 0
    page: int = 1
    page_size: int = Field(EXPLORE_PAGE_SIZE, alias="pageSize")
    has_more: bool = Field(False, alias="hasMore")
    stream: List[Any] = []  # List[StreamCard] — kept as Any for serialisation flexibility


class ExploreOffersResponse(BaseModel):
    """Response for GET /explore/offers."""

    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    offers: List[ExploreOffer] = []


class HomeResponse(BaseModel):
    """Response for GET /home."""

    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    hero_slides: List[HeroSlide] = Field([], alias="heroSlides")
    new_arrivals: List[StorefrontProduct] = Field([], alias="newArrivals")
    categories: List[CategoryCard] = []
    saree_edit: HomepageSection = Field(
        default_factory=lambda: HomepageSection(id="saree-edit", title="The Saree Edit"),
        alias="sareeEdit",
    )
    bride_groom_edit: HomepageSection = Field(
        default_factory=lambda: HomepageSection(id="bride-groom-edit", title="Bride & Groom Edit"),
        alias="brideGroomEdit",
    )
    celebration_edit: HomepageSection = Field(
        default_factory=lambda: HomepageSection(id="celebration-edit", title="The Celebration Edit"),
        alias="celebrationEdit",
    )
    sale_banner: Optional[HomepageSaleBanner] = Field(None, alias="saleBanner")
