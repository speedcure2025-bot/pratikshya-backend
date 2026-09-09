"""
Explore — API router.

URL mapping (API_CONTRACT.md → implementation):

  Public
  ─────────────────────────────────────────────────────────────────────────────
  GET  /explore          ← infinite/paged explore stream with interleaved cards
  GET  /explore/offers   ← offer strip inside the stream
  GET  /home             ← homepage assembly (heroes, new arrivals, editorial seams)

Contract notes:
  - GET /explore is the ONLY paginated surface in the application.
  - Stream interleaving constants (from explore.js):
      EXPLORE_PROMO_AFTER     = 4  (promo card after every 4th product)
      EXPLORE_EDITORIAL_AFTER = 8  (editorial card after every 8th product)
  - GET /home reservation rule: hero plates reserved first; category/seam images
    exclude already-reserved ids so no image appears twice on the page.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.schemas.catalog.explore import (
    ExploreOffersResponse,
    ExploreQuery,
    ExploreResponse,
    HomeResponse,
)
from app.services.catalog.explore_service import ExploreService

router = APIRouter(tags=["Explore"])


# ===========================================================================
# GET /explore — paginated explore stream
# ===========================================================================

@router.get(
    "/explore",
    response_model=ExploreResponse,
    summary="Explore — paginated product stream with interleaved promo/editorial cards",
    description=(
        "The **only** paginated surface in the application (pageSize defaults to 20).  \n\n"
        "Returns `items` (the plain product page) plus `stream` — the same products "
        "interleaved with `promo` and `editorial` cards at fixed intervals:  \n"
        "- A **promo** card is inserted after every 4th product (`EXPLORE_PROMO_AFTER`).  \n"
        "- An **editorial** card is inserted after every 8th product (`EXPLORE_EDITORIAL_AFTER`).  \n"
        "  When both intervals coincide the editorial card wins.  \n\n"
        "**Visibility gate** — only `PUBLISHED` products in an `ACTIVE` category and, "
        "when set, an `ACTIVE` subcategory.  \n\n"
        "**Facets, sort, pagination** — same vocabulary as `GET /products`.  \n\n"
        "**`hasMore`** — `true` when `page × pageSize < total`."
    ),
)
async def get_explore(
    # Optional search term
    q: Optional[str] = Query(None, description="Optional search/filter term"),

    # 12 facets
    category: Optional[List[str]] = Query(None),
    subcategory: Optional[List[str]] = Query(None),
    gender: Optional[List[str]] = Query(None),
    price: Optional[List[str]] = Query(None, description="Price band id(s)"),
    size: Optional[List[str]] = Query(None),
    color: Optional[List[str]] = Query(None),
    fabric: Optional[List[str]] = Query(None),
    material: Optional[List[str]] = Query(None),
    occasion: Optional[List[str]] = Query(None),
    collection: Optional[List[str]] = Query(None),
    rating: Optional[List[str]] = Query(None),
    availability: Optional[List[str]] = Query(None),

    # Sort & pagination
    sort: str = Query("recommended"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200, alias="pageSize"),

    db: AsyncSession = Depends(get_db),
) -> ExploreResponse:
    explore_query = ExploreQuery(
        q=q,
        category=category,
        subcategory=subcategory,
        gender=gender,
        price=price,
        size=size,
        color=color,
        fabric=fabric,
        material=material,
        occasion=occasion,
        collection=collection,
        rating=rating,
        availability=availability,
        sort=sort,
        page=page,
        pageSize=pageSize,
    )
    service = ExploreService(db)
    return await service.get_explore(explore_query)


# ===========================================================================
# GET /explore/offers — offer strip
# ===========================================================================

@router.get(
    "/explore/offers",
    response_model=ExploreOffersResponse,
    summary="Explore — offer strip",
    description=(
        "Returns the promotional offer strip displayed inside the explore stream.  \n"
        "Source: `getExploreOffers()`.  \n"
        "Content is static today; a live offer table is `BACKEND DECISION REQUIRED`."
    ),
)
async def get_explore_offers(
    db: AsyncSession = Depends(get_db),
) -> ExploreOffersResponse:
    service = ExploreService(db)
    return await service.get_explore_offers()


# ===========================================================================
# GET /home — homepage assembly
# ===========================================================================

@router.get(
    "/home",
    response_model=HomeResponse,
    summary="Homepage — assembled in one call",
    description=(
        "Single endpoint that assembles the full homepage:  \n"
        "- **heroSlides** — hero carousel slides (media URLs are `BACKEND DECISION REQUIRED`).  \n"
        "- **newArrivals** — up to 12 newest `PUBLISHED` products.  \n"
        "- **categories** — shop-by-category cards derived from active product categories.  \n"
        "- **sareeEdit** — up to 8 saree products.  \n"
        "- **brideGroomEdit** — up to 4 bridal couture + 4 menswear products.  \n"
        "- **celebrationEdit** — up to 8 festive-occasion products.  \n"
        "- **saleBanner** — sale banner seam.  \n\n"
        "**Reservation rule** — hero media plates are reserved first; every subsequent "
        "seam skips images already used by an earlier section so no plate appears "
        "twice on the page. `BACKEND DECISION REQUIRED`: composed endpoint vs "
        "several separate endpoints — the reservation ordering must be preserved "
        "either way."
    ),
)
async def get_home(
    db: AsyncSession = Depends(get_db),
) -> HomeResponse:
    service = ExploreService(db)
    return await service.get_home()
