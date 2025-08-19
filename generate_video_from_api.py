"""
Generate an Amazon product review video using the Real‑Time Amazon Data API.

This script fetches product details directly from the OpenWeb Ninja
Real‑Time Amazon Data API on RapidAPI and assembles a short review
video using the `edge‑tts` library for narration and MoviePy for
video composition.  It downloads the product image, synthesises a
voice‑over describing the product, creates a simple title slide and
stitches everything together into a 1080×1920 MP4 video.  The
resulting video is saved to the ``output/`` directory relative to the
script location.

Required environment variables:

  RAPIDAPI_KEY      – Your RapidAPI key (X‑RapidAPI‑Key).  Obtain this
                      from your RapidAPI developer dashboard.

Optional environment variables:

  RAPIDAPI_HOST     – The RapidAPI host for the ranking and product endpoints.
                      Defaults to ``real-time-amazon-data5.p.rapidapi.com``, which
                      hosts the v1 ranking endpoints such as ``/v1/rankings/movers-shakers``.
  REGION            – Amazon marketplace region code (default: ``US``).
  LANGUAGE          – ISO language code for the data returned by the API
                      (default: ``en``).
  CATEGORY_LIST     – Comma‑separated list of Amazon category slugs to
                      choose from when selecting a trending product.  If not
                      provided, a sensible default list of popular categories
                      (e.g. ``beauty,electronics,home-kitchen,toys-games``)
                      will be used.
  USED_ASINS_FILE   – Path to a JSON file used to persist the ASINs of
                      products that have already been featured.  Defaults
                      to ``used_asins.json`` in the current working directory.
  AMAZON_AFFILIATE_TAG – Your Amazon Associates affiliate tag.  When
                      present, the product link in the voice‑over will
                      include this tag.

Example usage:

    RAPIDAPI_KEY=xxxxx python generate_video_from_api.py

This script will call the ``/v1/rankings/movers-shakers`` endpoint for a
random category to obtain a currently trending product.  It will
avoid repeating ASINs across runs by recording used ASINs in a JSON
file.  After selecting a product, it fetches additional details via
``/product`` and reviews via ``/v1/products/reviews``.  It then
generates a short voice‑over and video as before.  You can open the
resulting ``output/video.mp4`` file locally or upload it wherever you
like.

Note:  Never commit your RapidAPI key directly into your source code
or repository.  Use environment variables or secret managers to
protect sensitive credentials.  This script reads the key at runtime
so you can configure it securely in GitHub Actions or your local
environment.
"""

import asyncio
import json
import os
import random
from typing import List, Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import AudioFileClip, ImageClip, concatenate_videoclips
import edge_tts


def fetch_product_details(
    asin: str, region: str, rapidapi_key: str, host: str
) -> Tuple[str, str, str]:
    """
    Fetch product details from the Real‑Time Amazon Data API.

    Parameters
    ----------
    asin : str
        The Amazon Standard Identification Number for the product.
    region : str
        The Amazon marketplace region (e.g. ``US``, ``GB``, ``DE``).
    rapidapi_key : str
        Your RapidAPI key used for authentication.
    host : str
        The RapidAPI host for the Real‑Time Amazon Data API.

    Returns
    -------
    Tuple[str, str, str]
        A tuple containing the product title, a primary image URL, and
        the canonical product URL on Amazon.  Raises an exception if
        the API request fails or returns unexpected data.
    """
    url = f"https://{host}/product"
    headers = {
        "X-RapidAPI-Key": rapidapi_key,
        "X-RapidAPI-Host": host,
    }
    params = {
        "asin": asin,
        "region": region,
    }
    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    try:
        title: str = data["product_title"]
        photo = data["product_photo"]
        if isinstance(photo, list):
            image_url = photo[0]
        else:
            image_url = photo
        product_url: str = data.get("product_url", f"https://www.amazon.com/dp/{asin}")
        return title, image_url, product_url
    except Exception as exc:
        raise ValueError(
            f"Unexpected response format when fetching product {asin}: {json.dumps(data)[:500]}"
        ) from exc


