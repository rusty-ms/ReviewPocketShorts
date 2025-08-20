#!/usr/bin/env python3
"""
Amazon Video Bot
Version: 2025-08-20d
Features:
- Fetch Amazon product via RapidAPI
- Download product images
- Generate TTS narration + subtitles (edge-tts)
- Burned-in captions synced to TTS
- Background music (royalty-free, auto-downloaded if missing)
- Export vertical MP4 ready for YouTube Shorts
- Auto-generate branded YouTube thumbnail (1280x720)
"""

import os
import re
import io
import sys
import random
import asyncio
from typing import Tuple, List, Optional

import requests
import edge_tts
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from moviepy.editor import (
    ImageClip, concatenate_videoclips, AudioFileClip,
    VideoFileClip, TextClip, CompositeVideoClip, CompositeAudioClip
)

# -------------------------------
# Config
# -------------------------------
VERSION = "2025-08-20d"
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = os.getenv("RAPIDAPI_HOST", "real-time-amazon-data.p.rapidapi.com")
COUNTRY = os.getenv("REGION", "US")
VOICE = os.getenv("TTS_VOICE", "en-US-JennyNeural")
BRAND_NAME = os.getenv("BRAND_NAME", "").strip() or None

OUTPUT_DIR = "output"
ASSETS_DIR = "assets"
THUMBNAIL_PATH = os.path.join(OUTPUT_DIR, "thumbnail.jpg")  # 1280x720
RAW_VIDEO_PATH = os.path.join(OUTPUT_DIR, "raw.mp4")
FINAL_VIDEO_PATH = os.path.join(OUTPUT_DIR, "video.mp4")
AUDIO_PATH = os.path.join(OUTPUT_DIR, "voice.mp3")
VTT_PATH = os.path.join(OUTPUT_DIR, "voice.vtt")

# Search terms (can also override via SEARCH_QUERIES env)
DEFAULT_QUERIES = [
    "books", "electronics", "home kitchen", "toys games", "beauty",
    "office products", "clothing shoes jewelry", "sports outdoors",
    "best sellers", "trending gadgets", "top rated", "new release"
]

# Background music (royalty‑free from Pixabay)
BG_MUSIC_URL = "https://cdn.pixabay.com/download/audio/2023/03/01/audio_4c6a7f9b8f.mp3?filename=corporate-technology-123447.mp3"
BG_MUSIC_PATH = os.path.join(ASSETS_DIR, "music.mp3")

# -------------------------------
# Helpers / Debug
# -------------------------------
def mask_key(key: Optional[str]) -> str:
    if not key:
        return "<missing>"
    return key if len(key) < 9 else f"{key[:4]}...{key[-4:]}"

def dprint(*args):
    if os.getenv("DEBUG", "0") == "1":
        print("[DEBUG]", *args)

def req(method: str, url: str, *, headers: dict, params: dict, timeout: int = 30) -> requests.Response:
    dprint(f"{method} {url} params={params}")
    safe_headers = {"X-RapidAPI-Key": "***", "X-RapidAPI-Host": headers.get("X-RapidAPI-Host")}
    dprint(f"Headers: {safe_headers}")
    r = requests.request(method, url, headers=headers, params=params, timeout=timeout)
    try:
        r.raise_for_status()
        return r
    except requests.HTTPError as e:
        print("HTTP ERROR:", e)
        print("URL:", r.url)
        print("STATUS:", r.status_code)
        try:
            print("BODY:", r.text[:500])
        except Exception:
            pass
        raise

