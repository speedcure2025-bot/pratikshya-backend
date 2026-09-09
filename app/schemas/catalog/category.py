"""
Category & Subcategory — Pydantic schemas.

Response shapes follow API_CONTRACT.md § CATEGORIES verbatim.
"""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────────────────────
# Subcategory schemas
# ─────────────────────────────────────────────────────────────────────────────

class SubcategoryResponse(BaseModel):
    """Public subcategory shape — verbatim from API_CONTRACT.md."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    categoryId: str = Field(alias="category_id", serialization_alias="categoryId")
    name: str
    slug: str
    description: Optional[str] = ""
    image: Optional[str] = ""
    status: str
    sortOrder: int = Field(0, alias="sort_order", serialization_alias="sortOrder")
    productCount: int = 0


class SubcategoryCreateRequest(BaseModel):
    """Body for POST /admin/categories/{categoryId}/subcategories."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    slug: Optional[str] = None          # auto-derived from name if omitted
    description: Optional[str] = ""
    image: Optional[str] = Field("", alias="imageUrl")
    sort_order: int = Field(0, alias="sortOrder")


class SubcategoryUpdateRequest(BaseModel):
    """Body for PATCH /admin/subcategories/{id}."""

    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    image: Optional[str] = Field(None, alias="imageUrl")
    sort_order: Optional[int] = Field(None, alias="sortOrder")


# ─────────────────────────────────────────────────────────────────────────────
# Category schemas
# ─────────────────────────────────────────────────────────────────────────────

class CategoryResponse(BaseModel):
    """Public category shape — verbatim from API_CONTRACT.md."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    name: str
    slug: str
    eyebrow: Optional[str] = ""
    description: Optional[str] = ""
    image: Optional[str] = ""
    bannerMediaId: Optional[str] = Field(None, alias="banner_media_id", serialization_alias="bannerMediaId")
    status: str
    sortOrder: int = Field(0, alias="sort_order", serialization_alias="sortOrder")
    featured: bool = False
    seoTitle: Optional[str] = Field("", alias="seo_title", serialization_alias="seoTitle")
    seoDescription: Optional[str] = Field("", alias="seo_description", serialization_alias="seoDescription")
    productCount: int = 0


class CategoryCreateRequest(BaseModel):
    """Body for POST /admin/categories."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    slug: Optional[str] = None          # auto-derived from name if omitted
    eyebrow: Optional[str] = ""
    description: Optional[str] = ""
    image: Optional[str] = Field("", alias="imageUrl")
    banner_media_id: Optional[str] = Field(None, alias="bannerMediaId")
    sort_order: int = Field(0, alias="sortOrder")
    featured: bool = False
    seo_title: Optional[str] = Field("", alias="seoTitle")
    seo_description: Optional[str] = Field("", alias="seoDescription")


class CategoryUpdateRequest(BaseModel):
    """Body for PATCH /admin/categories/{id}."""

    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = None
    slug: Optional[str] = None
    eyebrow: Optional[str] = None
    description: Optional[str] = None
    image: Optional[str] = Field(None, alias="imageUrl")
    banner_media_id: Optional[str] = Field(None, alias="bannerMediaId")
    sort_order: Optional[int] = Field(None, alias="sortOrder")
    featured: Optional[bool] = None
    seo_title: Optional[str] = Field(None, alias="seoTitle")
    seo_description: Optional[str] = Field(None, alias="seoDescription")


# ─────────────────────────────────────────────────────────────────────────────
# Response wrappers (keep envelope: ok + payload)
# ─────────────────────────────────────────────────────────────────────────────

class CategoryListResponse(BaseModel):
    ok: bool = True
    items: List[CategoryResponse]


class SingleCategoryResponse(BaseModel):
    ok: bool = True
    category: CategoryResponse


class SubcategoryListResponse(BaseModel):
    ok: bool = True
    items: List[SubcategoryResponse]


class SingleSubcategoryResponse(BaseModel):
    ok: bool = True
    subcategory: SubcategoryResponse


class OkResponse(BaseModel):
    ok: bool = True
    message: Optional[str] = None
