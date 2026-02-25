#!/usr/bin/env python3
"""
Amazon Video Bot — PA API Edition
Version: 2026-02-25

Uses Amazon Product Advertising API 5.0 for product discovery + images.
Falls back to mock data if PA API keys are not configured.

Pipeline:
  1. Pick a fresh product via PA API (scripts/amazon_products.py)
  2. Generate narration script via OpenAI (scripts/ai_summarize.py)
  3. Synthesize TTS via OpenAI TTS (scripts/tts_generator.py)
  4. Download product images from PA API
  5. Build 9:16 slideshow + text overlays via ffmpeg (scripts/video_assembler.py)
  6. Write metadata artifact for YouTube upload
"""

import logging
import os
import sys
import json
from datetime import datetime, timezone
from pathlib import Path

# ── Bootstrap path so scripts/ imports work ──────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from scripts.amazon_products import pick_fresh_product
from scripts.product_tracker import mark_used
from scripts.tts_generator import generate_voiceover
from scripts.video_assembler import assemble_video, download_images
from scripts.ai_summarize import generate_script

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("video_bot")

# ── Paths ──────────────────────────────────────────────────────────────────────
OUTPUT_DIR   = config.VIDEO_OUTPUT_DIR
TEMP_DIR     = config.TEMP_DIR
AUDIO_PATH   = os.path.join(OUTPUT_DIR, "voice.mp3")
VIDEO_PATH   = os.path.join(OUTPUT_DIR, "video.mp4")
META_PATH    = os.path.join(OUTPUT_DIR, "metadata.txt")
PRODUCT_PATH = os.path.join(OUTPUT_DIR, "product.json")


def write_metadata(product: dict, script_data: dict) -> None:
    """Write copy-paste metadata for YouTube upload."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    title       = script_data.get("title") or product["title"][:70]
    description = script_data.get("description", "")
    hashtags    = " ".join(script_data.get("hashtags", ["#Shorts", "#AmazonFinds", "#ProductReview"]))
    affiliate   = product.get("affiliate_url", "")

    blob = (
        "=== COPY/PASTE FOR YOUTUBE UPLOAD ===\n\n"
        f"TITLE:\n{title}\n\n"
        f"DESCRIPTION:\n{description}\n\n"
        f"HASHTAGS:\n{hashtags}\n\n"
        f"AFFILIATE LINK:\n{affiliate}\n\n"
        f"ASIN: {product.get('asin', '')}\n"
        f"PRICE: {product.get('price', '')}\n"
        f"RATING: {product.get('rating', '')} ({product.get('review_count', '')} reviews)\n"
        f"CATEGORY: {product.get('category', '')}\n"
    )

    with open(META_PATH, "w") as f:
        f.write(blob)

    # Also dump full product JSON for website_publisher
    product_out = {
        **product,
        "script_summary": script_data.get("script", "")[:300],
        "youtube_title":  title,
        "posted_at":      datetime.now(timezone.utc).isoformat(),
    }
    with open(PRODUCT_PATH, "w") as f:
        json.dump(product_out, f, indent=2)

    logger.info(f"Metadata written → {META_PATH}")
    logger.info(f"Product JSON written → {PRODUCT_PATH}")


def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    logger.info("=== Amazon Video Bot (PA API Edition) ===")

    # ── Step 1: Pick a fresh product ─────────────────────────────────────────
    logger.info("Step 1: Fetching product from Amazon PA API...")
    product = pick_fresh_product()
    if not product:
        logger.error("No fresh product found. Clear used_products.json or check PA API config.")
        sys.exit(1)

    logger.info(f"  Product: {product['title']}")
    logger.info(f"  ASIN:    {product['asin']}")
    logger.info(f"  Price:   {product.get('price', 'N/A')}")
    logger.info(f"  Rating:  {product.get('rating', 'N/A')} ({product.get('review_count', 0):,} reviews)")
    logger.info(f"  Images:  {len(product.get('images', []))} available")

    if not product.get("images"):
        logger.error("Product has no images — cannot build video.")
        sys.exit(1)

    # ── Step 2: Generate narration script ────────────────────────────────────
    logger.info("Step 2: Generating narration script via OpenAI...")
    try:
        script_data = generate_script(
            product=product,
            reviews=[],
            reviews_text=(
                f"This {product.get('category', 'product')} has "
                f"{product.get('rating', '4.5')}/5 stars from "
                f"{product.get('review_count', 'thousands of'):,} verified buyers."
                if isinstance(product.get('review_count'), int)
                else f"This {product.get('category', 'product')} has "
                     f"{product.get('rating', '4.5')}/5 stars from thousands of verified buyers."
            ),
        )
    except Exception as e:
        logger.error(f"Script generation failed: {e}")
        sys.exit(1)

    script = script_data["script"]
    logger.info(f"  Script ({len(script.split())} words): {script[:120]}...")

    # ── Step 3: Generate TTS voiceover ───────────────────────────────────────
    logger.info("Step 3: Generating TTS voiceover...")
    try:
        generate_voiceover(script, AUDIO_PATH)
    except Exception as e:
        logger.error(f"TTS generation failed: {e}")
        sys.exit(1)

    # ── Step 4: Download product images ──────────────────────────────────────
    logger.info("Step 4: Downloading product images...")
    image_paths = download_images(product["images"], os.path.join(TEMP_DIR, "images"))

    if not image_paths:
        logger.error("Failed to download any product images.")
        sys.exit(1)

    logger.info(f"  Downloaded {len(image_paths)} image(s)")

    # ── Step 5: Assemble video ────────────────────────────────────────────────
    logger.info("Step 5: Assembling video...")
    try:
        assemble_video(
            image_paths=image_paths,
            audio_path=AUDIO_PATH,
            output_path=VIDEO_PATH,
            product=product,
            script_data=script_data,
        )
    except Exception as e:
        logger.error(f"Video assembly failed: {e}")
        sys.exit(1)

    # ── Step 6: Write metadata ────────────────────────────────────────────────
    logger.info("Step 6: Writing metadata...")
    write_metadata(product, script_data)

    # ── Step 7: Mark product as used ─────────────────────────────────────────
    mark_used(product["asin"])
    logger.info(f"  Marked {product['asin']} as used")

    # ── Done ──────────────────────────────────────────────────────────────────
    size_mb = os.path.getsize(VIDEO_PATH) / 1024 / 1024
    logger.info("=== Done ===")
    logger.info(f"  Video:    {VIDEO_PATH} ({size_mb:.1f} MB)")
    logger.info(f"  Audio:    {AUDIO_PATH}")
    logger.info(f"  Metadata: {META_PATH}")
    logger.info(f"  Product:  {PRODUCT_PATH}")
    logger.info("")
    logger.info(f"  Title:    {script_data.get('title', '')}")
    logger.info(f"  Affiliate: {product.get('affiliate_url', '')}")


if __name__ == "__main__":
    main()
