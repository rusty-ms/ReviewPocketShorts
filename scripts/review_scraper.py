"""
review_scraper.py - Scrape top Amazon reviews for a product.
Uses BeautifulSoup with polite rate limiting and a realistic User-Agent.
Falls back gracefully if blocked.

Cost: FREE
"""
import time
import random
import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS_POOL = [
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    },
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    },
]


def _get_headers() -> dict:
    return random.choice(HEADERS_POOL)


def _polite_delay():
    """Random delay to avoid rate limiting."""
    time.sleep(random.uniform(2.0, 4.5))


def scrape_reviews(asin: str, max_reviews: int = 5) -> list[dict]:
    """
    Scrape top reviews for an Amazon product ASIN.
    Returns list of dicts with: title, body, rating, author, verified
    Falls back to empty list if blocked (pipeline continues gracefully).
    """
    url = f"https://www.amazon.com/product-reviews/{asin}?sortBy=helpful&reviewerType=all_reviews&pageSize={max_reviews}"
    
    try:
        _polite_delay()
        resp = requests.get(url, headers=_get_headers(), timeout=15)
        
        if resp.status_code == 503 or "robot" in resp.text.lower()[:500]:
            logger.warning(f"Amazon blocked review scrape for ASIN {asin} — skipping reviews")
            return []
        
        soup = BeautifulSoup(resp.text, "html.parser")
        reviews = []
        
        review_divs = soup.find_all("div", {"data-hook": "review"}, limit=max_reviews)
        
        for div in review_divs:
            try:
                title_el = div.find("a", {"data-hook": "review-title"})
                body_el = div.find("span", {"data-hook": "review-body"})
                rating_el = div.find("i", {"data-hook": "review-star-rating"})
                author_el = div.find("span", {"class": "a-profile-name"})
                verified_el = div.find("span", {"data-hook": "avp-badge"})
                
                title = title_el.get_text(strip=True) if title_el else ""
                body = body_el.get_text(strip=True) if body_el else ""
                rating_text = rating_el.get_text(strip=True) if rating_el else "0"
                rating = float(rating_text.split(" ")[0]) if rating_text else 0.0
                author = author_el.get_text(strip=True) if author_el else "Anonymous"
                verified = verified_el is not None
                
                # Skip very short reviews
                if len(body) < 20:
                    continue
                
                reviews.append({
                    "title": title,
                    "body": body[:800],  # Truncate for token efficiency
                    "rating": rating,
                    "author": author,
                    "verified": verified,
                })
            except Exception as e:
                logger.debug(f"Skipped a review due to parse error: {e}")
                continue
        
        logger.info(f"Scraped {len(reviews)} reviews for ASIN {asin}")
        return reviews
        
    except Exception as e:
        logger.warning(f"Review scrape failed for ASIN {asin}: {e}")
        return []


def format_reviews_for_prompt(reviews: list[dict]) -> str:
    """Format reviews into a clean string for the AI prompt."""
    if not reviews:
        return "No individual reviews available — summarize based on product features."
    
    lines = []
    for i, r in enumerate(reviews, 1):
        stars = "⭐" * int(r.get("rating", 0))
        verified = " (Verified Purchase)" if r.get("verified") else ""
        lines.append(f"Review {i} {stars}{verified}:")
        if r.get("title"):
            lines.append(f'  "{r["title"]}"')
        lines.append(f'  {r["body"]}')
        lines.append("")
    
    return "\n".join(lines)
