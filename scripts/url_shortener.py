"""
url_shortener.py - Shorten Amazon affiliate URLs via Bitly.

Requires BITLY_ACCESS_TOKEN in .env.
Falls back to the original URL if shortening fails.
"""
import logging
import requests
import config

logger = logging.getLogger(__name__)

BITLY_API = "https://api-ssl.bitly.com/v4/shorten"


def shorten(url: str) -> str:
    """
    Shorten a URL via Bitly. Returns shortened URL or original on failure.
    Example: https://www.amazon.com/dp/B07PXGQC1Q?tag=reviewpockets-20
          → https://bit.ly/abc123
    """
    token = getattr(config, "BITLY_ACCESS_TOKEN", None)
    if not token:
        logger.warning("BITLY_ACCESS_TOKEN not set — using original URL")
        return url

    try:
        resp = requests.post(
            BITLY_API,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"long_url": url},
            timeout=10,
        )
        if resp.status_code in (200, 201):
            short = resp.json().get("link", "")
            if short:
                logger.info(f"Bitly shortened: {url} → {short}")
                return short
        else:
            logger.warning(f"Bitly API error {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.warning(f"URL shortening failed: {e} — using original")

    return url
