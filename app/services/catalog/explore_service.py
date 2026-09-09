"""
ExploreService — business logic for the Explore surface.

Contract reference: API_CONTRACT.md § EXPLORE

Endpoints:
  GET /explore        — paginated product stream with interleaved promo/editorial cards
  GET /explore/offers — offer strip inside the stream
  GET /home           — homepage assembly

Key rules reproduced from the frontend:

  buildExploreStream():
    - Products are the primary content; promo and editorial cards are interleaved.
    - EXPLORE_PROMO_AFTER   = 4  → insert a promo card after every 4th product
    - EXPLORE_EDITORIAL_AFTER = 8 → insert an editorial card after every 8th product
    - Insertion is counted across the full page of products, not the combined stream.

  GET /home reservation rule:
    - Hero media plates are reserved first.
    - Category, collection and sale seam images then exclude those media ids so no
      plate appears twice on one page.
    - Implemented here: each seam builder tracks used_media_ids and skips duplicates.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog.category import CategoryModel
from app.models.catalog.product import ProductModel
from app.schemas.catalog.explore import (
    EXPLORE_EDITORIAL_AFTER,
    EXPLORE_PROMO_AFTER,
    CategoryCard,
    EditorialCard,
    ExploreOffer,
    ExploreOffersResponse,
    ExploreQuery,
    ExploreResponse,
    HomeResponse,
    HomepageSection,
    HomepageSaleBanner,
    HeroSlide,
    ProductCard,
    PromoCard,
)
from app.schemas.catalog.product import ProductListQuery, StorefrontProduct
from app.services.catalog.product_service import ProductService


# ── Static promo / editorial content ─────────────────────────────────────────
# Mirrors the frontend's static promo and editorial data.
# In production these would be fetched from a CMS / database table.

_PROMO_CARDS: List[Dict[str, Any]] = [
    {
        "id": "promo-new-arrivals",
        "title": "New Arrivals",
        "subtitle": "Fresh styles just landed",
        "cta": "Shop Now",
        "href": "/products?sort=newest",
        "image": "",
        "badge": "NEW",
    },
    {
        "id": "promo-sale",
        "title": "Festive Sale",
        "subtitle": "Up to 40% off on select styles",
        "cta": "Grab the Deal",
        "href": "/products?sort=discount",
        "image": "",
        "badge": "SALE",
    },
    {
        "id": "promo-bridal",
        "title": "Bridal Couture",
        "subtitle": "Crafted for your special day",
        "cta": "Explore",
        "href": "/products?category=bridal-couture",
        "image": "",
        "badge": "",
    },
]

_EDITORIAL_CARDS: List[Dict[str, Any]] = [
    {
        "id": "editorial-silk-sarees",
        "title": "The Silk Story",
        "subtitle": "Heritage weaves reimagined",
        "body": "Discover our curated collection of handwoven silk sarees.",
        "cta": "Read More",
        "href": "/products?fabric=silk",
        "image": "",
    },
    {
        "id": "editorial-festive",
        "title": "Festive Favourites",
        "subtitle": "Dress up the celebration",
        "body": "Occasion wear that makes every moment memorable.",
        "cta": "Shop the Edit",
        "href": "/products?occasion=festive",
        "image": "",
    },
]

_EXPLORE_OFFERS: List[ExploreOffer] = [
    ExploreOffer(
        id="offer-first-order",
        title="First Order Discount",
        description="Get 10% off on your first order",
        code="FIRST10",
        discountType="percentage",
        discountValue=10.0,
        minOrderValue=500,
        badge="NEW USER",
        href="/products",
    ),
    ExploreOffer(
        id="offer-free-shipping",
        title="Free Shipping",
        description="Free delivery on orders above ₹5,000",
        code="",
        discountType="fixed",
        discountValue=99.0,
        minOrderValue=5000,
        badge="FREE SHIP",
        href="/products",
    ),
    ExploreOffer(
        id="offer-festive",
        title="Festive Sale",
        description="Up to 40% off on festive wear",
        code="FESTIVE40",
        discountType="percentage",
        discountValue=40.0,
        minOrderValue=1000,
        badge="FESTIVE",
        href="/products?occasion=festive",
    ),
]


# ── Service ───────────────────────────────────────────────────────────────────

class ExploreService:
    """Business logic for the Explore and Home surfaces."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._product_service = ProductService(db)

    # ── GET /explore ──────────────────────────────────────────────────────────

    async def get_explore(self, query: ExploreQuery) -> ExploreResponse:
        """
        GET /explore

        1. Delegate to ProductService.list_storefront_products() for
           visibility-gated, filtered, sorted, paginated products.
        2. Build the interleaved stream via buildExploreStream():
             - After every EXPLORE_PROMO_AFTER     products → insert a promo card
             - After every EXPLORE_EDITORIAL_AFTER products → insert an editorial card
             - (When both thresholds coincide, editorial takes priority)
        3. Return: items, total, page, pageSize, hasMore, stream
        """
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

        items: List[StorefrontProduct] = result.get("items", [])
        total: int = result.get("total", 0)
        page_size = query.page_size
        page = query.page
        has_more = (page * page_size) < total

        stream = self._build_explore_stream(items, page)

        return ExploreResponse(
            ok=True,
            items=items,
            total=total,
            page=page,
            pageSize=page_size,
            hasMore=has_more,
            stream=stream,
        )

    def _build_explore_stream(
        self, products: List[StorefrontProduct], page: int
    ) -> List[Any]:
        """
        Reproduce buildExploreStream() from the frontend.

        Interleaving (counted by product index within the page):
          - product index + 1 is divisible by EXPLORE_EDITORIAL_AFTER → editorial card
          - product index + 1 is divisible by EXPLORE_PROMO_AFTER      → promo card
          (editorial checked first so it wins when both fire together)

        Promo/editorial content rotates through the static lists using the
        global product position (page-offset + local index) so cards stay
        consistent across pages.
        """
        stream: List[Any] = []
        promo_idx = 0
        editorial_idx = 0

        # Global offset so cards don't repeat the same item every page
        page_offset = (page - 1) * len(products)

        for local_idx, product in enumerate(products):
            global_pos = page_offset + local_idx

            stream.append(ProductCard(kind="product", product=product).model_dump(by_alias=True))

            one_based = local_idx + 1

            if EXPLORE_EDITORIAL_AFTER > 0 and one_based % EXPLORE_EDITORIAL_AFTER == 0:
                card_data = _EDITORIAL_CARDS[editorial_idx % len(_EDITORIAL_CARDS)]
                stream.append(
                    EditorialCard(
                        kind="editorial",
                        position=global_pos,
                        **card_data,
                    ).model_dump(by_alias=True)
                )
                editorial_idx += 1

            elif EXPLORE_PROMO_AFTER > 0 and one_based % EXPLORE_PROMO_AFTER == 0:
                card_data = _PROMO_CARDS[promo_idx % len(_PROMO_CARDS)]
                stream.append(
                    PromoCard(
                        kind="promo",
                        position=global_pos,
                        **card_data,
                    ).model_dump(by_alias=True)
                )
                promo_idx += 1

        return stream

    # ── GET /explore/offers ───────────────────────────────────────────────────

    async def get_explore_offers(self) -> ExploreOffersResponse:
        """
        GET /explore/offers

        Returns the offer strip displayed inside the explore stream.
        Static content today; a real offer table is BACKEND DECISION REQUIRED.
        """
        return ExploreOffersResponse(ok=True, offers=_EXPLORE_OFFERS)

    # ── GET /home ─────────────────────────────────────────────────────────────

    async def get_home(self) -> HomeResponse:
        """
        GET /home — B-02: backend-managed HOME_HERO.

        Assembles the homepage in one call:
          1. hero_slides         — active HOME_HERO from media_marketing_media
                                   (ordered, active-only, duplicate-safe);
                                   fallback to canonical 5 hero assets when
                                   no active entries or DB unavailable.
          2. new_arrivals        — up to 12 newest PUBLISHED products
          3. categories          — all ACTIVE categories (top-level cards)
          4. saree_edit          — up to 8 saree products
          5. bride_groom_edit    — up to 4 bridal + 4 menswear products
          6. celebration_edit    — up to 8 festive-occasion products
          7. sale_banner         — static sale banner

        Hero plates are reserved first; each subsequent seam excludes already-used
        media ids so no image appears twice on the page.
        """
        # Tracked media ids — seams exclude images already reserved by heroes.
        used_media_ids: set = set()

        # 1. Hero slides — backend-managed HOME_HERO with canonical fallback
        hero_slides = await self._build_hero_slides_from_db(used_media_ids)
        if not hero_slides:
            # Honest empty when marketing table exists but has zero active rows
            # is valid — but for resilience when DB unavailable or no seed,
            # fallback to canonical 5 hero assets (development/emergency fallback).
            # The fallback is documented and does NOT override valid DB config.
            try:
                # Try to detect if marketing table has any rows at all (even inactive)
                # If it has rows but zero active, return honest empty (case B).
                from app.services.media.marketing_media_service import MarketingMediaService

                svc = MarketingMediaService(self.db)
                all_rows = await svc.list(placement="HOME_HERO", active_only=False)
                if len(all_rows) == 0:
                    # No rows at all — use canonical fallback for resilience
                    hero_slides = self._build_hero_slides(used_media_ids)
                else:
                    # Rows exist but none active — honest empty per spec case B
                    hero_slides = []
            except Exception:
                # DB unavailable — canonical fallback
                hero_slides = self._build_hero_slides(used_media_ids)

        # Helper to safely select products when DB is unavailable
        async def safe_select(**kwargs):
            try:
                return await self._select_products(**kwargs)
            except Exception:
                return []

        async def safe_categories():
            try:
                return await self._build_category_cards(used_media_ids)
            except Exception:
                return []

        # 2. New arrivals — 12 newest published products
        new_arrivals = await safe_select(
            sort="newest", limit=12, used_media_ids=used_media_ids
        )

        # 3. Categories — all active (fetched from product table's category values)
        categories = await safe_categories()

        # 4. Saree edit
        saree_products = await safe_select(
            category="sarees", sort="recommended", limit=8,
            used_media_ids=used_media_ids
        )
        saree_edit = HomepageSection(
            id="saree-edit",
            title="The Saree Edit",
            subtitle="Handpicked heritage weaves",
            href="/products?category=sarees",
            products=saree_products,
        )

        # 5. Bride & Groom edit — mix bridal couture + menswear
        bridal_products = await safe_select(
            category="bridal-couture", sort="recommended", limit=4,
            used_media_ids=used_media_ids
        )
        mens_products = await safe_select(
            category="menswear", sort="recommended", limit=4,
            used_media_ids=used_media_ids
        )
        bride_groom_edit = HomepageSection(
            id="bride-groom-edit",
            title="Bride & Groom Edit",
            subtitle="Crafted for your special day",
            href="/products?collection=bridal",
            products=bridal_products + mens_products,
        )

        # 6. Celebration edit — festive occasion products
        celebration_products = await safe_select(
            occasion="festive", sort="recommended", limit=8,
            used_media_ids=used_media_ids
        )
        celebration_edit = HomepageSection(
            id="celebration-edit",
            title="The Celebration Edit",
            subtitle="Dress up the occasion",
            href="/products?occasion=festive",
            products=celebration_products,
        )

        # 7. Sale banner (static)
        sale_banner = HomepageSaleBanner(
            id="sale-banner",
            title="Festive Sale — Up to 40% Off",
            subtitle="Limited time offer on select styles",
            cta="Shop the Sale",
            href="/products?sort=discount",
            image="",
        )

        return HomeResponse(
            ok=True,
            heroSlides=hero_slides,
            newArrivals=new_arrivals,
            categories=categories,
            sareeEdit=saree_edit,
            brideGroomEdit=bride_groom_edit,
            celebrationEdit=celebration_edit,
            saleBanner=sale_banner,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _build_hero_slides_from_db(self, used_media_ids: set) -> List[HeroSlide]:
        """
        Load active HOME_HERO from media_marketing_media (ordered, active-only).

        Returns [] if no active entries or on DB failure — caller decides
        whether to use canonical fallback or honest empty.
        """
        try:
            from app.services.media.marketing_media_service import MarketingMediaService
            from app.storage.urls import build_media_url

            svc = MarketingMediaService(self.db)
            rows = await svc.list_active_home_hero()

            slides: List[HeroSlide] = []
            seen_keys: set = set()
            for row in rows:
                # Duplicate prevention within same placement (DB has unique constraint,
                # but also guard in memory)
                if row.object_key in seen_keys:
                    continue
                seen_keys.add(row.object_key)

                try:
                    image_url = build_media_url(row.object_key)
                except Exception:
                    image_url = f"/api/v1/media/objects/{row.object_key}"

                # Track for reservation rule
                used_media_ids.add(row.object_key)
                used_media_ids.add(image_url)

                slides.append(
                    HeroSlide(
                        id=row.id,
                        title=row.title or "PRATIKSHYA FASHON",
                        subtitle=row.subtitle or "",
                        cta=row.cta_label or "Explore Collection",
                        href=row.cta_href or "/shop",
                        image=image_url,
                        media_id=row.object_key,
                    )
                )
            return slides
        except Exception:
            # DB unavailable or table not yet migrated — let caller fallback
            return []

    @staticmethod
    def _build_hero_slides(used_media_ids: set) -> List[HeroSlide]:
        """
        Canonical hero slides — five editorial plates from the shipped
        hero asset library (hero001..hero005). Images are served through
        the canonical media object store (`/api/v1/media/objects/hero/...`),
        so the frontend's media resolver and Vite proxy can fetch them
        without hardcoding a filesystem path. The frontend's hero.js proxy
        filters on id && image, so image must be a real URL.

        Reservation: hero media ids are tracked so downstream seams skip
        duplicate plates (homepage reservation rule).
        """
        from app.storage.urls import build_media_url

        # Five canonical hero assets — order matches HOMEPAGE_HERO_THEMES
        # festive, bridal, heritage, celebration, arrivals
        hero_assets = [
            ("hero-1", "hero001.avif", "Festive Elegance", "Handwoven stories for the season of celebration", "Explore Collection", "/shop", "festive"),
            ("hero-2", "hero002.avif", "Bridal Couture", "Crafted for your most special day", "View Bridal", "/bridal", "bridal"),
            ("hero-3", "hero003.avif", "Heritage Weaves", "Six yards of timeless craft", "Shop Sarees", "/women/sarees", "heritage"),
            ("hero-4", "hero004.avif", "The Celebration Edit", "Dress up every moment", "Shop the Edit", "/shop", "celebration"),
            ("hero-5", "hero005.avif", "New Arrivals", "Fresh drapes, just landed", "Shop New", "/shop", "arrivals"),
        ]

        slides: List[HeroSlide] = []
        for slide_id, filename, title, subtitle, cta, href, _theme in hero_assets:
            object_key = f"hero/{filename}"
            try:
                image_url = build_media_url(object_key)
            except Exception:
                # Fallback to the application-level URL shape if builder fails
                image_url = f"/api/v1/media/objects/{object_key}"
            media_id = object_key
            used_media_ids.add(media_id)
            used_media_ids.add(image_url)
            slides.append(
                HeroSlide(
                    id=slide_id,
                    title=title,
                    subtitle=subtitle,
                    cta=cta,
                    href=href,
                    image=image_url,
                    media_id=media_id,
                )
            )

        return slides

    async def _select_products(
        self,
        *,
        sort: str = "recommended",
        limit: int = 8,
        category: Optional[str] = None,
        occasion: Optional[str] = None,
        used_media_ids: Optional[set] = None,
    ) -> List[StorefrontProduct]:
        """
        Fetch published products for a homepage seam.
        Products whose primary image is already in used_media_ids are skipped
        to honour the reservation rule.
        """
        product_query = ProductListQuery(
            category=category,
            occasion=occasion,
            sort=sort,
            page=1,
            # Fetch more than needed to account for media deduplication
            pageSize=limit * 2,
        )

        result = await self._product_service.list_storefront_products(product_query)
        all_items: List[StorefrontProduct] = result.get("items", [])

        selected: List[StorefrontProduct] = []
        for item in all_items:
            if len(selected) >= limit:
                break
            # Reserve and skip images already used by earlier seams
            img_key = item.primary_media_id or item.image
            if used_media_ids is not None:
                if img_key and img_key in used_media_ids:
                    continue
                if img_key:
                    used_media_ids.add(img_key)
            selected.append(item)

        return selected

    async def _build_category_cards(self, used_media_ids: set) -> List[CategoryCard]:
        """
        Build shop-by-category cards from distinct category values on
        published products.  Image resolution is BACKEND DECISION REQUIRED.
        """
        stmt = (
            select(ProductModel.category)
            .where(
                ProductModel.status == "PUBLISHED",
                ProductModel.published.is_(True),
                ProductModel.category.isnot(None),
                ProductModel.category != "",
            )
            .distinct()
        )
        result = await self.db.execute(stmt)
        categories = [row[0] for row in result if row[0]]

        category_rows = (await self.db.execute(select(CategoryModel))).scalars().all()
        status_map = {}
        for category in category_rows:
            if category.id:
                status_map[category.id] = category.status
            if category.slug:
                status_map[category.slug] = category.status
            if category.name:
                status_map[category.name] = category.status
        categories = [cat for cat in categories if status_map.get(cat, "ACTIVE") == "ACTIVE"]

        return [
            CategoryCard(
                id=cat,
                name=cat.replace("-", " ").title(),
                slug=cat,
                image="",
                href=f"/products?category={cat}",
            )
            for cat in sorted(categories)
        ]