def fetch_top_reviews(
    asin: str,
    region: str,
    language: str,
    rapidapi_key: str,
    host: str,
    max_reviews: int = 3,
) -> List[str]:
    """
    Fetch top customer reviews for a product.

    This function calls the ``/v1/products/reviews`` endpoint to retrieve
    reviews for the given ASIN.  It returns up to ``max_reviews`` review
    bodies.  If the API fails or returns no reviews, an empty list is
    returned.

    Parameters
    ----------
    asin : str
        The product ASIN to fetch reviews for.
    region : str
        Marketplace region code (e.g. ``US``).
    language : str
        Two‑letter language code for the response (e.g. ``en``).
    rapidapi_key : str
        RapidAPI key for authentication.
    host : str
        RapidAPI host for the Real‑Time Amazon Data API.
    max_reviews : int, optional
        Maximum number of reviews to return.  Defaults to 3.

    Returns
    -------
    list of str
        A list of review body strings.
    """
    url = f"https://{host}/v1/products/reviews"
    headers = {
        "X-RapidAPI-Key": rapidapi_key,
        "X-RapidAPI-Host": host,
    }
    params = {
        "asin": asin,
        "country": region,
        "language": language,
        "page": 1,
    }
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
    except Exception:
        return []
    data = response.json()
    reviews = []
    # The structure of the response may vary; attempt to extract review text.
    try:
        results = data.get("reviews") or data.get("data", {}).get("reviews", [])
        for review in results:
            # Attempt multiple keys for the review body
            text = review.get("review") or review.get("review_body") or review.get("content")
            if text:
                reviews.append(text.strip())
            if len(reviews) >= max_reviews:
                break
        return reviews
    except Exception:
        return []


def fetch_random_trending_product(
    categories: List[str],
    region: str,
    language: str,
    rapidapi_key: str,
    host: str,
    used_asins: set,
) -> Optional[dict]:
    """
    Fetch a random trending product from the movers‑and‑shakers list.

    This function randomly selects a category from ``categories``,
    calls the ``/v1/rankings/movers-shakers`` endpoint, shuffles the
    results and returns the first product whose ASIN is not present in
    ``used_asins``.  If no unused product is found after trying all
    categories, ``None`` is returned.

    Parameters
    ----------
    categories : list of str
        List of category slugs to pick from.
    region : str
        Marketplace region code (e.g. ``US``).
    language : str
        Two‑letter language code (e.g. ``en``).
    rapidapi_key : str
        RapidAPI key for authentication.
    host : str
        RapidAPI host for the ranking endpoints (should end with ``.p.rapidapi.com``).
    used_asins : set
        A set of ASINs that have already been featured.

    Returns
    -------
    dict or None
        A dictionary representing a product entry from the API result,
        or ``None`` if nothing suitable could be found.
    """
    random_categories = categories[:]
    random.shuffle(random_categories)
    for category in random_categories:
        # The ranking endpoints may live under two different path patterns
        # depending on the API provider.  Try both patterns.  The first
        # corresponds to OpenWeb Ninja's ``/api/v1/amazon/rankings/movers-shakers``
        # and the second to APIcalls.io's ``/v1/rankings/movers-shakers``.
        for path in ["/api/v1/amazon/rankings/movers-shakers", "/v1/rankings/movers-shakers"]:
            url = f"https://{host}{path}"
            headers = {
                "X-RapidAPI-Key": rapidapi_key,
                "X-RapidAPI-Host": host,
            }
            params = {
                "category": category,
                "country": region,
                "language": language,
                "page": 1,
            }
            try:
                response = requests.get(url, headers=headers, params=params, timeout=30)
                response.raise_for_status()
            except Exception:
                continue
            data = response.json()
            products = (
                data.get("results")
                or data.get("data", {}).get("results")
                or data.get("data", {}).get("products")
                or []
            )
            if not products:
                continue
            random.shuffle(products)
            for product in products:
                asin = (
                    product.get("asin")
                    or product.get("asin13")
                    or product.get("asin_10")
                    or None
                )
                if asin and asin not in used_asins:
                    return product
        # If both patterns fail, try the next category
    return None