def ensure_assets():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(ASSETS_DIR, exist_ok=True)
    if not os.path.exists(BG_MUSIC_PATH):
        print("[INFO] Downloading royalty-free background music...")
        r = requests.get(BG_MUSIC_URL, stream=True, timeout=60)
        r.raise_for_status()
        with open(BG_MUSIC_PATH, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        print(f"[INFO] Saved background track to {BG_MUSIC_PATH}")

# -------------------------------
# RapidAPI calls (v2)
# -------------------------------
def search_random_product(queries: List[str], country: str) -> str:
    """
    Try provider-specific search paths until one works:
      - /search (OpenWebNinja)
      - /v1/products/search (APICalls)
      - /products/search (some mirrors)
    Returns an ASIN.
    """
    search_paths = ["/search", "/v1/products/search", "/products/search"]
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}

    shuffled_queries = queries[:]
    random.shuffle(shuffled_queries)

    last_err = None
    for q in shuffled_queries:
        for path in search_paths:
            url = f"https://{RAPIDAPI_HOST}{path}"
            params = {"query": q, "country": country, "page": 1}
            try:
                r = req("GET", url, headers=headers, params=params)
            except Exception as e:
                last_err = e
                continue

            js = {}
            try:
                js = r.json()
            except Exception:
                pass

            products = (
                (js.get("data") or {}).get("products")
                or js.get("products")
                or js.get("data")  # some providers return list directly
                or []
            )

            if not products:
                dprint(f"No products for query='{q}' via path '{path}'")
                continue

            asin = None
            for item in products:
                asin = item.get("asin") or item.get("ASIN") or item.get("id")
                if asin:
                    break
            if asin:
                dprint(f"Selected product via search (ASIN={asin}) using path '{path}' and query '{q}'")
                return asin

    if last_err:
        raise RuntimeError(f"Search failed: {last_err}")
    raise RuntimeError("Search returned no products across all paths/queries")

def fetch_product_details(asin: str, country: str) -> Tuple[str, str, str, List[str]]:
    """
    /product-details (params: asin, country)
    Returns (title, hero_image_url, product_url, all_photo_urls)
    """
    url = f"https://{RAPIDAPI_HOST}/product-details"
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    params = {"asin": asin, "country": country}

    r = req("GET", url, headers=headers, params=params)
    payload = r.json()
    data = payload.get("data") or {}
    if not data:
        raise RuntimeError(f"No product details for ASIN {asin}")

    title = data.get("product_title") or "Untitled"
    product_url = data.get("product_url") or f"https://www.amazon.com/dp/{asin}"
    photos = data.get("product_photos") or []
    hero = photos[0] if photos else None
    if not hero:
        raise RuntimeError(f"No image in product details for ASIN {asin}")
    return title, hero, product_url, photos

def fetch_top_reviews(asin: str, country: str, max_reviews: int = 3) -> List[str]:
    """
    /product-reviews (params: asin, country, page=1, sort_by=TOP_REVIEWS)
    Returns a list of plaintext review snippets.
    """
    url = f"https://{RAPIDAPI_HOST}/product-reviews"
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    params = {"asin": asin, "country": country, "page": 1, "sort_by": "TOP_REVIEWS"}

    r = req("GET", url, headers=headers, params=params)
    js = r.json()
    items = (js.get("data") or {}).get("reviews", []) or js.get("reviews", []) or []
    out = []
    for it in items:
        txt = it.get("review_text") or it.get("body") or ""
        txt = " ".join(txt.split())
        if txt:
            out.append(txt[:300] + ("..." if len(txt) > 300 else ""))
        if len(out) >= max_reviews:
            break
    return out

# -------------------------------
# TTS + captions
# -------------------------------
async def synthesize_tts_with_subs(text, voice=VOICE, out_audio=AUDIO_PATH, out_vtt=VTT_PATH):
    """
    Generate voice and WebVTT subtitle file using edge-tts.
    """
    os.makedirs(os.path.dirname(out_audio), exist_ok=True)
    communicate = edge_tts.Communicate(text, voice=voice)
    await communicate.save(out_audio)
    await communicate.save(out_vtt, format="vtt")
    return out_audio, out_vtt

