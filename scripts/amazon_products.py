"""
amazon_products.py - Amazon Product Advertising API 5.0
Fetches bestselling products by category and returns product data
including images and affiliate-tagged URLs.

Cost: FREE (with active Amazon Associates account)
"""
import logging
import random
import time
from typing import Optional
import requests
import hmac
import hashlib
import datetime
import json
from urllib.parse import quote
import config
from scripts.product_tracker import is_used

logger = logging.getLogger(__name__)

# Amazon category node IDs (BrowseNodeIds)
CATEGORY_NODES = {
    "Electronics": "172282",
    "Beauty": "3760911",
    "Kitchen": "284507",
    "Toys": "165793011",
    "Sports": "3375251",
    "Home": "1055398",
    "Garden": "3238155",
    "Books": "283155",
}


def _sign_request(method: str, host: str, path: str, params: dict, secret_key: str, access_key: str) -> dict:
    """AWS Signature Version 4 for PA-API 5.0"""
    service = "ProductAdvertisingAPI"
    region = config.AMAZON_REGION
    endpoint = f"https://{host}{path}"
    
    # Timestamp
    t = datetime.datetime.utcnow()
    amz_date = t.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = t.strftime("%Y%m%d")
    
    payload = json.dumps(params)
    payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    
    canonical_headers = (
        f"content-encoding:amz-1.0\n"
        f"content-type:application/json; charset=UTF-8\n"
        f"host:{host}\n"
        f"x-amz-date:{amz_date}\n"
        f"x-amz-target:com.amazon.paapi5.v1.ProductAdvertisingAPIv1.{path.split('/')[-1]}\n"
    )
    signed_headers = "content-encoding;content-type;host;x-amz-date;x-amz-target"
    
    canonical_request = "\n".join([
        method,
        path,
        "",  # No query string
        canonical_headers,
        signed_headers,
        payload_hash,
    ])
    
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    
    def sign(key, msg):
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()
    
    signing_key = sign(sign(sign(sign(
        f"AWS4{secret_key}".encode("utf-8"), date_stamp),
        region), service), "aws4_request")
    
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    
    auth_header = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    
    headers = {
        "content-encoding": "amz-1.0",
        "content-type": "application/json; charset=UTF-8",
        "host": host,
        "x-amz-date": amz_date,
        "x-amz-target": f"com.amazon.paapi5.v1.ProductAdvertisingAPIv1.{path.split('/')[-1]}",
        "Authorization": auth_header,
    }
    return headers, endpoint, payload


def build_affiliate_url(asin: str) -> str:
    """Build an Amazon product URL with affiliate tag."""
    return f"https://www.amazon.com/dp/{asin}?tag={config.AMAZON_PARTNER_TAG}"


