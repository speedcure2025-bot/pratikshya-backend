"""
WishlistService — server-side wishlist for authenticated customers.

Contract (API_CONTRACT.md § WISHLIST):
  GET    /wishlist              → get or create wishlist, return product ids + resolved products
  POST   /wishlist/{productId}  → add product id to wishlist (idempotent)
  DELETE /wishlist/{productId}  → remove product id from wishlist
  POST   /wishlist/{productId}/toggle → add if absent, remove if present

Application-level product validation (Phase 4):
  `commerce_wishlist_item.product_id` has no DB FK to `catalog_product`
  (existing schema — unchanged), so the service validates at the boundary
  that a product exists and is storefront-visible before it can be added.
  Orphan ids that pre-date this check are still returned verbatim by
  reads so clients can show an honest "no longer available" state and
  remove them; they are never silently dropped.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.models.catalog.product import ProductModel
from app.models.commerce.wishlist import WishlistModel
from app.models.commerce.wishlist_item import WishlistItemModel


class WishlistService:
    """Business logic for the customer wishlist."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_or_create_wishlist(self, customer_id: str) -> WishlistModel:
        """Return existing wishlist or create one for the customer."""
        stmt = select(WishlistModel).where(WishlistModel.customer_id == customer_id)
        result = await self.db.execute(stmt)
        wishlist = result.scalars().first()
        if not wishlist:
            wishlist = WishlistModel(customer_id=customer_id)
            self.db.add(wishlist)
            await self.db.flush()
        return wishlist

    def _to_response(self, wishlist: WishlistModel) -> dict:
        """Convert wishlist to the API response shape."""
        items = [item.product_id for item in (wishlist.items or [])]
        return {
            "ok": True,
            "items": items,
            "count": len(items),
        }

    async def _validate_product_savable(self, product_id: str) -> None:
        """
        Reject products that cannot be saved: they must exist and be
        storefront-visible (`PUBLISHED` + `published`), mirroring the cart's
        add-time rule. No DB constraint is invented — this is the
        application-level guard for the schema's missing FK.
        """
        stmt = select(ProductModel).where(ProductModel.id == product_id)
        result = await self.db.execute(stmt)
        product = result.scalars().first()
        if not product or product.status != "PUBLISHED" or not product.published:
            raise NotFoundException(f"Product '{product_id}' is not available.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_wishlist(self, customer_id: str) -> dict:
        """GET /wishlist — fetch current wishlist."""
        wishlist = await self._get_or_create_wishlist(customer_id)
        return self._to_response(wishlist)

    async def add_product(self, customer_id: str, product_id: str) -> dict:
        """POST /wishlist/{productId} — add a product (idempotent)."""
        wishlist = await self._get_or_create_wishlist(customer_id)

        # Check if already present
        already = any(item.product_id == product_id for item in (wishlist.items or []))
        if not already:
            # Application-level product existence/visibility check (the
            # wishlist_item table has no FK to catalog_product).
            await self._validate_product_savable(product_id)

            # Attach through the relationship (not the raw FK column) so the
            # already-loaded `wishlist.items` collection reflects the new
            # line immediately — a re-query would return the same identity-
            # mapped object with a stale collection.
            new_item = WishlistItemModel(wishlist=wishlist, product_id=product_id)
            self.db.add(new_item)
            await self.db.flush()

        return self._to_response(wishlist)

    async def remove_product(self, customer_id: str, product_id: str) -> dict:
        """DELETE /wishlist/{productId} — remove a product (no-op if absent)."""
        wishlist = await self._get_or_create_wishlist(customer_id)

        present = next(
            (item for item in (wishlist.items or []) if item.product_id == product_id),
            None,
        )
        if present is not None:
            # Keep the loaded collection consistent with the deletion.
            wishlist.items.remove(present)
            await self.db.delete(present)
            await self.db.flush()

        return self._to_response(wishlist)

    async def toggle_product(self, customer_id: str, product_id: str) -> dict:
        """POST /wishlist/{productId}/toggle — add if absent, remove if present."""
        wishlist = await self._get_or_create_wishlist(customer_id)
        present = any(item.product_id == product_id for item in (wishlist.items or []))

        if present:
            return await self.remove_product(customer_id, product_id)
        return await self.add_product(customer_id, product_id)
