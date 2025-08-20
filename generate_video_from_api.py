#!/usr/bin/env python3
"""
Amazon Video Bot - generate_video_from_api.py
Version: 2025-08-20b
"""

import os
import random
import sys
from typing import Tuple, List, Optional

import requests

# -------------------------
# Utilities / Debug helpers
# -------------------------

def mask_key(key: Optional[str]) -> str:
    if not key:
        return "<missing>"
    return key if len(key) < 9 else f"{key[:4]}...{key[-4:]}"

def dprint(*args):
    if os.getenv("DEBUG", "0") == "1":
        print("[DEBUG]", *args)

def req(method: str, url: str, *, headers: dict, params: dict, timeout: int = 30) -> requests.Response:
    """requests wrapper that prints helpful diagnostics on failure."""
    dprint(f"{method} {url} params={params}")
    dprint(f"Headers: {headers}")
    r = requests.request(method, url, headers=headers, params=params, timeout=timeout)
    try:
        r.raise_for_status()
        return r
    except requests.HTTPError as e:
        # Print body to help diagnose provider/host mismatches
        print("HTTP ERROR:", e)
        print("URL:", r.url)
        print("STATUS:", r.status_code)
        try:
            print("BODY:", r.text[:500])
        except Exception:
            pass
        raise

# -------------------------
# API Calls (v2-compatible)
# -------------------------

def fetch_product_details(asin: str, country: str, rapidapi_key: str, host: str) -> Tuple[str, str, str]:
    """
    Uses v2 endpoint: /product-details  (params: asin, country)
    Returns (title, image_url, product_url)
    """
    url = f"https://{host}/product-details"
    headers = {"X-RapidAPI-Key": rapidapi_key, "X-RapidAPI-Host": host}
    params = {"asin": asin, "country": country}

    r = req("GET", url, headers=headers, params=params)
    payload = r.json()
    data = payload.get("data") or {}
    if not data:
        raise RuntimeError(f"No product details for ASIN {asin}")

    title = data.get("product_title") or "Untitled"
    product_url = data.get("product_url") or ""
    photos = data.get("product_photos") or []
    image_url = photos[0] if photos else None
    if not image_url:
        raise RuntimeError(f"No image in product details for ASIN {asin}")
    return title, image_url, product_url


def search_random_product(queries: List[str], country: str, rapidapi_key: str, host: str) -> str:
    """
    Try provider-specific search paths until one works:
      - /search                       (OpenWebNinja)
      - /v1/products/search           (APICalls)
      - /products/search              (some mirrors)
    Returns an ASIN.
    """
    search_paths = ["/search", "/v1/products/search", "/products/search"]
    headers = {"X-RapidAPI-Key": rapidapi_key, "X-RapidAPI-Host": host}

    shuffled_queries = queries[:]
    random.shuffle(shuffled_queries)

    last_err = None
    for q in shuffled_queries:
        for path in search_paths:
            url = f"https://{host}{path}"
            params = {"query": q, "country": country, "page": 1}
            try:
                r = req("GET", url, headers=headers, params=params)
            except Exception as e:
                last_err = e
                continue

            # Try common shapes:
            js = {}
            try:
                js = r.json()
            except Exception:
                pass

            # OpenWebNinja v2 typically: {"data":{"products":[...]}}
            products = (
                (js.get("data") or {}).get("products")
                or js.get("products")  # some providers flatten
                or []
            )

            if not products:
                # Keep trying other paths/queries
                dprint(f"No products for query='{q}' via path '{path}'")
                continue

            asin = None
            for item in products:
                asin = item.get("asin") or item.get("ASIN") or item.get("id")
                if asin:
                    break
            if asin:
                dprint(f"Selected product via search (ASIN={asin}) using path '{path}' and query '{q}'")
                return asin

    # If we get here, nothing worked
    if last_err:
        raise RuntimeError(f"Search failed: {last_err}")
    raise RuntimeError("Search returned no products across all paths/queries")


# -------------------------
# Main
# -------------------------

def main():
    version_str = "2025-08-20b"
    commit_sha = os.getenv("GITHUB_SHA", "")[:7] or "<local>"
    rapidapi_key = os.getenv("RAPIDAPI_KEY")
    host = os.getenv("RAPIDAPI_HOST", "real-time-amazon-data.p.rapidapi.com")
    country = os.getenv("REGION", "US")

    # Build queries from categories + generic fallbacks
    env_cats = os.getenv("CATEGORY_LIST")
    if env_cats:
        # convert "home-kitchen" to "home kitchen" for search terms
        cats = [c.strip().replace("-", " ") for c in env_cats.split(",") if c.strip()]
    else:
        cats = [
            "books", "electronics", "home kitchen", "toys games", "beauty",
            "office products", "clothing shoes jewelry", "sports outdoors"
        ]
    extra_q = [s.strip() for s in os.getenv("SEARCH_QUERIES", "best sellers,trending gadgets,top rated,new release").split(",") if s.strip()]
    queries = cats + extra_q

    print("=== Amazon Video Bot ===")
    print(f"Version: {version_str} (commit {commit_sha})")
    print(f"API Key: {mask_key(rapidapi_key)}")
    print(f"API Host: {host}")
    print(f"Country: {country}")
    print(f"Queries: {queries}")
    print("========================")

    if not rapidapi_key:
        raise RuntimeError("RAPIDAPI_KEY not set")

    # Pick a product via search (robust across providers)
    asin = search_random_product(queries, country, rapidapi_key, host)

    # Fetch details via v2 product-details
    title, image_url, product_url = fetch_product_details(asin, country, rapidapi_key, host)

    print("=== Product Selected ===")
    print(f"ASIN: {asin}")
    print(f"Title: {title}")
    print(f"Image: {image_url}")
    print(f"URL: {product_url}")
    print("========================")
    # (At this point you can proceed to TTS + video assembly.)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", e)
        sys.exit(1)
