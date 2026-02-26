"""
catalog_builder.py - Weekly Product Catalog Builder

Runs once per week (Sunday midnight via n8n) and uses RapidAPI to pull
trending Amazon products across all configured categories. Results are
saved to data/product_catalog.json.

The daily pipeline picks from this catalog instead of hitting any live API,
keeping RapidAPI usage to ~2 calls/week (1 search per category = 5-7 calls total
once per week).

Usage:
    python -m scripts.catalog_builder           # rebuild full catalog
    python -m scripts.catalog_builder --dry-run  # print results, don't save
"""

import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Optional
import requests
import config
from scripts.product_tracker import is_used

logger = logging.getLogger(__name__)

CATALOG_FILE = os.path.join(config.DATA_DIR, "product_catalog.json")
PRODUCTS_PER_CATEGORY = 7  # 7 × 5 categories = 35 products/week = ~5/day buffer


def _rapidapi_search(category: str, max_results: int = PRODUCTS_PER_CATEGORY) -> list[dict]:
    """Single RapidAPI search call for a category. Returns product list."""
    if not config.RAPIDAPI_KEY:
        logger.warning("No RAPIDAPI_KEY set — skipping")
        return []

    headers = {
        "X-RapidAPI-Key": config.RAPIDAPI_KEY,
        "X-RapidAPI-Host": config.RAPIDAPI_HOST,
    }

    queries = {
        "Electronics": "best seller electronics gadgets",
        "Beauty":      "best seller beauty skincare",
        "Kitchen":     "best seller kitchen cooking",
        "Toys":        "best seller toys kids",
        "Sports":      "best seller sports fitness outdoor",
        "Home":        "best seller home decor",
        "Garden":      "best seller garden outdoor",
    }
    query = queries.get(category, f"best seller {category}")

    try:
        resp = requests.get(
            f"https://{config.RAPIDAPI_HOST}/search",
            headers=headers,
            params={"query": query, "country": "US", "page": 1},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        items = (data.get("data") or {}).get("products") or data.get("products") or []
    except Exception as e:
        logger.error(f"[CatalogBuilder] RapidAPI search failed for {category}: {e}")
        return []

    products = []
    for item in items[:max_results]:
        asin = item.get("asin") or item.get("ASIN") or ""
        if not asin:
            continue

        title        = item.get("product_title") or item.get("title") or "Amazon Product"
        price        = item.get("product_price") or item.get("price") or "Check Amazon"
        rating       = item.get("product_star_rating") or item.get("rating") or "N/A"
        review_count = item.get("product_num_ratings") or item.get("review_count") or 0
        thumbnail    = item.get("product_photo") or item.get("thumbnail") or item.get("image") or ""

        # Clean price range strings
        if isinstance(price, str):
            price = price.split("–")[0].split("-")[0].strip()

        images = [thumbnail] if thumbnail and thumbnail.startswith("http") else []

        products.append({
            "asin":         asin,
            "title":        title,
            "price":        price,
            "rating":       rating,
            "review_count": review_count,
            "category":     category,
            "images":       images,
            "affiliate_url": f"https://www.amazon.com/dp/{asin}?tag={config.AMAZON_PARTNER_TAG}",
            "fetched_at":   datetime.now(timezone.utc).isoformat(),
        })

    logger.info(f"[CatalogBuilder] {category}: fetched {len(products)} products")
    return products


def _rapidapi_details(asin: str) -> list[str]:
    """Fetch high-res product images for a single ASIN. Returns image URL list."""
    if not config.RAPIDAPI_KEY:
        return []

    headers = {
        "X-RapidAPI-Key": config.RAPIDAPI_KEY,
        "X-RapidAPI-Host": config.RAPIDAPI_HOST,
    }
    try:
        resp = requests.get(
            f"https://{config.RAPIDAPI_HOST}/product-details",
            headers=headers,
            params={"asin": asin, "country": "US"},
            timeout=20,
        )
        resp.raise_for_status()
        d = resp.json().get("data") or resp.json()
        photos = d.get("product_photos") or d.get("images") or []
        images = [p for p in photos if p and p.startswith("http")][:5]
        return images
    except Exception as e:
        logger.warning(f"[CatalogBuilder] Details fetch failed for {asin}: {e}")
        return []


def load_catalog() -> dict:
    """Load the existing product catalog from disk."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    if not os.path.exists(CATALOG_FILE):
        return {"built_at": None, "products": []}
    try:
        with open(CATALOG_FILE) as f:
            return json.load(f)
    except Exception:
        logger.warning("[CatalogBuilder] Corrupt catalog — starting fresh")
        return {"built_at": None, "products": []}


def save_catalog(catalog: dict):
    """Save catalog to disk."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(CATALOG_FILE, "w") as f:
        json.dump(catalog, f, indent=2)
    logger.info(f"[CatalogBuilder] Catalog saved: {len(catalog['products'])} products → {CATALOG_FILE}")


def build_catalog(dry_run: bool = False, fetch_details: bool = True) -> dict:
    """
    Pull fresh products from RapidAPI for all categories.
    Merges with existing catalog — keeps unused products, adds new ones.

    fetch_details: if True, makes one extra API call per product to get
                   high-res images. Set False to minimize API usage.
    """
    categories = list(config.AMAZON_CATEGORIES)
    existing   = load_catalog()

    # Index existing products by ASIN for fast lookup
    existing_by_asin = {p["asin"]: p for p in existing.get("products", [])}

    new_products = []
    total_api_calls = 0

    for i, category in enumerate(categories):
        logger.info(f"[CatalogBuilder] Fetching category {i+1}/{len(categories)}: {category}")
        products = _rapidapi_search(category)
        total_api_calls += 1

        for product in products:
            asin = product["asin"]

            # Skip if already in catalog with good images
            if asin in existing_by_asin:
                existing_p = existing_by_asin[asin]
                if len(existing_p.get("images", [])) >= 2:
                    logger.debug(f"[CatalogBuilder] Skipping {asin} — already in catalog with images")
                    continue

            # Fetch high-res images if thumbnail only
            if fetch_details and len(product.get("images", [])) < 2:
                logger.info(f"[CatalogBuilder] Fetching images for {asin}: {product['title'][:40]}")
                images = _rapidapi_details(asin)
                if images:
                    product["images"] = images
                total_api_calls += 1
                time.sleep(0.5)  # gentle rate limit

            new_products.append(product)

        # Gentle pause between categories
        if i < len(categories) - 1:
            time.sleep(1)

    # Merge: keep existing unused products + add new ones
    existing_unused = [
        p for p in existing.get("products", [])
        if not is_used(p["asin"]) and p["asin"] not in {np["asin"] for np in new_products}
    ]

    merged = new_products + existing_unused
    random.shuffle(merged)

    catalog = {
        "built_at":        datetime.now(timezone.utc).isoformat(),
        "product_count":   len(merged),
        "categories":      categories,
        "total_api_calls": total_api_calls,
        "products":        merged,
    }

    logger.info(
        f"[CatalogBuilder] Done — {len(new_products)} new + {len(existing_unused)} kept "
        f"= {len(merged)} total ({total_api_calls} API calls)"
    )

    if not dry_run:
        save_catalog(catalog)

    return catalog


def pick_from_catalog() -> Optional[dict]:
    """
    Pick a fresh unused product from the catalog.
    Called by the daily pipeline instead of hitting any live API.
    Returns None if catalog is empty or all products used.
    """
    catalog = load_catalog()
    products = catalog.get("products", [])

    if not products:
        logger.warning("[CatalogBuilder] Catalog is empty — needs rebuild")
        return None

    # Prefer unused products
    fresh = [p for p in products if not is_used(p["asin"])]

    if not fresh:
        logger.warning("[CatalogBuilder] All catalog products used — consider rebuilding")
        # Fall back to any product (pipeline can recycle)
        fresh = products

    # Pick randomly from top half to add variety
    pool = fresh[:max(1, len(fresh) // 2)] if len(fresh) > 4 else fresh
    product = random.choice(pool)

    built_at = catalog.get("built_at", "unknown")
    logger.info(
        f"[CatalogBuilder] Picked from catalog: {product['title']} "
        f"({len(fresh)} unused remaining, catalog built {built_at[:10]})"
    )
    return product


def catalog_status() -> dict:
    """Return a summary of current catalog state."""
    catalog = load_catalog()
    products = catalog.get("products", [])
    unused   = [p for p in products if not is_used(p["asin"])]
    by_cat   = {}
    for p in unused:
        cat = p.get("category", "Unknown")
        by_cat[cat] = by_cat.get(cat, 0) + 1

    return {
        "built_at":      catalog.get("built_at"),
        "total":         len(products),
        "unused":        len(unused),
        "used":          len(products) - len(unused),
        "days_remaining": len(unused),  # 1 video/day
        "by_category":   by_cat,
    }


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Build weekly product catalog from RapidAPI")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't save")
    parser.add_argument("--status", action="store_true", help="Show catalog status only")
    parser.add_argument("--no-details", action="store_true", help="Skip per-product details calls (fewer API calls)")
    args = parser.parse_args()

    if args.status:
        status = catalog_status()
        print(json.dumps(status, indent=2))
    else:
        catalog = build_catalog(dry_run=args.dry_run, fetch_details=not args.no_details)
        print(json.dumps({
            "built_at":        catalog["built_at"],
            "product_count":   catalog["product_count"],
            "total_api_calls": catalog["total_api_calls"],
            "dry_run":         args.dry_run,
        }, indent=2))
