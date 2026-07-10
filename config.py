"""
config.py - Centralized config loader
All secrets loaded from .env, never hardcoded.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Amazon PA API
AMAZON_ACCESS_KEY = os.getenv("AMAZON_ACCESS_KEY")
AMAZON_SECRET_KEY = os.getenv("AMAZON_SECRET_KEY")
AMAZON_PARTNER_TAG = os.getenv("AMAZON_PARTNER_TAG", "reviewpockets-20")
AMAZON_HOST = os.getenv("AMAZON_HOST", "webservices.amazon.com")
AMAZON_REGION = os.getenv("AMAZON_REGION", "us-east-1")

# RapidAPI (fallback when PA API is not yet active)
RAPIDAPI_KEY  = os.getenv("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = os.getenv("RAPIDAPI_HOST", "real-time-amazon-data.p.rapidapi.com")

# --- Bitly ---
BITLY_ACCESS_TOKEN = os.getenv("BITLY_ACCESS_TOKEN", "")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# TTS
TTS_VOICE = os.getenv("TTS_VOICE", "en-US-JennyNeural")

# YouTube
YOUTUBE_CLIENT_SECRET_FILE = os.getenv("YOUTUBE_CLIENT_SECRET_FILE", "client_secret.json")
YOUTUBE_TOKEN_FILE = os.getenv("YOUTUBE_TOKEN_FILE", "youtube_token.json")
YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")

# YouTube — headless/CI auth (GitHub Actions has no browser).
# Generate once locally with authorize_youtube.py --print-refresh-token, then
# store the three values below as GitHub Actions secrets.
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN", "")

# Instagram / Meta
META_APP_ID = os.getenv("META_APP_ID")
META_APP_SECRET = os.getenv("META_APP_SECRET")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID")

# AI avatar presenter (HeyGen). Optional — pipeline falls back to a plain
# product-image slideshow when unset. Sign up at https://www.heygen.com,
# create/pick an avatar in their studio, and grab its avatar_id.
HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY", "")
HEYGEN_AVATAR_ID = os.getenv("HEYGEN_AVATAR_ID", "")

# Paths
VIDEO_OUTPUT_DIR = os.getenv("VIDEO_OUTPUT_DIR", "./output")
TEMP_DIR = os.getenv("TEMP_DIR", "./temp")
DATA_DIR = os.getenv("DATA_DIR", "./data")
USED_PRODUCTS_FILE = os.getenv("USED_PRODUCTS_FILE", "./data/used_products.json")

# Amazon categories to rotate
AMAZON_CATEGORIES = [
    c.strip() for c in os.getenv("AMAZON_CATEGORIES", "Electronics,Beauty,Kitchen,Toys,Sports").split(",")
]

# Video
VIDEO_WIDTH = int(os.getenv("VIDEO_WIDTH", 1080))
VIDEO_HEIGHT = int(os.getenv("VIDEO_HEIGHT", 1920))
VIDEO_FPS = int(os.getenv("VIDEO_FPS", 30))
VIDEO_DURATION_TARGET = int(os.getenv("VIDEO_DURATION_TARGET", 45))


def validate_config():
    """Check that required secrets are set before running."""
    required = {
        "AMAZON_ACCESS_KEY": AMAZON_ACCESS_KEY,
        "AMAZON_SECRET_KEY": AMAZON_SECRET_KEY,
        "OPENAI_API_KEY": OPENAI_API_KEY,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")
    return True


def instagram_configured() -> bool:
    """Returns True only if all Instagram/Meta keys are set."""
    return all([
        META_ACCESS_TOKEN and META_ACCESS_TOKEN != "FILL_ME_IN",
        INSTAGRAM_ACCOUNT_ID and INSTAGRAM_ACCOUNT_ID != "FILL_ME_IN",
        META_APP_ID and META_APP_ID != "FILL_ME_IN",
    ])


def youtube_headless_configured() -> bool:
    """Returns True if a refresh-token credential is available (no browser needed)."""
    return all([YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN])


def youtube_configured() -> bool:
    """Returns True if YouTube upload can run — headless creds, or a local token/secret file."""
    return youtube_headless_configured() or os.path.exists(YOUTUBE_TOKEN_FILE) or os.path.exists(YOUTUBE_CLIENT_SECRET_FILE)


def heygen_configured() -> bool:
    """Returns True only if the avatar API key and avatar id are both set."""
    return bool(HEYGEN_API_KEY and HEYGEN_AVATAR_ID)