def _rapidapi_products(category: str = None, max_results: int = 10) -> list[dict]:
    """
    Fetch products via RapidAPI (real-time-amazon-data).
    Used as fallback when PA API is not yet active.

    COST OPTIMIZED: single search call returns multiple products with thumbnail
    images included. No per-product details calls — keeps usage at 1 API call
    per pipeline run. A details call is only made if the chosen product has no
    usable images from the search result.
    """
    if not config.RAPIDAPI_KEY:
        return []

    if category is None:
        category = random.choice(config.AMAZON_CATEGORIES)

    headers = {
        "X-RapidAPI-Key":  config.RAPIDAPI_KEY,
        "X-RapidAPI-Host": config.RAPIDAPI_HOST,
    }

    # Single search call — returns title, price, rating, thumbnail per product
    search_url = f"https://{config.RAPIDAPI_HOST}/search"
    try:
        resp = requests.get(search_url, headers=headers, params={
            "query": f"best seller {category}",
            "country": "US",
            "page": 1,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = (data.get("data") or {}).get("products") or data.get("products") or []
    except Exception as e:
        logger.error(f"RapidAPI search failed: {e}")
        return []

    products = []
    for item in items[:max_results]:
        asin = item.get("asin") or item.get("ASIN") or ""
        if not asin:
            continue

        title  = item.get("product_title") or item.get("title") or "Amazon Product"
        price  = item.get("product_price") or item.get("price") or "Check Amazon"
        rating = item.get("product_star_rating") or item.get("rating") or "N/A"
        review_count = item.get("product_num_ratings") or item.get("review_count") or 0
        thumbnail = item.get("product_photo") or item.get("thumbnail") or item.get("image") or ""

        # Clean price string (e.g. "$19.99 – $24.99" → "$19.99")
        if isinstance(price, str):
            price = price.split("–")[0].split("-")[0].strip()

        images = [thumbnail] if thumbnail and thumbnail.startswith("http") else []

        products.append({
            "asin":          asin,
            "title":         title,
            "images":        images,
            "price":         price,
            "rating":        rating,
            "review_count":  review_count,
            "category":      category,
            "affiliate_url": build_affiliate_url(asin),
            "_rapidapi":     True,  # Flag — details not fetched yet
        })

    logger.info(f"[RapidAPI] Search returned {len(products)} products for '{category}' (1 API call)")
    return products


def _rapidapi_fetch_details(asin: str, product: dict) -> dict:
    """
    Fetch full product photos for a chosen product.
    Only called ONCE per pipeline run after a product is selected.
    Cost: 1 additional API call.
    """
    if not config.RAPIDAPI_KEY:
        return product

    headers = {
        "X-RapidAPI-Key":  config.RAPIDAPI_KEY,
        "X-RapidAPI-Host": config.RAPIDAPI_HOST,
    }
    try:
        resp = requests.get(
            f"https://{config.RAPIDAPI_HOST}/product-details",
            headers=headers,
            params={"asin": asin, "country": "US"},
            timeout=15,
        )
        resp.raise_for_status()
        d = resp.json().get("data") or resp.json()

        photos = d.get("product_photos") or d.get("images") or []
        images = [p for p in photos if p and p.startswith("http")][:5]
        if images:
            product = {**product, "images": images}
            logger.info(f"[RapidAPI] Fetched {len(images)} full images for {asin} (1 API call)")
    except Exception as e:
        logger.warning(f"[RapidAPI] Details fetch failed (non-fatal): {e}")

    return product


def search_bestsellers(category: str = None, max_results: int = 10) -> list[dict]:
    """
    Search Amazon for bestselling products in a category.
    Priority: PA API → RapidAPI → mock data
    Returns list of product dicts with title, asin, images, price, rating, url.
    """
    if not config.AMAZON_ACCESS_KEY or not config.AMAZON_SECRET_KEY:
        logger.warning("Amazon PA API keys not set — trying RapidAPI...")
        results = _rapidapi_products(category, max_results)
        return results if results else _mock_products()
    
    if category is None:
        category = random.choice(config.AMAZON_CATEGORIES)
    
    browse_node = CATEGORY_NODES.get(category, "172282")
    
    path = "/paapi5/searchitems"
    params = {
        "PartnerTag": config.AMAZON_PARTNER_TAG,
        "PartnerType": "Associates",
        "Marketplace": "www.amazon.com",
        "Keywords": f"best seller {category}",
        "BrowseNodeId": browse_node,
        "SortBy": "Featured",
        "ItemCount": max_results,
        "Resources": [
            "Images.Primary.Large",
            "Images.Variants.Large",
            "ItemInfo.Title",
            "ItemInfo.ByLineInfo",
            "Offers.Listings.Price",
            "CustomerReviews.Count",
            "CustomerReviews.StarRating",
        ],
    }
    
    try:
        headers, endpoint, payload = _sign_request(
            "POST", config.AMAZON_HOST, path, params,
            config.AMAZON_SECRET_KEY, config.AMAZON_ACCESS_KEY
        )
        resp = requests.post(endpoint, data=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        items = data.get("SearchResult", {}).get("Items", [])
        products = []
        for item in items:
            asin = item.get("ASIN", "")
            title = item.get("ItemInfo", {}).get("Title", {}).get("DisplayValue", "Unknown Product")
            
            # Images
            images = []
            primary = item.get("Images", {}).get("Primary", {}).get("Large", {}).get("URL")
            if primary:
                images.append(primary)
            variants = item.get("Images", {}).get("Variants", [])
            for v in (variants or [])[:4]:
                url = v.get("Large", {}).get("URL")
                if url:
                    images.append(url)
            
            # Price
            listing = item.get("Offers", {}).get("Listings", [{}])[0]
            price = listing.get("Price", {}).get("DisplayAmount", "Check Amazon for price")
            
            # Rating
            reviews = item.get("CustomerReviews", {})
            rating = reviews.get("StarRating", {}).get("Value", "N/A")
            review_count = reviews.get("Count", {}).get("Value", 0)
            
            products.append({
                "asin": asin,
                "title": title,
                "images": images,
                "price": price,
                "rating": rating,
                "review_count": review_count,
                "category": category,
                "affiliate_url": build_affiliate_url(asin),
            })
        
        logger.info(f"Found {len(products)} products in {category}")
        return products
        
    except Exception as e:
        logger.error(f"Amazon PA API error: {e} — trying RapidAPI fallback...")
        results = _rapidapi_products(category, max_results)
        return results if results else _mock_products()


def pick_fresh_product(categories: list[str] = None) -> Optional[dict]:
    """
    Pick a fresh product.
    Priority: weekly catalog → live RapidAPI → mock fallback
    """
    # ── Try weekly catalog first (no API calls) ───────────────────────────────
    try:
        from scripts.catalog_builder import pick_from_catalog, catalog_status
        status = catalog_status()
        if status["unused"] > 0:
            product = pick_from_catalog()
            if product:
                return product
        else:
            logger.warning("[pick_fresh_product] Catalog empty or exhausted — falling back to live API")
    except Exception as e:
        logger.warning(f"[pick_fresh_product] Catalog lookup failed: {e} — falling back to live API")

    return _pick_fresh_live(categories)


def _pick_fresh_live(categories: list[str] = None) -> Optional[dict]:
    """
    Pick a product we haven't used yet.

    PA API active  → loop categories, up to N PA API calls
    RapidAPI mode  → 1 search call + 1 details call max (cost-optimized)
    Mock fallback  → no API calls
    """
    cats = list(categories or config.AMAZON_CATEGORIES)
    random.shuffle(cats)

    pa_api_active = bool(config.AMAZON_ACCESS_KEY and config.AMAZON_SECRET_KEY)

    # ── RapidAPI path (cost-optimized: 2 calls max) ───────────────────────────
    if not pa_api_active and config.RAPIDAPI_KEY:
        category = cats[0]  # Single search — one category, one call
        products = _rapidapi_products(category)
        if not products:
            logger.warning("[RapidAPI] No products returned — falling back to mock")
            return random.choice(_mock_products())

        fresh = [p for p in products if not is_used(p["asin"])]
        pool  = fresh if fresh else products  # reuse any if all used
        product = random.choice(pool[:5])

        # Fetch full images only for the chosen product (1 details call)
        if not product.get("images"):
            product = _rapidapi_fetch_details(product["asin"], product)
        else:
            # Have thumbnail from search — get high-res details (optional, 1 call)
            product = _rapidapi_fetch_details(product["asin"], product)

        logger.info(f"[RapidAPI] Selected: {product['title']} ({len(product.get('images', []))} images)")
        return product

    # ── PA API path (or mock fallback) ────────────────────────────────────────
    for category in cats:
        products = search_bestsellers(category)

        if products and products[0].get("_mock"):
            product = random.choice(products)
            logger.info(f"[MOCK] Selected: {product['title']}")
            return product

        fresh = [p for p in products if not is_used(p["asin"])]
        if fresh:
            product = random.choice(fresh)
            logger.info(f"Selected: {product['title']} (ASIN: {product['asin']})")
            return product

        time.sleep(1)

    logger.warning("No fresh products found — falling back to mock")
    return random.choice(_mock_products())


def _mock_products() -> list[dict]:
    """
    Mock data for testing without PA API keys.
    Uses picsum.photos for reliable placeholder images (seeded by ASIN = consistent).
    NOTE: Mock products are flagged so deduplication is skipped.
    """
    products = [
        {
            "asin": "B08N5KWB9H",
            "title": "Echo Dot (4th Gen) | Smart speaker with Alexa",
            "images": [
                "https://picsum.photos/seed/B08N5KWB9H/800/800",
                "https://picsum.photos/seed/B08N5KWB9H-2/800/800",
            ],
            "price": "$49.99", "rating": 4.7, "review_count": 523847,
            "category": "Electronics",
        },
        {
            "asin": "B09B8YWXDF",
            "title": "Instant Pot Duo 7-in-1 Electric Pressure Cooker",
            "images": [
                "https://picsum.photos/seed/B09B8YWXDF/800/800",
                "https://picsum.photos/seed/B09B8YWXDF-2/800/800",
            ],
            "price": "$79.99", "rating": 4.8, "review_count": 145000,
            "category": "Kitchen",
        },
        {
            "asin": "B08DFPV5RP",
            "title": "Hydro Flask 32 oz Wide Mouth Water Bottle",
            "images": [
                "https://picsum.photos/seed/B08DFPV5RP/800/800",
                "https://picsum.photos/seed/B08DFPV5RP-2/800/800",
            ],
            "price": "$44.95", "rating": 4.8, "review_count": 89500,
            "category": "Sports",
        },
        {
            "asin": "B07PXGQC1Q",
            "title": "COSRX Advanced Snail 96 Mucin Power Essence",
            "images": [
                "https://picsum.photos/seed/B07PXGQC1Q/800/800",
                "https://picsum.photos/seed/B07PXGQC1Q-2/800/800",
            ],
            "price": "$25.00", "rating": 4.5, "review_count": 67000,
            "category": "Beauty",
        },
        {
            "asin": "B07MQWQJBT",
            "title": "LEGO Classic Medium Creative Brick Box",
            "images": [
                "https://picsum.photos/seed/B07MQWQJBT/800/800",
                "https://picsum.photos/seed/B07MQWQJBT-2/800/800",
            ],
            "price": "$39.99", "rating": 4.8, "review_count": 43000,
            "category": "Toys",
        },
    ]
    for p in products:
        p["affiliate_url"] = build_affiliate_url(p["asin"])
        p["_mock"] = True  # Flag so deduplication is skipped
    return products
