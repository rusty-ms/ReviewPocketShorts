#!/usr/bin/env python3
"""
Amazon Video Bot — PA API Edition
Version: 2026-07-10

Uses Amazon Product Advertising API 5.0 for product discovery + images.
Falls back to mock data if PA API keys are not configured.

This is the entry point run by .github/workflows/amazon_video_bot.yml.

Pipeline:
  1. Pick a fresh product via PA API (scripts/amazon_products.py)
  2. Scrape top customer reviews (scripts/review_scraper.py)
  3. Generate narration script via OpenAI (scripts/ai_summarize.py)
  4. Synthesize TTS via OpenAI TTS (scripts/tts_generator.py)
  5. Download product images from PA API
  6. Generate an AI avatar clip, if configured (scripts/avatar_generator.py)
  7. Build 9:16 slideshow + text overlays + optional avatar PIP via ffmpeg
  8. Write metadata artifact (always, for manual fallback / debugging)
  9. Upload to YouTube Shorts, if configured
 10. Publish the product page to the website, if configured
 11. Post to Instagram Reels, if configured
 12. Mark product as used

Steps 9-11 are each independently gated on their credentials being present
(same pattern as main.py) and are non-fatal — a missing or failing
integration logs a warning and the run still produces/keeps its artifacts.
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
from scripts.review_scraper import scrape_reviews, format_reviews_for_prompt
from scripts.tts_generator import generate_voiceover
from scripts.video_assembler import assemble_video, download_images
from scripts.ai_summarize import generate_script
from scripts.avatar_generator import generate_avatar_clip

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


def write_metadata(product: dict, script_data: dict, results: dict) -> None:
    """Write copy-paste metadata plus the full product JSON for the website publisher."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    title       = script_data.get("title") or product["title"][:70]
    description = script_data.get("description", "")
    hashtags    = " ".join(script_data.get("hashtags", ["#Shorts", "#AmazonFinds", "#ProductReview"]))
    affiliate   = product.get("affiliate_url", "")

    blob = (
        "=== COPY/PASTE FOR YOUTUBE UPLOAD (if not auto-uploaded) ===\n\n"
        f"TITLE:\n{title}\n\n"
        f"DESCRIPTION:\n{description}\n\n"
        f"HASHTAGS:\n{hashtags}\n\n"
        f"AFFILIATE LINK:\n{affiliate}\n\n"
        f"ASIN: {product.get('asin', '')}\n"
        f"PRICE: {product.get('price', '')}\n"
        f"RATING: {product.get('rating', '')} ({product.get('review_count', '')} reviews)\n"
        f"CATEGORY: {product.get('category', '')}\n\n"
        f"YouTube:   {results.get('youtube_url') or '(not uploaded)'}\n"
        f"Instagram: {results.get('instagram_permalink') or '(not posted)'}\n"
        f"Website:   {results.get('website_url') or '(not published)'}\n"
    )

    with open(META_PATH, "w") as f:
        f.write(blob)

    product_out = {
        **product,
        "script_summary": script_data.get("script", "")[:300],
        "youtube_title":  title,
        "youtube_url":    results.get("youtube_url", ""),
        "youtube_id":     results.get("youtube_id", ""),
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

    results = {"youtube_url": "", "youtube_id": "", "instagram_permalink": "", "website_url": ""}

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

    # ── Step 2: Scrape real customer reviews ─────────────────────────────────
    logger.info("Step 2: Scraping customer reviews...")
    reviews = []
    try:
        reviews = scrape_reviews(product["asin"], max_reviews=5)
    except Exception as e:
        logger.warning(f"Review scrape failed (non-fatal): {e}")
    reviews_text = format_reviews_for_prompt(reviews)
    logger.info(f"  {len(reviews)} review(s) collected")

    # ── Step 3: Generate narration script ────────────────────────────────────
    logger.info("Step 3: Generating narration script via OpenAI...")
    try:
        script_data = generate_script(
            product=product,
            reviews=reviews,
            reviews_text=reviews_text,
        )
    except Exception as e:
        logger.error(f"Script generation failed: {e}")
        sys.exit(1)

    script = script_data["script"]
    logger.info(f"  Script ({len(script.split())} words): {script[:120]}...")

    # ── Step 4: Generate TTS voiceover ───────────────────────────────────────
    logger.info("Step 4: Generating TTS voiceover...")
    try:
        generate_voiceover(script, AUDIO_PATH)
    except Exception as e:
        logger.error(f"TTS generation failed: {e}")
        sys.exit(1)

    # ── Step 5: Download product images ──────────────────────────────────────
    logger.info("Step 5: Downloading product images...")
    image_paths = download_images(product["images"], os.path.join(TEMP_DIR, "images"))

    if not image_paths:
        logger.error("Failed to download any product images.")
        sys.exit(1)

    logger.info(f"  Downloaded {len(image_paths)} image(s)")

    # ── Step 6: Generate AI avatar clip (optional) ───────────────────────────
    logger.info("Step 6: Generating AI avatar clip (if configured)...")
    avatar_clip_path = None
    try:
        avatar_clip_path = generate_avatar_clip(
            audio_path=AUDIO_PATH,
            script=script,
            out_path=os.path.join(TEMP_DIR, "avatar.mp4"),
        )
    except Exception as e:
        logger.warning(f"Avatar generation failed (non-fatal, falling back to slideshow only): {e}")

    if avatar_clip_path:
        logger.info(f"  Avatar clip ready: {avatar_clip_path}")
    else:
        logger.info("  Avatar not configured/available — using product-image slideshow only")

    # ── Step 7: Assemble video ────────────────────────────────────────────────
    logger.info("Step 7: Assembling video...")
    try:
        assemble_video(
            image_paths=image_paths,
            audio_path=AUDIO_PATH,
            output_path=VIDEO_PATH,
            product=product,
            script_data=script_data,
            avatar_clip_path=avatar_clip_path,
        )
    except Exception as e:
        logger.error(f"Video assembly failed: {e}")
        sys.exit(1)

    # ── Step 8: Upload to YouTube (if configured) ────────────────────────────
    logger.info("Step 8: Uploading to YouTube Shorts (if configured)...")
    if config.youtube_configured():
        try:
            from scripts.youtube_uploader import upload_short
            yt_result = upload_short(
                video_path=VIDEO_PATH,
                title=script_data["title"],
                description=script_data["description"],
                hashtags=script_data["hashtags"],
            )
            results["youtube_url"] = yt_result.get("video_url", "")
            results["youtube_id"] = yt_result.get("video_id", "")
            logger.info(f"  ✓ YouTube: {results['youtube_url']}")
        except Exception as e:
            logger.warning(f"YouTube upload failed (non-fatal): {e}")
    else:
        logger.info("  YouTube not configured (set YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN) — skipping")

    # ── Step 9: Publish product page to website (best-effort) ───────────────
    logger.info("Step 9: Publishing product page to website...")
    try:
        from scripts.website_publisher import publish_to_website
        product_for_web = {
            **product,
            "script_summary": script.split(".")[0][:200] + ".",
        }
        youtube_result = (
            {"video_url": results["youtube_url"], "video_id": results["youtube_id"]}
            if results["youtube_id"] else None
        )
        if publish_to_website(product_for_web, youtube_result):
            results["website_url"] = f"https://reviewpocketshorts.com/products/{product['asin']}.html"
            logger.info(f"  ✓ Website: {results['website_url']}")
        else:
            logger.warning("  ⚠ Website publish returned False (check GITHUB_TOKEN / repo access)")
    except Exception as e:
        logger.warning(f"Website publish failed (non-fatal): {e}")

    # ── Step 10: Post to Instagram Reels (if configured) ─────────────────────
    logger.info("Step 10: Posting to Instagram Reels (if configured)...")
    if config.instagram_configured():
        try:
            from scripts.instagram_poster import post_reel
            caption = f"{script_data['description']}\n\n" + " ".join(script_data["hashtags"])
            ig_result = post_reel(video_path=VIDEO_PATH, caption=caption)
            results["instagram_permalink"] = ig_result.get("permalink", "")
            logger.info(f"  ✓ Instagram: {results['instagram_permalink'] or ig_result.get('media_id')}")
        except Exception as e:
            logger.warning(f"Instagram post failed (non-fatal): {e}")
    else:
        logger.info("  Instagram not configured — skipping")

    # ── Step 11: Write metadata ───────────────────────────────────────────────
    logger.info("Step 11: Writing metadata...")
    write_metadata(product, script_data, results)

    # ── Step 12: Mark product as used ────────────────────────────────────────
    if not product.get("_mock"):
        mark_used(product["asin"], product.get("title", ""), results.get("youtube_url", ""))
        logger.info(f"  Marked {product['asin']} as used")
    else:
        logger.info("  Mock product — not marking as used")

    # ── Done ──────────────────────────────────────────────────────────────────
    size_mb = os.path.getsize(VIDEO_PATH) / 1024 / 1024
    logger.info("=== Done ===")
    logger.info(f"  Video:     {VIDEO_PATH} ({size_mb:.1f} MB)")
    logger.info(f"  Audio:     {AUDIO_PATH}")
    logger.info(f"  Metadata:  {META_PATH}")
    logger.info(f"  Product:   {PRODUCT_PATH}")
    logger.info("")
    logger.info(f"  Title:     {script_data.get('title', '')}")
    logger.info(f"  Affiliate: {product.get('affiliate_url', '')}")
    logger.info(f"  YouTube:   {results['youtube_url'] or '(not uploaded)'}")
    logger.info(f"  Instagram: {results['instagram_permalink'] or '(not posted)'}")
    logger.info(f"  Website:   {results['website_url'] or '(not published)'}")


if __name__ == "__main__":
    main()
