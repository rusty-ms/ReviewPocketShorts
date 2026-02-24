"""
main.py - ReviewPocketShorts Pipeline Orchestrator
Runs the full daily pipeline:
  1. Pick a fresh trending Amazon product
  2. Scrape top customer reviews
  3. Generate AI video script (GPT-4o-mini)
  4. Generate TTS voiceover (edge-tts, free)
  5. Download product images
  6. Assemble vertical Short video (FFmpeg)
  7. Upload to YouTube Shorts
  8. Post to Instagram Reels
  9. Mark product as used

Run manually:  python main.py
Run via n8n:   Triggered by n8n webhook or cron node (see n8n/workflow.json)
"""
import json
import logging
import os
import shutil
import sys
import traceback
from datetime import datetime
import config
from scripts.amazon_products import pick_fresh_product
from scripts.review_scraper import scrape_reviews, format_reviews_for_prompt
from scripts.ai_summarize import generate_script
from scripts.tts_generator import generate_voiceover
from scripts.video_assembler import download_images, assemble_video
from scripts.youtube_uploader import upload_short
from scripts.instagram_poster import post_reel
from scripts.product_tracker import mark_used

# ── Logging Setup ──────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/{datetime.now().strftime('%Y-%m-%d')}.log"),
    ],
)
logger = logging.getLogger("main")


def cleanup_temp():
    """Remove temp files from this run."""
    if os.path.exists(config.TEMP_DIR):
        shutil.rmtree(config.TEMP_DIR)
        os.makedirs(config.TEMP_DIR, exist_ok=True)


def run_pipeline(dry_run: bool = False) -> dict:
    """
    Execute the full ReviewPocketShorts pipeline.
    Set dry_run=True to skip actual uploads (for testing).
    Returns result dict with all produced URLs.
    """
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger.info(f"=== ReviewPocketShorts Pipeline START (run_id={run_id}, dry_run={dry_run}) ===")

    result = {
        "run_id": run_id,
        "success": False,
        "product": None,
        "youtube_url": None,
        "instagram_permalink": None,
        "error": None,
    }

    try:
        # ── Validate config ────────────────────────────────────────
        config.validate_config()

        # ── Step 1: Pick a fresh product ───────────────────────────
        logger.info("Step 1/8: Picking fresh Amazon product...")
        product = pick_fresh_product()
        if not product:
            raise RuntimeError("No fresh products available — all categories exhausted")

        result["product"] = {
            "asin": product["asin"],
            "title": product["title"],
            "price": product["price"],
            "rating": product["rating"],
            "affiliate_url": product["affiliate_url"],
        }
        logger.info(f"  ✓ Product: {product['title']} ({product['price']})")

        # ── Step 2: Scrape reviews ─────────────────────────────────
        logger.info("Step 2/8: Scraping customer reviews...")
        reviews = scrape_reviews(product["asin"], max_reviews=5)
        reviews_text = format_reviews_for_prompt(reviews)
        logger.info(f"  ✓ {len(reviews)} reviews collected")

        # ── Step 3: Generate AI script ─────────────────────────────
        logger.info("Step 3/8: Generating AI video script...")
        script_data = generate_script(product, reviews, reviews_text)
        logger.info(f"  ✓ Script ({len(script_data['script'].split())} words): {script_data['title']}")

        # ── Step 4: Generate TTS voiceover ─────────────────────────
        logger.info("Step 4/8: Generating TTS voiceover (free edge-tts)...")
        audio_path = os.path.join(config.TEMP_DIR, f"voiceover_{run_id}.mp3")
        generate_voiceover(script_data["script"], audio_path)
        logger.info(f"  ✓ Audio: {audio_path}")

        # ── Step 5: Download product images ───────────────────────
        logger.info("Step 5/8: Downloading product images...")
        image_dir = os.path.join(config.TEMP_DIR, "images")
        image_paths = download_images(product.get("images", []), image_dir)
        if not image_paths:
            raise RuntimeError("No product images could be downloaded")
        logger.info(f"  ✓ {len(image_paths)} images downloaded")

        # ── Step 6: Assemble video ─────────────────────────────────
        logger.info("Step 6/8: Assembling video with FFmpeg...")
        video_filename = f"short_{run_id}_{product['asin']}.mp4"
        video_path = os.path.join(config.VIDEO_OUTPUT_DIR, video_filename)
        os.makedirs(config.VIDEO_OUTPUT_DIR, exist_ok=True)

        background_music = os.path.join("assets", "background_music.mp3")
        assemble_video(
            image_paths=image_paths,
            audio_path=audio_path,
            output_path=video_path,
            product=product,
            script_data=script_data,
            background_music=background_music if os.path.exists(background_music) else None,
        )
        logger.info(f"  ✓ Video: {video_path}")

        if dry_run:
            logger.info("DRY RUN — skipping uploads")
            result["success"] = True
            result["dry_run"] = True
            return result

        # ── Step 7: Upload to YouTube ──────────────────────────────
        logger.info("Step 7/8: Uploading to YouTube Shorts...")
        yt_result = upload_short(
            video_path=video_path,
            title=script_data["title"],
            description=script_data["description"],
            hashtags=script_data["hashtags"],
        )
        result["youtube_url"] = yt_result["video_url"]
        logger.info(f"  ✓ YouTube: {yt_result['video_url']}")

        # ── Step 8: Post to Instagram ──────────────────────────────
        if config.instagram_configured():
            logger.info("Step 8/8: Posting to Instagram Reels...")
            caption_parts = [
                script_data["description"],
                " ".join(script_data["hashtags"]),
            ]
            ig_result = post_reel(
                video_path=video_path,
                caption="\n\n".join(filter(None, caption_parts)),
            )
            result["instagram_permalink"] = ig_result.get("permalink", "")
            logger.info(f"  ✓ Instagram: {ig_result.get('permalink', ig_result.get('media_id'))}")
        else:
            logger.info("Step 8/8: Instagram not configured — skipping")

        # ── Mark product used ──────────────────────────────────────
        mark_used(product["asin"], product["title"], yt_result["video_url"])

        result["success"] = True
        logger.info(f"=== Pipeline COMPLETE (run_id={run_id}) ===")
        logger.info(f"  YouTube:   {result['youtube_url']}")
        logger.info(f"  Instagram: {result['instagram_permalink']}")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Pipeline FAILED: {e}")
        logger.error(traceback.format_exc())

    finally:
        cleanup_temp()

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ReviewPocketShorts Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Skip uploads, test pipeline only")
    args = parser.parse_args()

    result = run_pipeline(dry_run=args.dry_run)

    # Output JSON result (n8n can parse this from stdout)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["success"] else 1)
