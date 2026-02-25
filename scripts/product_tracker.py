"""
product_tracker.py - Track used products so we never repeat one.
Stored in data/used_products.json (gitignored).
"""
import json
import os
import logging
from datetime import datetime
from config import USED_PRODUCTS_FILE, DATA_DIR

logger = logging.getLogger(__name__)


def _load() -> dict:
    os.makedirs(DATA_DIR, exist_ok=True)
    default = {"used": [], "history": []}
    if not os.path.exists(USED_PRODUCTS_FILE):
        return default
    try:
        with open(USED_PRODUCTS_FILE, "r") as f:
            data = json.load(f)
        # Ensure expected keys exist even if file was corrupted/empty
        data.setdefault("used", [])
        data.setdefault("history", [])
        return data
    except Exception:
        logger.warning(f"Corrupted {USED_PRODUCTS_FILE} — resetting")
        return default


def _save(data: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(USED_PRODUCTS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def is_used(asin: str) -> bool:
    data = _load()
    return asin in data.get("used", [])


def mark_used(asin: str, product_title: str = "", video_url: str = ""):
    data = _load()
    if asin not in data["used"]:
        data["used"].append(asin)
    data["history"].append({
        "asin": asin,
        "title": product_title,
        "posted_at": datetime.utcnow().isoformat(),
        "youtube_url": video_url,
    })
    _save(data)
    logger.info(f"Marked ASIN {asin} as used: {product_title}")


def get_history() -> list:
    return _load().get("history", [])
