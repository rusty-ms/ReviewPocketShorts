#!/usr/bin/env python3
"""
Amazon Video Bot - generate_video_from_api.py
Version: 2025-08-20a
"""

import os
import random
import requests
import sys
from typing import Tuple

# --- Config defaults ---
CATEGORY_LIST = [
    "books", "electronics", "home kitchen", "toys games", "beauty",
    "office products", "clothing shoes jewelry", "sports outdoors",
    "best sellers", "trending gadgets", "top rated", "new release"
]

# --- Debug utility ---
def mask_key(key: str) -> str:
    """Show only first 4 and last 4 chars of a key."""
    if not key or len(key) < 8:
        return key
    return f"{key[:4]}...{key[-4:]}"

def debug_print(msg: str):
    if os.getenv("DEBUG", "0") == "1":
        print("[DEBUG]", msg)

# --- API helpers ---
def fetch_product_details(asin: str, country: str, rapidapi_key: str, host: str) -> Tuple[str, str, str]:
    """
    Call /product-details v2 endpoint. Return (title, image_url, product_url).
    """
    url = f"https://{host}/product-details"
    headers = {
        "X-RapidAPI-Key": rapidapi_key,
        "X-RapidAPI-Host": host,
    }
    params = {"asin": asin, "country": country}

    debug_print(f"GET {url} params={params}")
    debug_print(f"Headers: {headers}")

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()

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

def search_random_product(queries, country: str, rapidapi_key: str, host: str) -> str:
    """
    Pick a random search query, call search endpoint, return ASIN.
    """
    query = random.choice(queries)
    url = f"https://{host}/product-search"
    headers = {
        "X-RapidAPI-Key": rapidapi_key,
        "X-RapidAPI-Host": host,
    }
    params = {"query": query, "country": country, "page": 1}

    debug_print(f"Search query={query}")
    debug_print(f"GET {url} params={params}")

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()

    products = payload.get("data", {}).get("products", [])
    if not products:
        raise RuntimeError("No products found in search")

    asin = products[0].get("asin")
    if not asin:
        raise RuntimeError("First product had no ASIN")

    debug_print(f"Selected product via search (ASIN={asin})")
    return asin

# --- Main ---
def main():
    rapidapi_key = os.getenv("RAPIDAPI_KEY")
    host = os.getenv("RAPIDAPI_HOST", "real-time-amazon-data.p.rapidapi.com")
    country = os.getenv("REGION", "US")  # renamed but backwards compat

    print("=== Amazon Video Bot ===")
    print("Version: 2025-08-20a")
    print(f"API Key: {mask_key(rapidapi_key)}")
    print(f"API Host: {host}")
    print(f"Country: {country}")
    print(f"Categories: {CATEGORY_LIST}")
    print("========================")

    if not rapidapi_key:
        raise RuntimeError("RAPIDAPI_KEY not set")

    asin = search_random_product(CATEGORY_LIST, country, rapidapi_key, host)
    title, image_url, product_url = fetch_product_details(asin, country, rapidapi_key, host)

    print("=== Product Selected ===")
    print(f"ASIN: {asin}")
    print(f"Title: {title}")
    print(f"Image: {image_url}")
    print(f"URL: {product_url}")
    print("========================")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", e)
        sys.exit(1)
