"""
avatar_generator.py - Generate an AI presenter clip (woman avatar + voice) via HeyGen.

Status: SCAFFOLD — wired into the pipeline but not yet exercised against a real
HeyGen account. HEYGEN_API_KEY / HEYGEN_AVATAR_ID are unset by default, so
generate_avatar_clip() short-circuits to None and the pipeline falls back to
the plain product-image slideshow (existing behavior). Once you have a HeyGen
account and avatar_id, set both env vars and do one dry run before trusting
this in the daily pipeline — the request/response shapes below follow HeyGen's
documented v2 API as of writing, but should be reconciled against
https://docs.heygen.com before the first real run.

We already generate the narration voiceover ourselves (scripts/tts_generator.py,
OpenAI "nova" — a female voice), so this module drives the avatar's lip-sync
from that existing audio file rather than asking HeyGen to do its own TTS.

Flow:
  1. Upload the voiceover MP3 to HeyGen as an asset
  2. Kick off video generation: chosen avatar + that audio asset
  3. Poll until the render completes
  4. Download the finished clip

Cost: HeyGen is a paid API (free tier is very limited) — check current pricing
at https://www.heygen.com/pricing before enabling this for daily/unattended use.
"""
import logging
import os
import time
import requests
import config

logger = logging.getLogger(__name__)

API_BASE = "https://api.heygen.com"
UPLOAD_BASE = "https://upload.heygen.com"
POLL_INTERVAL_SECS = 10
POLL_TIMEOUT_SECS = 300


def _upload_audio_asset(audio_path: str) -> str:
    """Upload the voiceover file to HeyGen, return its asset URL."""
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    resp = requests.post(
        f"{UPLOAD_BASE}/v1/asset",
        headers={
            "x-api-key": config.HEYGEN_API_KEY,
            "Content-Type": "audio/mpeg",
        },
        data=audio_bytes,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    asset_url = data.get("url", "")
    if not asset_url:
        raise RuntimeError(f"HeyGen asset upload returned no URL: {resp.text[:300]}")
    logger.info(f"[HeyGen] Audio asset uploaded: {asset_url}")
    return asset_url


def _start_video_generation(audio_asset_url: str) -> str:
    """Kick off avatar+audio video generation, return the HeyGen video_id."""
    resp = requests.post(
        f"{API_BASE}/v2/video/generate",
        headers={
            "x-api-key": config.HEYGEN_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "video_inputs": [
                {
                    "character": {
                        "type": "avatar",
                        "avatar_id": config.HEYGEN_AVATAR_ID,
                        "avatar_style": "normal",
                    },
                    "voice": {
                        "type": "audio",
                        "audio_url": audio_asset_url,
                    },
                }
            ],
            "dimension": {"width": 720, "height": 1280},
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    video_id = data.get("video_id", "")
    if not video_id:
        raise RuntimeError(f"HeyGen video generation did not return a video_id: {resp.text[:300]}")
    logger.info(f"[HeyGen] Render started: video_id={video_id}")
    return video_id


def _wait_for_video(video_id: str) -> str:
    """Poll HeyGen until the render finishes, return the final video URL."""
    deadline = time.time() + POLL_TIMEOUT_SECS
    while time.time() < deadline:
        resp = requests.get(
            f"{API_BASE}/v1/video_status.get",
            headers={"x-api-key": config.HEYGEN_API_KEY},
            params={"video_id": video_id},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        status = data.get("status", "")
        logger.info(f"[HeyGen] Render status: {status}")

        if status == "completed":
            video_url = data.get("video_url", "")
            if not video_url:
                raise RuntimeError("HeyGen reported completed but returned no video_url")
            return video_url
        if status == "failed":
            raise RuntimeError(f"HeyGen render failed: {data.get('error')}")

        time.sleep(POLL_INTERVAL_SECS)

    raise TimeoutError(f"HeyGen render did not finish within {POLL_TIMEOUT_SECS}s")


def generate_avatar_clip(audio_path: str, script: str, out_path: str) -> str | None:
    """
    Generate an AI avatar clip lip-synced to our existing voiceover.
    Returns the local path to the downloaded clip, or None if avatar generation
    is not configured or fails for any reason (non-fatal — caller falls back
    to the plain product-image slideshow).
    """
    if not config.heygen_configured():
        return None

    try:
        audio_asset_url = _upload_audio_asset(audio_path)
        video_id = _start_video_generation(audio_asset_url)
        video_url = _wait_for_video(video_id)

        resp = requests.get(video_url, timeout=120)
        resp.raise_for_status()
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(resp.content)

        logger.info(f"[HeyGen] Avatar clip downloaded: {out_path}")
        return out_path

    except Exception as e:
        logger.warning(f"[HeyGen] Avatar generation failed (non-fatal): {e}")
        return None
