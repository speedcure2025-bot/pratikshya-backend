"""
Search — API router.

URL mapping (API_CONTRACT.md → implementation):

  Public
  ─────────────────────────────────────────────────────────────────────────────
  GET  /search       ← full-text product search with facets, sort, pagination

Matching behaviour (from API_CONTRACT.md § SEARCH):
  Case/diacritic-normalised substring across: name, brand, category label,
  subcategory, fabric, material, colors, occasion, tags, collection, sku.

Response shape:
  { ok, items: StorefrontProduct[], total, facets: FacetCounts, suggestions,
    appliedFilters }
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.schemas.catalog.search import SearchQuery, SearchResponse
from app.services.catalog.search_service import SearchService

router = APIRouter(tags=["Search"])


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="Product search — full-text + faceted, sorted and paginated",
    description=(
        "Full-text search across: name, brand, category, subcategory, fabric, "
        "material, colors, occasion, tags, collection, SKU.  \n"
        "Matching is case/diacritic-normalised substring — exactly reproduces "
        "`matchesSearch()` / `normaliseSearchText()` from the frontend.  \n\n"
        "**Visibility gate** — only `PUBLISHED` products in an `ACTIVE` category and, "
        "when set, an `ACTIVE` subcategory.  \n\n"
        "**Facets** — 12 facets (category, subcategory, gender, price, size, color, "
        "fabric, material, occasion, collection, rating, availability). "
        "Multi-value: repeat the key. AND across facets, OR within a facet.  \n\n"
        "**Sort** — recommended (default), newest, price-asc, price-desc, discount, "
        "name-asc, popularity, rating. "
        "Aliases: price-low → price-asc, price-high → price-desc, name/az → name-asc.  \n\n"
        "**Pagination** — `page` + `pageSize` (default 20, max 200).  \n\n"
        "**Suggestions** — static list today; dynamic suggest is "
        "`BACKEND DECISION REQUIRED`."
    ),
)
async def search_products(
    # --- Search term ---
    q: Optional[str] = Query(None, description="Search term — case/diacritic-normalised substring"),

    # --- 12 facets ---
    category: Optional[List[str]] = Query(None, description="Filter by category id(s)"),
    subcategory: Optional[List[str]] = Query(None, description="Filter by subcategory id(s)"),
    gender: Optional[List[str]] = Query(None, description="Filter by gender"),
    price: Optional[List[str]] = Query(None, description="Filter by price band id(s)"),
    size: Optional[List[str]] = Query(None, description="Filter by size(s)"),
    color: Optional[List[str]] = Query(None, description="Filter by color(s)"),
    fabric: Optional[List[str]] = Query(None, description="Filter by fabric(s)"),
    material: Optional[List[str]] = Query(None, description="Filter by material(s)"),
    occasion: Optional[List[str]] = Query(None, description="Filter by occasion(s)"),
    collection: Optional[List[str]] = Query(None, description="Filter by collection id(s)"),
    rating: Optional[List[str]] = Query(None, description="Minimum rating filter"),
    availability: Optional[List[str]] = Query(None, description="Filter by availability status"),

    # --- Sort & pagination ---
    sort: str = Query(
        "recommended",
        description=(
            "Sort order: recommended, newest, price-asc, price-desc, discount, "
            "name-asc, popularity, rating. "
            "Aliases: price-low, price-high, name, az."
        ),
    ),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    pageSize: int = Query(20, ge=1, le=200, alias="pageSize", description="Items per page (default 20)"),

    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    search_query = SearchQuery(
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
    service = SearchService(db)
    return await service.search(search_query)
