"""
SearchService — business logic for GET /search.

Contract reference: API_CONTRACT.md § SEARCH

Matching rules (must reproduce matchesSearch / normaliseSearchText exactly):
  - Normalise both sides: case-folded, diacritic-stripped (NFKD → ASCII).
  - Substring match across: name, brand, category, subcategory, fabric,
    material, colors (joined), occasion (joined), tags (joined),
    collection, sku.
  - Facet / sort / pagination: re-uses ProductService logic verbatim so
    results stay consistent with GET /products.
  - Suggestions: static list today (from navigationConfig.js equivalent).
    A dynamic suggest endpoint is BACKEND DECISION REQUIRED.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.catalog.product import (
    FacetCounts,
    ProductListQuery,
    StorefrontProduct,
)
from app.schemas.catalog.search import SearchQuery, SearchResponse, SearchSuggestion
from app.services.catalog.product_service import ProductService

# ── Static suggestions ────────────────────────────────────────────────────────
# Mirrors searchSuggestions from src/config/navigationConfig.js.
# These are always returned regardless of the query term until a real
# suggest endpoint is built.

_STATIC_SUGGESTIONS: List[SearchSuggestion] = [
    SearchSuggestion(label="Sarees", query="sarees", category="sarees"),
    SearchSuggestion(label="Lehengas", query="lehengas", category="lehengas"),
    SearchSuggestion(label="Kurtis & Suits", query="kurtis", category="kurtis-and-suits"),
    SearchSuggestion(label="Bridal Couture", query="bridal", category="bridal-couture"),
    SearchSuggestion(label="Menswear", query="menswear", category="menswear"),
    SearchSuggestion(label="Kidswear", query="kidswear", category="kidswear"),
    SearchSuggestion(label="Silk Sarees", query="silk sarees"),
    SearchSuggestion(label="Wedding Collection", query="wedding collection"),
    SearchSuggestion(label="New Arrivals", query="new arrivals"),
    SearchSuggestion(label="Sale", query="sale"),
]


# ── Service ───────────────────────────────────────────────────────────────────

class SearchService:
    """Business logic for the search surface."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._product_service = ProductService(db)

    async def search(self, query: SearchQuery) -> SearchResponse:
        """
        GET /search

        1. Delegate to ProductService.list_storefront_products() with the
           same filter/sort/pagination parameters — this ensures identical
           visibility gating and facet computation.
        2. The `q` term is included so the product service applies its
           normalise_search substring match across all required fields.
        3. Append static suggestions (dynamic suggest is BACKEND DECISION REQUIRED).
        """

        # Map SearchQuery → ProductListQuery (they are structurally identical;
        # SearchQuery just adds the suggestions field in the response).
        product_query = ProductListQuery(
            q=query.q,
            category=query.category,
            subcategory=query.subcategory,
            gender=query.gender,
            price=query.price,
            size=query.size,
            color=query.color,
            fabric=query.fabric,
            material=query.material,
            occasion=query.occasion,
            collection=query.collection,
            rating=query.rating,
            availability=query.availability,
            sort=query.sort,
            page=query.page,
            pageSize=query.page_size,
        )

        result: Dict[str, Any] = await self._product_service.list_storefront_products(
            product_query
        )

        # Filter suggestions: when a query term is provided, narrow the static
        # list to entries whose label/query contains the term (case-insensitive).
        # This mimics the frontend filterSuggestions behaviour without a real
        # suggest index.
        suggestions = self._filter_suggestions(query.q)

        return SearchResponse(
            ok=True,
            items=result.get("items", []),
            total=result.get("total", 0),
            facets=result.get("facets", FacetCounts()),
            suggestions=suggestions,
            appliedFilters=result.get("appliedFilters", {}),
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _filter_suggestions(q: Optional[str]) -> List[SearchSuggestion]:
        """
        Return suggestions relevant to the search term.
        Falls back to the full static list when `q` is absent or blank.
        """
        if not q or not q.strip():
            return _STATIC_SUGGESTIONS

        term = q.strip().lower()
        filtered = [
            s for s in _STATIC_SUGGESTIONS
            if term in s.label.lower() or term in s.query.lower()
        ]
        # Always return at least the full list so the search page is never empty
        return filtered if filtered else _STATIC_SUGGESTIONS
