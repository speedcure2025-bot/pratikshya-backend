"""
Search Pydantic schemas.

Covers:
  GET /search — full-text product search with facets, sort, pagination and suggestions.

Envelope convention mirrors the rest of the application:
  { ok: true, items, total, facets, suggestions, appliedFilters }
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.catalog.product import (
    SORT_ALIASES,
    VALID_SORTS,
    FacetCounts,
    StorefrontProduct,
)


# ── Query params ──────────────────────────────────────────────────────────────

class SearchQuery(BaseModel):
    """
    Query parameters for GET /search.

    Accepts the full facet/sort/pagination vocabulary of GET /products
    plus the mandatory `q` term.
    """

    model_config = ConfigDict(populate_by_name=True)

    # Search term — required on the /search surface but validated in the service
    q: Optional[str] = None

    # --- 12 facets (multi-value, AND across, OR within) ---
    category: Optional[Union[str, List[str]]] = None
    subcategory: Optional[Union[str, List[str]]] = None
    gender: Optional[Union[str, List[str]]] = None
    price: Optional[Union[str, List[str]]] = None          # price band id
    size: Optional[Union[str, List[str]]] = None
    color: Optional[Union[str, List[str]]] = None
    fabric: Optional[Union[str, List[str]]] = None
    material: Optional[Union[str, List[str]]] = None
    occasion: Optional[Union[str, List[str]]] = None
    collection: Optional[Union[str, List[str]]] = None
    rating: Optional[Union[str, List[str]]] = None
    availability: Optional[Union[str, List[str]]] = None

    # --- Sort & pagination ---
    sort: str = "recommended"
    page: int = 1
    page_size: int = Field(20, alias="pageSize")

    @field_validator("sort")
    @classmethod
    def resolve_sort(cls, v: str) -> str:
        resolved = SORT_ALIASES.get(v, v)
        return resolved if resolved in VALID_SORTS else "recommended"


# ── Suggestion shape ──────────────────────────────────────────────────────────

class SearchSuggestion(BaseModel):
    """A single search suggestion entry."""

    label: str
    query: str
    category: Optional[str] = None


# ── Response envelope ─────────────────────────────────────────────────────────

class SearchResponse(BaseModel):
    """Response for GET /search."""

    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    items: List[StorefrontProduct] = []
    total: int = 0
    facets: FacetCounts = Field(default_factory=FacetCounts)
    suggestions: List[SearchSuggestion] = []
    applied_filters: Dict[str, Any] = Field({}, alias="appliedFilters")