def fetch_search_product(
    queries: List[str],
    region: str,
    language: str,
    rapidapi_key: str,
    host: str,
) -> Optional[dict]:
    """
    Fetch a random product using the search endpoint as a fallback.

    This function attempts to call the search endpoint with each query in
    ``queries`` until a non-empty result list is returned.  It tries
    multiple path patterns to account for differences between API providers.

    Parameters
    ----------
    queries : list of str
        List of search queries.  The function will randomise this list.
    region : str
        Marketplace region code (e.g. ``US``).
    language : str
        Two‑letter language code for the response (e.g. ``en``).  Some
        providers may ignore this parameter.
    rapidapi_key : str
        RapidAPI key for authentication.
    host : str
        RapidAPI host for the Real‑Time Amazon Data API.

    Returns
    -------
    dict or None
        A dictionary representing a product entry from the API result,
        or ``None`` if all queries and paths fail to return any product.
    """
    random_queries = queries[:]
    random.shuffle(random_queries)
    # Two common path patterns: the OpenWeb Ninja provider uses `/search` and
    # the APIcalls provider uses `/v1/products/search`.  We try both.
    search_paths = ["/search", "/v1/products/search"]
    for query in random_queries:
        for path in search_paths:
            url = f"https://{host}{path}"
            headers = {
                "X-RapidAPI-Key": rapidapi_key,
                "X-RapidAPI-Host": host,
            }
            params = {
                # Some providers expect `query`, others expect `keywords` or `q`.
                # We include multiple keys to maximise compatibility.  The API
                # should ignore unknown parameters.
                "query": query,
                "keywords": query,
                "q": query,
                "country": region,
                "language": language,
                "page": 1,
            }
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
            except Exception:
                continue
            data = resp.json()
            # Parse the list of products from various potential keys
            products = (
                data.get("results")
                or data.get("data")
                or data.get("products")
                or data.get("items")
                or []
            )
            if not products:
                continue
            # Each product may be nested under a wrapper; flatten if needed
            if isinstance(products, dict):
                # e.g. {"products": [...]} or similar
                products = (
                    products.get("results")
                    or products.get("products")
                    or products.get("items")
                    or []
                )
            if not isinstance(products, list):
                continue
            random.shuffle(products)
            for product in products:
                # Accept first product with an ASIN
                asin = (
                    product.get("asin")
                    or product.get("asin13")
                    or product.get("asin_10")
                    or None
                )
                if asin:
                    return product
    return None


async def generate_voiceover(text: str, output_path: str, voice: str = "en-US-AriaNeural") -> None:
    """Generate a voiceover using edge‑tts and save it to output_path."""
    communicate = edge_tts.Communicate(text, voice)
    with open(output_path, "wb") as out_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                out_file.write(chunk["data"])


