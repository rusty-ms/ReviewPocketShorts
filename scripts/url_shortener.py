"""
url_shortener.py - Shorten Amazon affiliate URLs via TinyURL.
Free, no API key required.
Falls back to the original URL if shortening fails.
"""
import logging
import requests

logger = logging.getLogger(__name__)

TINYURL_API = "https://tinyurl.com/api-create.php"


def shorten(url: str) -> str:
    """
    Shorten a URL via TinyURL. Returns shortened URL or original on failure.
    Example: https://www.amazon.com/dp/B07PXGQC1Q?tag=reviewpockets-20
          → https://tinyurl.com/abc123
    """
    try:
        resp = requests.get(TINYURL_API, params={"url": url}, timeout=10)
        if resp.status_code == 200 and resp.text.startswith("https://"):
            short = resp.text.strip()
            logger.info(f"Shortened URL: {url} → {short}")
            return short
    except Exception as e:
        logger.warning(f"URL shortening failed: {e} — using original")
    return url
