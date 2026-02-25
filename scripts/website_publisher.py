"""
website_publisher.py — ReviewPocketShorts Website Publisher
────────────────────────────────────────────────────────────
Called by main.py after a successful YouTube upload.

Usage (standalone):
    python scripts/website_publisher.py \
        --asin B09B8YWXDF \
        --title "Instant Pot Duo 7-in-1" \
        --price "$79.99" \
        --rating 4.8 \
        --review-count 145000 \
        --category Kitchen \
        --affiliate-url "https://www.amazon.com/dp/B09B8YWXDF?tag=reviewpockets-20" \
        --image-url "https://..." \
        --youtube-url "https://www.youtube.com/shorts/VIDEO_ID" \
        --youtube-id VIDEO_ID \
        --script-summary "Great kitchen gadget..."

Or import and call publish_to_website(product, youtube_result).

Workflow:
  1. Clone/pull ReviewPocketShortsWeb to /tmp/rps-web
  2. Generate products/{asin}.html from products/example.html template
  3. Update products.json (prepend, cap at MAX_PRODUCTS)
  4. Regenerate sitemap.xml
  5. Commit + push
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
WEB_REPO_OWNER = "rusty-ms"
WEB_REPO_NAME  = "ReviewPocketShortsWeb"
WEB_REPO_DIR   = "/tmp/rps-web"
SITE_BASE_URL  = "https://rusty-ms.github.io/ReviewPocketShortsWeb"
MAX_PRODUCTS   = 30
AFFILIATE_TAG  = "reviewpockets-20"
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "")

logger = logging.getLogger(__name__)


# ── Git helpers ───────────────────────────────────────────────────────────────

def run(cmd: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command, log it, return result."""
    logger.debug("$ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def _authenticated_url() -> str:
    """Build an HTTPS remote URL with GitHub token embedded for auth."""
    token = GITHUB_TOKEN
    if token:
        return f"https://{token}@github.com/{WEB_REPO_OWNER}/{WEB_REPO_NAME}.git"
    return f"https://github.com/{WEB_REPO_OWNER}/{WEB_REPO_NAME}.git"


def clone_or_pull(dest: str) -> None:
    """Clone the repo if it doesn't exist, otherwise pull latest."""
    auth_url = _authenticated_url()
    safe_url = f"https://github.com/{WEB_REPO_OWNER}/{WEB_REPO_NAME}.git"  # for logging
    if os.path.isdir(os.path.join(dest, ".git")):
        logger.info(f"Pulling latest from {safe_url} → {dest}")
        # Ensure remote uses authenticated URL
        run(["git", "remote", "set-url", "origin", auth_url], cwd=dest)
        run(["git", "pull", "--rebase"], cwd=dest)
    else:
        logger.info(f"Cloning {safe_url} → {dest}")
        shutil.rmtree(dest, ignore_errors=True)
        run(["git", "clone", auth_url, dest])
        # Set identity for commits
        run(["git", "config", "user.email", "friday@reviewpocketshorts.com"], cwd=dest)
        run(["git", "config", "user.name", "FRIDAY Pipeline"], cwd=dest)


def commit_and_push(repo_dir: str, message: str) -> None:
    """Stage all changes, commit, and push."""
    run(["git", "add", "-A"], cwd=repo_dir)

    # Check if there's anything to commit
    status = run(["git", "status", "--porcelain"], cwd=repo_dir, check=False)
    if not status.stdout.strip():
        logger.info("Nothing to commit — website already up to date.")
        return

    run(["git", "commit", "-m", message], cwd=repo_dir)
    logger.info("Pushing to GitHub...")
    # Use authenticated URL for push
    run(["git", "push", _authenticated_url(), "main"], cwd=repo_dir)
    logger.info("✅ Website pushed successfully.")


# ── Product page generator ────────────────────────────────────────────────────

def load_template(repo_dir: str) -> str:
    """Load products/example.html as the template."""
    template_path = os.path.join(repo_dir, "products", "example.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()


def make_seo_title(title: str) -> str:
    """Create an SEO-optimized page title."""
    return f"{title} Review — Is It Worth It? | Review Pocket Shorts"


def make_meta_description(title: str, price: str, summary: str) -> str:
    """Create a compelling meta description under 160 chars."""
    base = f"{title} ({price}) — {summary}"
    if len(base) > 155:
        base = base[:152] + "..."
    return base


def generate_product_page(product: dict, repo_dir: str) -> str:
    """
    Generate products/{asin}.html from the example.html template.
    Returns the output file path.
    """
    template = load_template(repo_dir)

    asin         = product["asin"]
    title        = product["title"]
    price        = product.get("price", "")
    price_num    = re.sub(r"[^0-9.]", "", price)
    rating       = product.get("rating", "")
    review_count = product.get("review_count", "")
    category     = product.get("category", "Product")
    affiliate    = product.get("affiliate_url", f"https://www.amazon.com/dp/{asin}?tag={AFFILIATE_TAG}")
    image_url    = product.get("image_url", "")
    yt_id        = product.get("youtube_id", "")
    yt_url       = product.get("youtube_url", f"https://www.youtube.com/shorts/{yt_id}")
    summary      = product.get("script_summary", "")
    posted_at    = product.get("posted_at", datetime.now(timezone.utc).isoformat())
    posted_date  = posted_at[:10] if posted_at else str(date.today())
    canonical    = f"{SITE_BASE_URL}/products/{asin}.html"
    page_title   = make_seo_title(title)
    meta_desc    = make_meta_description(title, price, summary)

    replacements = {
        "{{ASIN}}":            asin,
        "{{TITLE}}":           title,
        "{{CATEGORY}}":        category,
        "{{PRICE}}":           price,
        "{{PRICE_NUMERIC}}":   price_num,
        "{{RATING}}":          str(rating),
        "{{REVIEW_COUNT}}":    str(review_count),
        "{{AFFILIATE_URL}}":   affiliate,
        "{{IMAGE_URL}}":       image_url,
        "{{YOUTUBE_ID}}":      yt_id,
        "{{YOUTUBE_URL}}":     yt_url,
        "{{SCRIPT_SUMMARY}}":  summary,
        "{{POSTED_DATE}}":     posted_date,
        "{{CANONICAL_URL}}":   canonical,
        "{{PAGE_TITLE}}":      page_title,
        "{{META_DESCRIPTION}}": meta_desc,
    }

    html = template
    for placeholder, value in replacements.items():
        html = html.replace(placeholder, str(value))

    out_path = os.path.join(repo_dir, "products", f"{asin}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info(f"Generated product page: {out_path}")
    return out_path


# ── products.json updater ─────────────────────────────────────────────────────

def update_products_json(product: dict, repo_dir: str) -> None:
    """
    Prepend the new product to products.json, keep last MAX_PRODUCTS entries.
    Sets the featured ASIN to the new product.
    """
    json_path = os.path.join(repo_dir, "products.json")

    if os.path.isfile(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"products": []}

    # Build the product entry
    asin = product["asin"]
    entry = {
        "asin":          asin,
        "title":         product.get("title", ""),
        "price":         product.get("price", ""),
        "rating":        product.get("rating", ""),
        "review_count":  product.get("review_count", 0),
        "category":      product.get("category", ""),
        "affiliate_url": product.get("affiliate_url", f"https://www.amazon.com/dp/{asin}?tag={AFFILIATE_TAG}"),
        "image_url":     product.get("image_url", ""),
        "youtube_url":   product.get("youtube_url", ""),
        "youtube_id":    product.get("youtube_id", ""),
        "page_url":      f"/ReviewPocketShortsWeb/products/{asin}.html",
        "posted_at":     product.get("posted_at", datetime.now(timezone.utc).isoformat()),
        "script_summary": product.get("script_summary", ""),
    }

    # Remove existing entry for same ASIN if present
    products = [p for p in data.get("products", []) if p.get("asin") != asin]

    # Prepend new entry, cap at MAX_PRODUCTS
    products = [entry] + products
    products = products[:MAX_PRODUCTS]

    data["products"] = products
    data["featured"]  = asin
    data["updated"]   = datetime.now(timezone.utc).isoformat()

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info(f"Updated products.json ({len(products)} products, featured={asin})")


# ── sitemap.xml regenerator ───────────────────────────────────────────────────

def regenerate_sitemap(repo_dir: str) -> None:
    """Rebuild sitemap.xml from products.json."""
    json_path = os.path.join(repo_dir, "products.json")
    if not os.path.isfile(json_path):
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    products = data.get("products", [])
    today    = str(date.today())

    product_entries = []
    for p in products:
        asin    = p.get("asin", "")
        lastmod = (p.get("posted_at") or today)[:10]
        thumb   = p.get("image_url", "")
        title   = p.get("title", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        summary = (p.get("script_summary") or "").replace("&", "&amp;").replace("<", "&lt;")
        yt_id   = p.get("youtube_id", "")

        video_block = ""
        if yt_id:
            video_block = f"""
    <video:video>
      <video:thumbnail_loc>{thumb}</video:thumbnail_loc>
      <video:title>{title} — Review</video:title>
      <video:description>{summary}</video:description>
      <video:player_loc>https://www.youtube.com/embed/{yt_id}</video:player_loc>
    </video:video>"""

        product_entries.append(f"""  <url>
    <loc>{SITE_BASE_URL}/products/{asin}.html</loc>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
    <lastmod>{lastmod}</lastmod>{video_block}
  </url>""")

    sitemap = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:video="http://www.google.com/schemas/sitemap-video/1.1">

  <!-- Homepage -->
  <url>
    <loc>{SITE_BASE_URL}/</loc>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
    <lastmod>{today}</lastmod>
  </url>

  <!-- Product Pages -->
  <!-- BEGIN_PRODUCTS -->
{"".join(chr(10) + e for e in product_entries)}
  <!-- END_PRODUCTS -->

</urlset>
"""

    sitemap_path = os.path.join(repo_dir, "sitemap.xml")
    with open(sitemap_path, "w", encoding="utf-8") as f:
        f.write(sitemap)

    logger.info(f"Regenerated sitemap.xml ({len(products)} product URLs)")


# ── Main publish function ─────────────────────────────────────────────────────

def publish_to_website(product: dict, youtube_result: dict | None = None) -> bool:
    """
    Full publish pipeline:
      1. Clone/pull web repo
      2. Generate product page
      3. Update products.json
      4. Regenerate sitemap
      5. Commit + push

    Args:
        product:        Product dict (same format as main pipeline uses)
        youtube_result: dict with at least {'video_url': '...', 'video_id': '...'}

    Returns:
        True on success, False on error.
    """
    logger.info("=== Website Publisher START ===")

    # Merge youtube info into product if provided
    if youtube_result:
        yt_url = youtube_result.get("video_url", "")
        yt_id  = youtube_result.get("video_id", "")

        # Extract ID from URL if not provided directly
        if not yt_id and yt_url:
            m = re.search(r"shorts/([A-Za-z0-9_-]+)", yt_url)
            if not m:
                m = re.search(r"v=([A-Za-z0-9_-]+)", yt_url)
            yt_id = m.group(1) if m else ""

        product = {**product, "youtube_url": yt_url, "youtube_id": yt_id}

    # Use YouTube thumbnail as image fallback (always available, matches the channel's visual)
    yt_id = product.get("youtube_id", "")
    if not product.get("image_url") and yt_id:
        product = {**product, "image_url": f"https://i.ytimg.com/vi/{yt_id}/hqdefault.jpg"}
        logger.info(f"Using YouTube thumbnail as image: {product['image_url']}")

    if not product.get("youtube_id"):
        logger.warning("No YouTube ID available — skipping website publish.")
        return False

    try:
        # Step 1: Clone or pull
        clone_or_pull(WEB_REPO_DIR)

        # Step 2: Generate product HTML page
        generate_product_page(product, WEB_REPO_DIR)

        # Step 3: Update products.json
        update_products_json(product, WEB_REPO_DIR)

        # Step 4: Regenerate sitemap
        regenerate_sitemap(WEB_REPO_DIR)

        # Step 5: Commit + push
        commit_msg = (
            f"feat: add {product['asin']} — {product.get('title', 'new product')[:60]}\n\n"
            f"YouTube: {product.get('youtube_url', 'N/A')}\n"
            f"Price: {product.get('price', 'N/A')}"
        )
        commit_and_push(WEB_REPO_DIR, commit_msg)

        logger.info(f"=== Website Publisher DONE ===")
        logger.info(f"  Live at: {SITE_BASE_URL}/products/{product['asin']}.html")
        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Git command failed: {e.cmd}")
        logger.error(f"  stdout: {e.stdout}")
        logger.error(f"  stderr: {e.stderr}")
        return False

    except Exception as e:
        logger.error(f"Website publisher error: {e}", exc_info=True)
        return False


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    parser = argparse.ArgumentParser(description="Publish a product review to the ReviewPocketShorts website.")
    parser.add_argument("--asin",          required=True)
    parser.add_argument("--title",         required=True)
    parser.add_argument("--price",         default="")
    parser.add_argument("--rating",        type=float, default=0.0)
    parser.add_argument("--review-count",  type=int, default=0)
    parser.add_argument("--category",      default="Product")
    parser.add_argument("--affiliate-url", default="")
    parser.add_argument("--image-url",     default="")
    parser.add_argument("--youtube-url",   required=True)
    parser.add_argument("--youtube-id",    default="")
    parser.add_argument("--script-summary", default="")
    parser.add_argument("--posted-at",     default="")
    args = parser.parse_args()

    asin = args.asin
    affiliate = args.affiliate_url or f"https://www.amazon.com/dp/{asin}?tag={AFFILIATE_TAG}"

    product = {
        "asin":          asin,
        "title":         args.title,
        "price":         args.price,
        "rating":        args.rating,
        "review_count":  args.review_count,
        "category":      args.category,
        "affiliate_url": affiliate,
        "image_url":     args.image_url,
        "youtube_url":   args.youtube_url,
        "youtube_id":    args.youtube_id,
        "script_summary": args.script_summary,
        "posted_at":     args.posted_at or datetime.now(timezone.utc).isoformat(),
    }

    success = publish_to_website(product)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