def download_image(url: str, path: str) -> None:
    """
    Download an image from a URL and save it to the given path.

    Many image hosting providers block requests without a recognised
    ``User‑Agent`` header.  To prevent 403 "Forbidden" errors, we
    supply a common browser User‑Agent.  If the download fails,
    the caller should catch the exception and decide how to proceed.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/115.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    with open(path, "wb") as f:
        f.write(resp.content)


def create_text_image(
    text: str,
    size: Tuple[int, int] = (1080, 1920),
    bg_color: Tuple[int, int, int] = (255, 255, 255),
    font_path: Optional[str] = None,
    font_size: int = 64,
) -> str:
    """
    Create a centred text slide using Pillow and return the filename.

    Parameters
    ----------
    text : str
        The message to display on the slide.
    size : tuple, optional
        The (width, height) of the image in pixels.  Defaults to
        1080×1920 (portrait orientation).
    bg_color : tuple, optional
        The background colour as an RGB tuple.  Defaults to white.
    font_path : str or None, optional
        Path to a TrueType font file.  If not provided, attempts to
        load a common system font; falls back to Pillow's default.
    font_size : int, optional
        Font size in points.  Defaults to 64.

    Returns
    -------
    str
        The path to the temporary image file saved in the current
        working directory.
    """
    img = Image.new("RGB", size, bg_color)
    draw = ImageDraw.Draw(img)
    # Load a TrueType font if available
    if font_path and os.path.exists(font_path):
        font = ImageFont.truetype(font_path, font_size)
    else:
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()
    # Wrap text within the slide width minus padding
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        candidate = (current_line + " " + word).strip()
        if draw.textsize(candidate, font=font)[0] < size[0] - 80:
            current_line = candidate
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    total_height = sum(draw.textsize(line, font=font)[1] for line in lines) + (len(lines) - 1) * 20
    y = (size[1] - total_height) // 2
    for line in lines:
        w, h = draw.textsize(line, font=font)
        x = (size[0] - w) // 2
        draw.text((x, y), line, font=font, fill=(0, 0, 0))
        y += h + 20
    out_name = "title_slide.jpg"
    img.save(out_name)
    return out_name


def create_video(
    product_title: str,
    image_path: str,
    audio_path: str,
    tagline: str,
    output_dir: str,
) -> str:
    """Assemble the title slide, product image and voice‑over into a video.

    Parameters
    ----------
    product_title : str
        The product title used for narration and display.
    image_path : str
        Path to the downloaded product image.
    audio_path : str
        Path to the recorded voice‑over audio file.
    tagline : str
        The headline text for the title slide.
    output_dir : str
        Directory where the final video will be saved.

    Returns
    -------
    str
        Path to the generated MP4 video file.
    """
    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    title_image_filename = create_text_image(tagline)
    title_image_path = os.path.join(output_dir, "title.jpg")
    os.replace(title_image_filename, title_image_path)
    # Load audio and compute its duration
    audio_clip = AudioFileClip(audio_path)
    duration = audio_clip.duration
    # Create a 3 second title clip and a main image clip of matching duration
    title_clip = ImageClip(title_image_path).set_duration(3).set_fps(30)
    main_clip = (
        ImageClip(image_path)
        .set_duration(duration)
        .set_fps(30)
        .set_audio(audio_clip)
    )
    final_clip = concatenate_videoclips([title_clip, main_clip])
    video_path = os.path.join(output_dir, "video.mp4")
    final_clip.write_videofile(video_path, codec="libx264", audio_codec="aac", fps=30)
    return video_path


def main() -> None:
    """
    Entry point for the script.

    This revised version relies primarily on product search rather than
    ranking endpoints to select a product.  Ranking endpoints (like
    Movers & Shakers) are not consistently available across all
    providers and plans, so falling back to search ensures that a
    product is always found.  The script constructs a list of search
    queries from the provided categories and optional additional
    queries, executes the search API, selects a random result, fetches
    details and reviews, then composes a short video.
    """
    # Read configuration from environment variables
    rapidapi_key = os.getenv("RAPIDAPI_KEY")
    if not rapidapi_key:
        raise RuntimeError(
            "RAPIDAPI_KEY is not set. Please export your RapidAPI key as an environment variable."
        )
    host = os.getenv("RAPIDAPI_HOST", "real-time-amazon-data.p.rapidapi.com")
    region = os.getenv("REGION", "US")
    language = os.getenv("LANGUAGE", "en")

    # Determine category list from environment or default.  These slugs
    # correspond to Amazon departments.  They will be converted to
    # human-friendly search phrases (hyphens replaced with spaces).
    raw_categories = os.getenv("CATEGORY_LIST", "").strip()
    if raw_categories:
        categories = [c.strip() for c in raw_categories.split(",") if c.strip()]
    else:
        categories = [
            "beauty",
            "electronics",
            "home-kitchen",
            "toys-games",
            "office-products",
            "pet-supplies",
            "fashion",
            "sports-outdoors",
            "books",
            "automotive",
            "grocery-gourmet-food",
        ]

    # Convert category slugs into search-friendly phrases (replace hyphens with spaces)
    category_queries = [c.replace("-", " ") for c in categories]

    # Parse additional search queries from environment
    raw_queries = os.getenv("SEARCH_QUERIES", "").strip()
    additional_queries: List[str] = []
    if raw_queries:
        additional_queries = [q.strip() for q in raw_queries.split(",") if q.strip()]
    else:
        # Default fallback search terms that are likely to yield interesting products
        additional_queries = [
            "best sellers",
            "top rated",
            "trending gadgets",
            "popular products",
            "Amazon deals",
            "tech accessories",
            "kitchen essentials",
        ]

    # Combine category-derived queries with additional queries
    search_queries = category_queries + additional_queries

    # Determine if verbose debugging is enabled
    debug_mode = os.getenv("DEBUG") or os.getenv("VERBOSE")
    if debug_mode:
        print(f"[DEBUG] Using RapidAPI host: {host}")
        print(f"[DEBUG] Search queries: {search_queries}")
        print(f"[DEBUG] Region/language: {region}/{language}")

    # Perform a search to find a product.  Randomise the queries list to
    # diversify results across runs.
    product_entry = fetch_search_product(search_queries, region, language, rapidapi_key, host)
    if product_entry is None:
        raise RuntimeError(
            "Could not find a product using the search API. Try expanding the CATEGORY_LIST or SEARCH_QUERIES, "
            "or check your RapidAPI subscription and host configuration."
        )

    # Extract ASIN from the returned product entry
    asin = (
        product_entry.get("asin")
        or product_entry.get("asin13")
        or product_entry.get("asin_10")
    )
    if not asin:
        raise RuntimeError("Selected product does not have a valid ASIN.")

    if debug_mode:
        print(f"[DEBUG] Selected product via search (ASIN={asin})")

    # Load or initialise the used ASINs set to avoid repeats across runs
    used_file = os.getenv("USED_ASINS_FILE", "used_asins.json")
    used_asins: set = set()
    if os.path.exists(used_file):
        try:
            with open(used_file, "r") as f:
                used_asins = set(json.load(f))
        except Exception:
            used_asins = set()

    # Add the ASIN to the used set and persist
    used_asins.add(asin)
    try:
        with open(used_file, "w") as f:
            json.dump(sorted(list(used_asins)), f)
    except Exception:
        pass

    # Fetch additional product details
    title, image_url, product_url = fetch_product_details(asin, region, rapidapi_key, host)

    # Fetch top reviews
    reviews = fetch_top_reviews(asin, region, language, rapidapi_key, host, max_reviews=3)

    # Build voiceover text and description
    affiliate_tag = os.getenv("AMAZON_AFFILIATE_TAG", "").strip()
    affiliate_link = (
        f"https://www.amazon.com/dp/{asin}?tag={affiliate_tag}"
        if affiliate_tag else product_url
    )
    tagline = f"🔥 Check out this Amazon find: {title}!"
    description_lines = [f"Check out {title} – it's a great find on Amazon!"]
    if reviews:
        description_lines.append("")
        description_lines.append("Here's what customers are saying:")
        for review in reviews:
            snippet = review.strip().replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:197] + "..."
            description_lines.append(f"• {snippet}")
    if affiliate_link:
        description_lines.append("")
        description_lines.append(f"👉 Find it here: {affiliate_link}")
    description = "\n".join(description_lines)

    # Prepare output directories and file paths
    output_dir = os.path.join(os.getcwd(), "output")
    os.makedirs(output_dir, exist_ok=True)
    image_path = os.path.join(output_dir, "product.jpg")
    audio_path = os.path.join(output_dir, "voice.mp3")

    # Download image and generate voiceover
    download_image(image_url, image_path)
    asyncio.run(generate_voiceover(description, audio_path))

    # Create the video
    create_video(title, image_path, audio_path, tagline, output_dir)
    print(f"Successfully created video for {title} ({asin})")


if __name__ == "__main__":
    main()
