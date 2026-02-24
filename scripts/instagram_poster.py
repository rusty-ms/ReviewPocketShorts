"""
instagram_poster.py - Post a video as an Instagram Reel via Meta Graph API.
Requires: Instagram Professional Account + Facebook Page + Meta Developer App.

The video must be publicly accessible via URL during upload.
We handle this by temporarily hosting via a simple upload flow.

Cost: FREE (Meta Graph API)
"""
import logging
import os
import time
import requests
import config

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"


def _api(method: str, path: str, **kwargs) -> dict:
    """Make a Meta Graph API call."""
    url = f"{GRAPH_API_BASE}/{path}"
    params = kwargs.pop("params", {})
    params["access_token"] = config.META_ACCESS_TOKEN

    resp = getattr(requests, method)(url, params=params, timeout=30, **kwargs)
    resp.raise_for_status()
    return resp.json()


def post_reel(
    video_path: str,
    caption: str,
    cover_image_path: str = None,
) -> dict:
    """
    Post a video as an Instagram Reel using the Meta Graph API.
    
    Process:
    1. Initialize an upload session
    2. Upload the video binary
    3. Publish the container
    
    Returns dict with media_id and permalink.
    """
    account_id = config.INSTAGRAM_ACCOUNT_ID

    if not account_id or not config.META_ACCESS_TOKEN:
        raise EnvironmentError("META_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID must be set in .env")

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    file_size = os.path.getsize(video_path)
    logger.info(f"Posting Instagram Reel: {os.path.basename(video_path)} ({file_size / 1024 / 1024:.1f} MB)")

    # Step 1: Initialize upload session (Resumable Upload)
    init_resp = _api(
        "post",
        f"{account_id}/video_reels",
        params={},
        data={
            "upload_phase": "start",
            "access_token": config.META_ACCESS_TOKEN,
        },
    )

    video_id = init_resp.get("video_id")
    upload_url = init_resp.get("upload_url")

    if not video_id or not upload_url:
        raise RuntimeError(f"Failed to initialize Instagram upload: {init_resp}")

    logger.info(f"Instagram upload session created: {video_id}")

    # Step 2: Upload the video binary
    with open(video_path, "rb") as f:
        upload_resp = requests.post(
            upload_url,
            headers={
                "Authorization": f"OAuth {config.META_ACCESS_TOKEN}",
                "offset": "0",
                "file_size": str(file_size),
            },
            data=f,
            timeout=300,
        )
        upload_resp.raise_for_status()

    logger.info("Video binary uploaded to Instagram")

    # Step 3: Create media container
    container_params = {
        "media_type": "REELS",
        "video_id": video_id,
        "caption": caption[:2200],  # Instagram caption limit
        "share_to_feed": "true",
    }

    if cover_image_path and os.path.exists(cover_image_path):
        # Cover image must be a URL; skip if no hosting available
        logger.debug("Cover image local path provided — skipping (needs public URL)")

    container_resp = _api("post", f"{account_id}/media", params=container_params)
    container_id = container_resp.get("id")

    if not container_id:
        raise RuntimeError(f"Failed to create Instagram media container: {container_resp}")

    # Step 4: Wait for video processing (poll status)
    logger.info("Waiting for Instagram video processing...")
    for attempt in range(15):
        time.sleep(10)
        status_resp = _api("get", container_id, params={"fields": "status_code,status"})
        status = status_resp.get("status_code", "")
        logger.debug(f"Instagram processing status (attempt {attempt+1}): {status}")

        if status == "FINISHED":
            break
        elif status == "ERROR":
            raise RuntimeError(f"Instagram video processing error: {status_resp}")

    # Step 5: Publish
    publish_resp = _api(
        "post",
        f"{account_id}/media_publish",
        params={"creation_id": container_id},
    )

    media_id = publish_resp.get("id", "")
    logger.info(f"Instagram Reel published: media_id={media_id}")

    # Get permalink
    permalink = ""
    try:
        details = _api("get", media_id, params={"fields": "permalink"})
        permalink = details.get("permalink", "")
    except Exception:
        pass

    return {"media_id": media_id, "permalink": permalink}