def parse_vtt(vtt_path: str):
    """
    Parse WebVTT captions into [(start, end, text), ...]
    """
    pattern = re.compile(r"(\d+):(\d+):(\d+\.\d+)")
    captions = []
    with open(vtt_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    start, end, buf = None, None, []
    for line in lines:
        if "-->" in line:
            times = line.split("-->")
            s, e = times[0].strip(), times[1].strip().split(" ")[0]
            def parse(ts):
                h, m, sf = pattern.match(ts).groups()
                return int(h)*3600 + int(m)*60 + float(sf)
            start, end = parse(s), parse(e)
        elif line.strip() == "":
            if start is not None and buf:
                captions.append((start, end, " ".join(buf)))
            start, end, buf = None, None, []
        else:
            buf.append(line.strip())
    return captions

def overlay_captions(video, vtt_path):
    captions = parse_vtt(vtt_path)
    subs = []
    for (s, e, txt) in captions:
        sub = (TextClip(txt, fontsize=60, color="white",
                        stroke_color="black", stroke_width=2,
                        size=(1080, None), method="caption")
               .set_start(s).set_end(e).set_position(("center", "bottom")))
        subs.append(sub)
    return CompositeVideoClip([video, *subs], size=(1080, 1920))

# -------------------------------
# Video assembly
# -------------------------------
def download_image_to_file(url: str, dest_path: str):
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    with open(dest_path, "wb") as f:
        f.write(r.content)

def assemble_video_from_images(image_urls: List[str], audio_path: str, out_path=RAW_VIDEO_PATH) -> str:
    """
    Create a vertical 1080x1920 MP4 from up to 5 images and the given audio file.
    (Static slides to keep CI fast/reliable)
    """
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    narration = AudioFileClip(audio_path)
    target_total = max(18.0, min(40.0, narration.duration))
    per_slide = target_total / max(1, min(5, len(image_urls)))

    clips = []
    used = 0
    for idx, url in enumerate(image_urls[:5]):
        try:
            fname = os.path.join(OUTPUT_DIR, f"frame_{idx}.jpg")
            download_image_to_file(url, fname)
            clip = ImageClip(fname).set_duration(per_slide).resize(height=1920)
            # center crop to 1080x1920 if needed
            if clip.w != 1080:
                clip = clip.resize(width=1080) if clip.w < 1080 else clip.crop(x_center=clip.w/2, width=1080)
            clips.append(clip)
            used += 1
        except Exception as e:
            dprint(f"Image skip ({url}): {e}")
            continue

    if not clips:
        narration.close()
        raise RuntimeError("Could not build any image clips")

    video = concatenate_videoclips(clips, method="compose").set_audio(narration)
    video.write_videofile(out_path, fps=30, codec="libx264", audio_codec="aac", verbose=False, logger=None)
    narration.close()
    for c in clips:
        c.close()
    video.close()
    return out_path

# -------------------------------
# Thumbnail generation (1280x720)
# -------------------------------
def load_font(size: int) -> ImageFont.FreeTypeFont:
    # Try common fonts; fall back to PIL default
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]:
        if os.path.exists(path):
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()

def text_wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    words = text.split()
    lines, cur = [], []
    for w in words:
        trial = " ".join(cur + [w])
        if draw.textlength(trial, font=font) <= max_width:
            cur.append(w)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur = [w]
    if cur: lines.append(" ".join(cur))
    return lines[:3]  # keep it concise

def create_thumbnail(hero_url: str, title: str, brand: Optional[str], out_path=THUMBNAIL_PATH) -> str:
    """
    Make a 1280x720 thumbnail with blurred hero background, bold title, and optional brand tag.
    """
    W, H = 1280, 720
    # Load hero image
    r = requests.get(hero_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("RGB")

    # Fill background with blur
    bg = img.copy().resize((W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(18))

    # Foreground (contain)
    fg = img.copy()
    fg.thumbnail((W, H), Image.LANCZOS)
    canvas = bg.copy()
    x = (W - fg.width) // 2
    y = (H - fg.height) // 2
    canvas.paste(fg, (x, y))

    draw = ImageDraw.Draw(canvas)
    # Dark gradient bottom for text legibility
    grad_height = int(H * 0.42)
    for i in range(grad_height):
        alpha = int(255 * (i / grad_height) * 0.8)
        draw.rectangle([(0, H - grad_height + i), (W, H - grad_height + i + 1)], fill=(0, 0, 0, alpha))

    # Title text
    title_font = load_font(64)
    max_text_width = int(W * 0.92)
    lines = text_wrap(draw, title, title_font, max_text_width)
    line_h = int(title_font.size * 1.15)
    total_h = line_h * len(lines)
    y_text = H - grad_height + int((grad_height - total_h) / 2)

    for line in lines:
        w = draw.textlength(line, font=title_font)
        draw.text(((W - w) / 2, y_text), line, fill="white", font=title_font, stroke_width=3, stroke_fill="black")
        y_text += line_h

    # Brand badge (optional)
    if brand:
        badge_font = load_font(36)
        pad = 18
        text_w = draw.textlength(brand, font=badge_font)
        bx = W - int(text_w) - pad*2 - 20
        by = 20
        bw = int(text_w) + pad*2
        bh = badge_font.size + pad
        draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=16, fill=(255, 255, 255, 220))
        draw.text((bx + pad, by + pad/2), brand, fill="black", font=badge_font)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path, "JPEG", quality=90)
    return out_path

