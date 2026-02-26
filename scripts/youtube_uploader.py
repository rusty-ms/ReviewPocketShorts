"""
youtube_uploader.py - Upload video to YouTube as a Short.
Uses YouTube Data API v3 with OAuth2 (your account only).
Token is stored locally (gitignored) after first auth.

Cost: FREE (10,000 units/day quota; upload costs 1,600 units → ~6 free uploads/day)
"""
import logging
import os
import pickle
import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import config

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # Required for comments
]


def _get_credentials() -> Credentials:
    """Load or refresh OAuth2 credentials. Opens browser on first run."""
    token_file = config.YOUTUBE_TOKEN_FILE
    creds = None

    if os.path.exists(token_file):
        with open(token_file, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(google.auth.transport.requests.Request())
        else:
            if not os.path.exists(config.YOUTUBE_CLIENT_SECRET_FILE):
                raise FileNotFoundError(
                    f"YouTube client_secret.json not found at {config.YOUTUBE_CLIENT_SECRET_FILE}. "
                    "Download it from Google Cloud Console → APIs → YouTube Data API v3 → Credentials."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                config.YOUTUBE_CLIENT_SECRET_FILE, SCOPES
            )
            # Detect headless environment (no display) — use manual copy/paste flow
            if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
                flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
                auth_url, _ = flow.authorization_url(prompt="consent")
                print("\n⚠️  Headless environment detected.")
                print("=" * 60)
                print("Open this URL in your browser to authorize YouTube:\n")
                print(auth_url)
                print("\n" + "=" * 60)
                code = input("Paste the authorization code here: ").strip()
                flow.fetch_token(code=code)
                creds = flow.credentials
            else:
                creds = flow.run_local_server(port=0)

        with open(token_file, "wb") as f:
            pickle.dump(creds, f)
        logger.info(f"YouTube token saved to {token_file}")

    return creds


def upload_short(
    video_path: str,
    title: str,
    description: str,
    hashtags: list[str],
    thumbnail_path: str = None,
) -> dict:
    """
    Upload a video as a YouTube Short.
    Returns dict with video_id and video_url.
    """
    creds = _get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    # Build description with hashtags
    full_description = description
    if hashtags:
        tag_line = " ".join(hashtags)
        if tag_line not in full_description:
            full_description += f"\n\n{tag_line}"

    # #Shorts must be in title or description for algorithm
    if "#Shorts" not in full_description and "#Shorts" not in title:
        full_description += "\n#Shorts"

    body = {
        "snippet": {
            "title": title[:100],
            "description": full_description[:5000],
            "tags": [t.lstrip("#") for t in hashtags][:500],
            "categoryId": "22",  # People & Blogs
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 5,  # 5MB chunks
    )

    logger.info(f"Uploading to YouTube: {title}")
    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            logger.info(f"YouTube upload progress: {pct}%")

    video_id = response.get("id", "")
    video_url = f"https://www.youtube.com/shorts/{video_id}"
    logger.info(f"YouTube upload complete: {video_url}")

    # Upload custom thumbnail if provided
    if thumbnail_path and os.path.exists(thumbnail_path) and video_id:
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg"),
            ).execute()
            logger.info("Custom thumbnail uploaded")
        except Exception as e:
            logger.warning(f"Thumbnail upload failed (may need channel verification): {e}")

    # Post and pin affiliate link comment
    if video_id:
        _post_pinned_comment(youtube, video_id, description)

    return {"video_id": video_id, "video_url": video_url}


def _post_pinned_comment(youtube, video_id: str, description: str):
    """
    Post the affiliate link as a pinned comment so it's immediately visible on Shorts.
    Extracts the amzn.to / bit.ly link from the description.
    """
    try:
        # Extract the short link from description (line starting with 🛒)
        short_url = None
        for line in description.split("\n"):
            line = line.strip()
            if line.startswith("🛒"):
                short_url = line.replace("🛒", "").strip()
                break

        if not short_url:
            logger.warning("No affiliate link found in description — skipping pinned comment")
            return

        comment_text = f"🛒 Grab it here → {short_url}\n\n👆 Tap the link above to shop on Amazon!"

        # Post the comment
        comment_response = youtube.commentThreads().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {"textOriginal": comment_text}
                    }
                }
            }
        ).execute()

        comment_id = comment_response["snippet"]["topLevelComment"]["id"]
        logger.info(f"Comment posted: {comment_id}")

        # Pin the comment
        youtube.comments().setModerationStatus(
            id=comment_id,
            moderationStatus="published",
        ).execute()

        # Mark as pinned via video update (channel owner can pin their own comment)
        youtube.comments().update(
            part="snippet",
            body={
                "id": comment_id,
                "snippet": {
                    "textOriginal": comment_text,
                    "isPinned": True,
                }
            }
        ).execute()

        logger.info(f"✓ Pinned comment posted on {video_id}: {short_url}")

    except Exception as e:
        logger.warning(f"Pinned comment failed (non-fatal): {e}")
