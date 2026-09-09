"""
Wishlist — API router.

URL mapping (API_CONTRACT.md § WISHLIST → implementation):

  GET    /wishlist                        ← fetch wishlist (product ids + count)
  POST   /wishlist/{productId}            ← add product (idempotent)
  DELETE /wishlist/{productId}            ← remove product
  POST   /wishlist/{productId}/toggle     ← add-if-absent / remove-if-present

Authorization: Customer JWT required on all endpoints.
              Guest wishlists remain localStorage-only (per spec — no merge path defined).

Response shape (all endpoints):
  { ok: true, items: [productId], count: number }
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_customer, get_db
from app.models.auth.user import UserModel
from app.services.commerce.wishlist_service import WishlistService

router = APIRouter(prefix="/wishlist", tags=["Wishlist"])


# ===========================================================================
# GET /wishlist — fetch current wishlist
# ===========================================================================

@router.get(
    "",
    summary="Get customer wishlist",
    description=(
        "Returns `{ ok: true, items: [productId], count }`.  \n"
        "Authorization: Customer JWT.  \n"
        "Creates an empty wishlist on first access."
    ),
)
async def get_wishlist(
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = WishlistService(db)
    return await service.get_wishlist(current_user.id)


# ===========================================================================
# POST /wishlist/{productId} — add a product (idempotent)
# ===========================================================================

@router.post(
    "/{product_id}",
    summary="Add product to wishlist (idempotent)",
    description=(
        "Adds `productId` to the wishlist. "
        "Calling this again for the same product is a no-op.  \n\n"
        "The product must exist and be storefront-visible (`PUBLISHED` and "
        "published) — an unknown or unavailable product is a 404.  \n"
        "Returns the updated `{ ok: true, items, count }`."
    ),
)
async def add_to_wishlist(
    product_id: str,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = WishlistService(db)
    return await service.add_product(current_user.id, product_id)


# ===========================================================================
# DELETE /wishlist/{productId} — remove a product
# ===========================================================================

@router.delete(
    "/{product_id}",
    summary="Remove product from wishlist",
    description=(
        "Removes `productId` from the wishlist. "
        "No-op if the product was not saved.  \n"
        "Returns the updated `{ ok: true, items, count }`."
    ),
)
async def remove_from_wishlist(
    product_id: str,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = WishlistService(db)
    return await service.remove_product(current_user.id, product_id)


# ===========================================================================
# POST /wishlist/{productId}/toggle — add-if-absent / remove-if-present
# ===========================================================================

@router.post(
    "/{product_id}/toggle",
    summary="Toggle product in wishlist",
    description=(
        "Adds the product if it is not currently saved (requiring it to exist "
        "and be storefront-visible); removes it if it is.  \n"
        "Returns the updated `{ ok: true, items, count }`."
    ),
)
async def toggle_wishlist(
    product_id: str,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = WishlistService(db)
    return await service.toggle_product(current_user.id, product_id)