# -------------------------------
# Script builder
# -------------------------------
def build_narration_script(title: str, reviews: List[str], product_url: str, affiliate_tag: Optional[str]) -> str:
    lines = [f"{title}. Quick takeaways from top reviews:"]
    for r in reviews[:3]:
        lines.append(r)
    if affiliate_tag := os.getenv("AMAZON_AFFILIATE_TAG", "").strip():
        sep = "&" if "?" in product_url else "?"
        lines.append(f"Full details and current price: {product_url}{sep}tag={affiliate_tag}")
    else:
        lines.append("Check the link for details and current price.")
    return " ".join(lines)

# -------------------------------
# Main
# -------------------------------
def main():
    # Build queries
    env_qs = os.getenv("SEARCH_QUERIES")
    queries = [q.strip() for q in env_qs.split(",")] if env_qs else DEFAULT_QUERIES

    print("\n=== Amazon Video Bot ===")
    print(f"Version: {VERSION}")
    print(f"API Key: {mask_key(RAPIDAPI_KEY)}")
    print(f"API Host: {RAPIDAPI_HOST}")
    print(f"Country: {COUNTRY}")
    print(f"Queries: {queries}")
    print(f"Brand: {BRAND_NAME or '(none)'}")
    print("========================")

    if not RAPIDAPI_KEY:
        sys.exit("Missing RAPIDAPI_KEY env var")

    ensure_assets()

    # 1) Pick a product
    asin = search_random_product(queries, COUNTRY)

    # 2) Details + photos
    title, hero, product_url, photos = fetch_product_details(asin, COUNTRY)
    print("=== Product Selected ===")
    print(f"ASIN: {asin}")
    print(f"Title: {title}")
    print(f"Hero: {hero}")
    print(f"URL:  {product_url}")
    print("========================")

    # 3) Reviews
    try:
        reviews = fetch_top_reviews(asin, COUNTRY, max_reviews=3)
    except Exception as e:
        dprint(f"Reviews fetch failed: {e}")
        reviews = []
    if not reviews:
        reviews = ["Review data not available right now."]

    # 4) Script + TTS (+ subtitles)
    script = build_narration_script(title, reviews, product_url, os.getenv("AMAZON_AFFILIATE_TAG", "").strip() or None)
    print("=== Narration ===")
    print(script)
    print("=================")
    asyncio.run(synthesize_tts_with_subs(script, voice=VOICE, out_audio=AUDIO_PATH, out_vtt=VTT_PATH))

    # 5) Assemble video (use hero + next photos)
    image_urls = [u for u in ([hero] + photos[1:]) if u]
    raw_video = assemble_video_from_images(image_urls, AUDIO_PATH, out_path=RAW_VIDEO_PATH)

    # 6) Overlay captions and add background music
    base = VideoFileClip(raw_video)
    final = overlay_captions(base, VTT_PATH)

    if os.path.exists(BG_MUSIC_PATH):
        music = AudioFileClip(BG_MUSIC_PATH).volumex(0.15)
        comp_audio = CompositeAudioClip([base.audio, music.set_duration(base.duration)])
        final = final.set_audio(comp_audio)

    final.write_videofile(FINAL_VIDEO_PATH, fps=30, codec="libx264", audio_codec="aac")
    base.close()
    final.close()

    # 7) Create YouTube thumbnail from hero
    thumb = create_thumbnail(hero, title, BRAND_NAME, out_path=THUMBNAIL_PATH)
    print(f"Thumbnail written to: {thumb}")
    print(f"Video written to:     {FINAL_VIDEO_PATH}")

if __name__ == "__main__":
    # Light dependency bootstrap if numpy missing (moviepy sometimes needs it)
    try:
        import numpy  # noqa
    except ImportError:
        os.system(f"{sys.executable} -m pip install --quiet numpy")
    main()
