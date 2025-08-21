#!/usr/bin/env python3
"""
Amazon Video Bot (ffmpeg edition)
Version: 2025-08-20e
"""

import os
import re
import io
import sys
import json
import shlex
import random
import asyncio
import subprocess
import math
from typing import Tuple, List, Optional

import requests
import edge_tts
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# -------------------------------
# Config / Env
# -------------------------------
VERSION = "2025-08-20e"
COMMIT = (os.getenv("GITHUB_SHA") or "")[:7] or "<local>"

RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = os.getenv("RAPIDAPI_HOST", "real-time-amazon-data.p.rapidapi.com")
COUNTRY       = os.getenv("REGION", "US")
VOICE         = os.getenv("TTS_VOICE", "en-US-JennyNeural")
BRAND_NAME    = os.getenv("BRAND_NAME", "").strip() or None
DEBUG         = os.getenv("DEBUG", "1") == "1"

OUTPUT_DIR = "output"
ASSETS_DIR = "assets"
AUDIO_PATH = os.path.join(OUTPUT_DIR, "voice.mp3")
VTT_PATH   = os.path.join(OUTPUT_DIR, "voice.vtt")
SRT_PATH   = os.path.join(OUTPUT_DIR, "voice.srt")
RAW_PATH   = os.path.join(OUTPUT_DIR, "raw.mp4")
FINAL_PATH = os.path.join(OUTPUT_DIR, "video.mp4")
THUMBNAIL  = os.path.join(OUTPUT_DIR, "thumbnail.jpg")

CAPTION_FONT_SIZE  = int(os.getenv("CAPTION_FONT_SIZE", 6))   # 36–48 looks good
CAPTION_MARGIN_V   = int(os.getenv("CAPTION_MARGIN_V", 10))   # move text up from bottom
CAPTION_ALIGNMENT  = int(os.getenv("CAPTION_ALIGNMENT", 2))    # 2=bottom‑center, 8=top‑center, 5=center
CAPTION_OUTLINE    = int(os.getenv("CAPTION_OUTLINE", 5))
CAPTION_BOX_ALPHA  = os.getenv("CAPTION_BOX_ALPHA", "00")      # 00–FF (hex, AA in &HAA..&)

# captions on/off (default hidden)
SHOW_CAPTIONS = os.getenv("SHOW_CAPTIONS", "0") == "1"

# logo overlay
LOGO_PATH     = os.getenv("LOGO_PATH", os.path.join(ASSETS_DIR, "logo.png"))
LOGO_POS      = os.getenv("LOGO_POS", "tr")       # tl | tr | bl | br
LOGO_WIDTH    = int(os.getenv("LOGO_WIDTH", 220)) # px width for overlay
LOGO_MARGIN   = int(os.getenv("LOGO_MARGIN", 28)) # px
LOGO_OPACITY  = float(os.getenv("LOGO_OPACITY", "0.85"))  # 0..1

# artifact with copy/paste title/description/link
WRITE_METADATA_TXT = os.getenv("WRITE_METADATA_TXT", "1") == "1"

# image quality / size helpers
PREFER_HIGHRES     = os.getenv("PREFER_HIGHRES", "1") == "1"
AMZ_MAX_SIDE       = int(os.getenv("AMZ_MAX_SIDE", "2560"))  # try to bump Amazon photo URLs up to this

MAX_REVIEWS              = int(os.getenv("MAX_REVIEWS", 2))
REVIEW_SNIPPET_CHARS     = int(os.getenv("REVIEW_SNIPPET_CHARS", 160))
INTRO_PREFIX             = os.getenv("INTRO_PREFIX", "{title}. Quick takeaways from reviews:")
OUTRO_TEXT               = os.getenv("OUTRO_TEXT", "Check the link for details and current price.")
INCLUDE_AFFILIATE_LINE   = os.getenv("INCLUDE_AFFILIATE_LINE", "1") == "0"

DEFAULT_QUERIES = [
    "books","electronics","home kitchen","toys games","beauty",
    "office products","clothing shoes jewelry","sports outdoors",
    "best sellers","trending gadgets","top rated","new release"
]

# Royalty‑free background music (Pixabay)
#BG_MUSIC_URL  = "https://cdn.pixabay.com/download/audio/2023/03/01/audio_4c6a7f9b8f.mp3?filename=corporate-technology-123447.mp3"
BG_MUSIC_PATH = os.path.join(ASSETS_DIR, "music.mp3")

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# -------------------------------
# Small utils
# -------------------------------
def mask_key(k: Optional[str]) -> str:
    if not k: return "<missing>"
    return k if len(k) < 9 else f"{k[:4]}...{k[-4:]}"

def log(*a):
    print(*a)

def dprint(*a):
    if DEBUG: print("[DEBUG]", *a)

def run(cmd: List[str]) -> None:
    dprint("RUN:", " ".join(shlex.quote(c) for c in cmd))
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")

def ensure_dirs():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(ASSETS_DIR, exist_ok=True)

def ensure_bg_music():
    if not os.path.exists(BG_MUSIC_PATH):
        log("[INFO] Downloading royalty‑free background music…")
        r = requests.get(BG_MUSIC_URL, stream=True, timeout=60)
        r.raise_for_status()
        with open(BG_MUSIC_PATH, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        log(f"[INFO] Saved {BG_MUSIC_PATH}")

def upgrade_amazon_image_url(url: str, max_side: int = AMZ_MAX_SIDE) -> str:
    """
    Try to bump Amazon m.media image URLs to a larger size by swapping the size token.
    Examples we handle: ..._SL1500_.jpg, ..._AC_SL1200_.jpg → _SL2560_ (or max_side)
    If no token found, we leave the URL.
    """
    if "m.media-amazon.com/images" not in url:
        return url
    # common patterns: _SL####_, _AC_SL####_
    return re.sub(r"_(?:AC_)?SL\d+_", f"_SL{max_side}_", url)

# -------------------------------
# Amazonm image as PNG
# -------------------------------

def fetch_image_as_png(pic_url: str, out_path: str, headers: dict, prefer_highres: bool = True) -> str:
    """
    Download an image, normalize orientation/colors, and save as PNG.
    Returns the written file path. Raises on hard errors.
    """
    # Try “upgrading” Amazon URL to a higher-res variant first
    if prefer_highres:
        try:
            pic_url = upgrade_amazon_image_url(pic_url)
        except Exception:
            pass

    r = requests.get(pic_url, headers=headers, timeout=30)
    r.raise_for_status()

    im = Image.open(io.BytesIO(r.content))

    # Normalize EXIF rotation (fixes sideways images)
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:
        pass

    # Ensure RGB (some assets may be P/LA/CMYK)
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")
    elif im.mode == "RGBA":
        # remove alpha to keep the pipeline simple unless you want transparent PNGs
        im = im.convert("RGB")

    # Save as PNG (lossless). optimize=True reduces size a bit; you can add compress_level=6..9 if desired
    im.save(out_path, "PNG", optimize=True)
    return out_path

# -------------------------------
# RapidAPI (v2)
# -------------------------------
def req(method: str, url: str, params: dict) -> dict:
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    dprint("GET", url, "params=", params, "headers=", {"X-RapidAPI-Key":"***","X-RapidAPI-Host":RAPIDAPI_HOST})
    r = requests.request(method, url, headers=headers, params=params, timeout=30)
    if r.status_code >= 400:
        print("HTTP ERROR:", r.status_code, r.url, r.text[:500])
        r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {}

def search_random_product(queries: List[str], country: str) -> str:
    search_paths = ["/search", "/v1/products/search", "/products/search"]
    qs = queries[:]
    random.shuffle(qs)

    last_err = None
    for q in qs:
        for p in search_paths:
            url = f"https://{RAPIDAPI_HOST}{p}"
            try:
                js = req("GET", url, {"query": q, "country": country, "page": 1})
            except Exception as e:
                last_err = e
                continue
            products = (js.get("data") or {}).get("products") or js.get("products") or js.get("data") or []
            if not products:
                dprint(f"No products for q='{q}' via '{p}'")
                continue
            for item in products:
                asin = item.get("asin") or item.get("ASIN") or item.get("id")
                if asin:
                    dprint(f"Selected ASIN={asin} via '{p}' q='{q}'")
                    return asin
    if last_err:
        raise RuntimeError(f"Search failed: {last_err}")
    raise RuntimeError("Search returned no products")

def fetch_product_details(asin: str, country: str) -> Tuple[str, str, str, List[str]]:
    url = f"https://{RAPIDAPI_HOST}/product-details"
    js = req("GET", url, {"asin": asin, "country": country})
    data = js.get("data") or {}
    if not data:
        raise RuntimeError(f"No product details for ASIN {asin}")
    title = data.get("product_title") or "Untitled"
    product_url = data.get("product_url") or f"https://www.amazon.com/dp/{asin}"
    photos = data.get("product_photos") or []
    if not photos: raise RuntimeError("No product photos")
    return title, photos[0], product_url, photos

def fetch_top_reviews(asin: str, country: str, max_reviews: int = 3) -> List[str]:
    url = f"https://{RAPIDAPI_HOST}/product-reviews"
    js = req("GET", url, {"asin": asin, "country": country, "page": 1, "sort_by": "TOP_REVIEWS"})
    items = (js.get("data") or {}).get("reviews", []) or js.get("reviews", []) or []
    out = []
    for it in items:
        txt = (it.get("review_text") or it.get("body") or "").strip()
        txt = " ".join(txt.split())
        if txt:
            out.append(txt[:300] + ("..." if len(txt) > 300 else ""))
        if len(out) >= max_reviews: break
    return out

def fetch_feature_snippets_from_details(asin: str, country: str, limit: int = 3) -> List[str]:
    url = f"https://{RAPIDAPI_HOST}/product-details"
    js = req("GET", url, {"asin": asin, "country": country})
    data = js.get("data") or {}

    # Common fields different providers use
    candidates = []
    for key in ["about_product", "product_bullets", "product_highlights", "features"]:
        val = data.get(key)
        if isinstance(val, list):
            candidates.extend([str(x) for x in val if x])
        elif isinstance(val, str):
            candidates.extend([s.strip() for s in val.split("\n") if s.strip()])

    cleaned = []
    for c in candidates:
        c = " ".join(c.split())
        if 8 <= len(c) <= 220:
            cleaned.append(c)
        if len(cleaned) >= limit:
            break
    return cleaned

# -------------------------------
# TTS + captions (fixed for edge-tts)
# -------------------------------

def vtt_to_srt(vtt_path: str, srt_path: str) -> None:
    """
    Minimal WebVTT → SRT converter compatible with edge-tts output.
    """
    with open(vtt_path, "r", encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f]

    # Split into cue blocks by blank lines
    entries, buf = [], []
    for ln in lines:
        if ln.strip() == "" and buf:
            entries.append(buf); buf = []
        else:
            buf.append(ln)
    if buf:
        entries.append(buf)

    idx = 1
    out = []
    ts_re = re.compile(r"(?P<s>\d+:\d+:\d+\.\d+)\s*-->\s*(?P<e>\d+:\d+:\d+\.\d+)")

    def fix_ts(ts: str) -> str:
        # 00:00:01.234 -> 00:00:01,234 (SRT uses comma)
        return ts.replace(".", ",")

    for chunk in entries:
        times = None
        text_lines = []
        for ln in chunk:
            if "-->" in ln:
                m = ts_re.search(ln)
                if m:
                    s = fix_ts(m.group("s"))
                    e = fix_ts(m.group("e"))
                    times = f"{s} --> {e}"
            elif ln and not ln.startswith("WEBVTT"):
                text_lines.append(ln)
        if times and text_lines:
            out.append(str(idx))
            out.append(times)
            out.extend(text_lines)
            out.append("")  # blank line
            idx += 1

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def _ticks_to_vtt_ts(ticks: int) -> str:
    # edge-tts offsets/durations are in 100-ns ticks (10,000,000 per second)
    total_seconds = ticks / 10_000_000.0
    h = int(total_seconds // 3600)
    m = int((total_seconds % 3600) // 60)
    s = total_seconds % 60
    # VTT requires dot as decimal separator for milliseconds
    return f"{h:02d}:{m:02d}:{s:06.3f}"

async def synthesize_tts_with_subs(text: str, voice: str, out_audio: str, out_vtt: str):
    """
    Generate narration MP3 and a WebVTT subtitle file by streaming edge-tts.
    NOTE: edge-tts Communicate.save() does not accept a 'format' arg for VTT.
    """
    os.makedirs(os.path.dirname(out_audio), exist_ok=True)
    communicate = edge_tts.Communicate(text, voice=voice)

    # Collect captions from SentenceBoundary events while writing audio bytes
    cues = []  # list of (start_ticks, end_ticks, text)
    with open(out_audio, "wb") as af:
        async for chunk in communicate.stream():
            typ = chunk.get("type", "")
            if typ == "audio":
                af.write(chunk["data"])
            else:
                # Be tolerant to different casings
                t = typ.lower()
                if t == "sentenceboundary":
                    start = int(chunk.get("offset", 0))
                    dur   = int(chunk.get("duration", 0))
                    end   = start + max(dur, 1)
                    txt   = (chunk.get("text") or "").strip()
                    if txt:
                        cues.append((start, end, txt))

    # Build a simple WebVTT file
    lines = ["WEBVTT", ""]
    for (start, end, txt) in cues:
        s_ts = _ticks_to_vtt_ts(start)
        e_ts = _ticks_to_vtt_ts(end)
        lines.append(f"{s_ts} --> {e_ts}")
        lines.append(txt)
        lines.append("")  # blank line between cues

    with open(out_vtt, "w", encoding="utf-8") as vf:
        vf.write("\n".join(lines))

    return out_audio, out_vtt


# -------------------------------
# Thumbnail (1280x720)
# -------------------------------
def load_font(size: int):
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]:
        if os.path.exists(path):
            from PIL import ImageFont
            return ImageFont.truetype(path, size=size)
    from PIL import ImageFont
    return ImageFont.load_default()

def create_thumbnail(hero_url: str, title: str, brand: Optional[str], out_path: str) -> str:
    W, H = 1280, 720
    r = requests.get(hero_url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("RGB")

    # bg blur
    bg = img.copy().resize((W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(18))
    # fg contain
    fg = img.copy(); fg.thumbnail((W, H), Image.LANCZOS)
    canvas = bg.copy()
    canvas.paste(fg, ((W - fg.width)//2, (H - fg.height)//2))

    draw = ImageDraw.Draw(canvas)
    grad_h = int(H * 0.42)
    for i in range(grad_h):
        alpha = int(200 * (i/grad_h))
        draw.rectangle([(0, H-grad_h+i), (W, H-grad_h+i+1)], fill=(0,0,0,alpha))

    title_font = load_font(64)
    # naive wrap
    words = title.split()
    lines, cur = [], []
    max_w = int(W*0.92)
    for w in words:
        trial = " ".join(cur+[w])
        if draw.textlength(trial, font=title_font) <= max_w:
            cur.append(w)
        else:
            lines.append(" ".join(cur)); cur=[w]
    if cur: lines.append(" ".join(cur))
    lines = lines[:3]
    line_h = int(title_font.size * 1.15)
    y = H - grad_h + int((grad_h - line_h*len(lines))/2)
    for line in lines:
        lw = draw.textlength(line, font=title_font)
        draw.text(((W-lw)/2, y), line, fill="white", font=title_font,
                  stroke_width=3, stroke_fill="black")
        y += line_h

    if brand:
        badge_font = load_font(36)
        pad = 18
        tw = draw.textlength(brand, font=badge_font)
        bx = W - int(tw) - pad*2 - 20; by = 20
        bw = int(tw) + pad*2; bh = badge_font.size + pad
        draw.rounded_rectangle([bx,by,bx+bw,by+bh], radius=16, fill=(255,255,255,230))
        draw.text((bx+pad, by+pad/2), brand, fill="black", font=badge_font)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path, "JPEG", quality=90)
    return out_path

# -------------------------------
# Build narration script
# -------------------------------
def build_script(title: str, reviews: list[str], product_url: str) -> str:
    intro = INTRO_PREFIX.replace("{title}", title)
    lines = [intro]

    for r in reviews[:MAX_REVIEWS]:
        r = " ".join(r.split())
        if len(r) > REVIEW_SNIPPET_CHARS:
            r = r[:REVIEW_SNIPPET_CHARS - 1] + "…"
        lines.append(r)

    tag = os.getenv("AMAZON_AFFILIATE_TAG", "").strip()
    if INCLUDE_AFFILIATE_LINE and tag:
        sep = "&" if "?" in product_url else "?"
        lines.append(f"Full details and current price: {product_url}{sep}tag={tag}")
    else:
        lines.append(OUTRO_TEXT)

    return " ".join(lines)

# -------------------------------
# Metadata
# -------------------------------

def build_affiliate_url(product_url: str) -> str:
    tag = os.getenv("AMAZON_AFFILIATE_TAG", "").strip()
    if not tag:
        return product_url
    sep = "&" if "?" in product_url else "?"
    return f"{product_url}{sep}tag={tag}"

def write_metadata_file(title: str, reviews_or_features: List[str], product_url: str, path: str) -> str:
    aff = build_affiliate_url(product_url)
    # same text your TTS uses but formatted for description
    bullets = "\n".join([f"• {x}" for x in reviews_or_features[:3]]) if reviews_or_features else ""
    description = (
        f"{title}\n\n"
        f"{('Key points' if 'Key features' in INTRO_PREFIX else 'Top reviews')}:\n{bullets}\n\n"
        f"Amazon link (affiliate): {aff}\n"
        f"#Amazon #trending #shorts"
    )
    blob = (
        "=== COPY/PASTE METADATA ===\n"
        f"Title: {title}\n\n"
        f"Description:\n{description}\n\n"
        f"Affiliate Link Only:\n{aff}\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(blob)
    return path

# -------------------------------
# Video assembly via ffmpeg
# -------------------------------
def build_slideshow_ffmpeg(image_files: List[str], narration_mp3: str, srt_path: str,
                           out_path: str, music_path: Optional[str]) -> None:
    """
    Create 1080x1920 slideshow from images, burn SRT captions, mix background music.
    """
    # Get narration duration using ffprobe
    probe = subprocess.run(
        ["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1", narration_mp3],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        dur = float(probe.stdout.strip())
    except Exception:
        dur = 24.0
    dur = max(18.0, min(40.0, dur))
    per = dur / max(1, len(image_files))

    # inputs: one looped image input per slide
    cmd = ["ffmpeg","-y"]
    for img in image_files:
        cmd += ["-loop","1","-t", f"{per:.3f}","-i", img]
    # narration
    cmd += ["-i", narration_mp3]
    # optional music
    if music_path and os.path.exists(music_path):
        cmd += ["-i", music_path]

    # Build filter_complex
    vf_parts = []
    for i in range(len(image_files)):
        # scale and pad to 1080x1920, square pixels
        vf_parts.append(
            f"[{i}:v]"
            f"scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad=1080:1920:(1080-iw)/2:(1920-ih)/2:color=black,"
            f"setsar=1[v{i}]"
        )
    vf_concat_in = "".join(f"[v{i}]" for i in range(len(image_files)))
    vf = f"{';'.join(vf_parts)};{vf_concat_in}concat=n={len(image_files)}:v=1:a=0[vout]"
    # burn subtitles only if enabled AND file exists
    if SHOW_CAPTIONS and os.path.exists(srt_path):
        style = (
            f"Fontname=DejaVu Sans,"
            f"Fontsize={CAPTION_FONT_SIZE},"
            f"BorderStyle=3,Outline={CAPTION_OUTLINE},Shadow=0,"
            f"PrimaryColour=&H00FFFFFF&,"
            f"OutlineColour=&H80000000&,"
            f"BackColour=&H{CAPTION_BOX_ALPHA}000000&,"
            f"Alignment={CAPTION_ALIGNMENT},"
            f"MarginV={CAPTION_MARGIN_V}"
        )
        vf += f";[vout]subtitles={srt_path}:force_style='{style}'[vsub]"
        v_current = "[vsub]"
    else:
        v_current = "[vout]"

    # optional logo overlay
    have_logo = LOGO_PATH and os.path.exists(LOGO_PATH)
    if have_logo:
        # add logo as an input
        cmd += ["-i", LOGO_PATH]
        logo_idx = len(image_files) + (2 if (music_path and os.path.exists(music_path)) else 1)  # after voice (+ music)
        # scale logo to width, keep AR, apply opacity
        pos = LOGO_POS.lower()
        x_expr = f"{LOGO_MARGIN}" if "l" in pos else f"W-w-{LOGO_MARGIN}"
        y_expr = f"{LOGO_MARGIN}" if "t" in pos else f"H-h-{LOGO_MARGIN}"
        vf += (
            f";[{logo_idx}:v]scale={LOGO_WIDTH}:-1,format=rgba,colorchannelmixer=aa={LOGO_OPACITY}[logo]"
            f";{v_current}[logo]overlay={x_expr}:{y_expr}[vlogo]"
        )
        v_current = "[vlogo]"

    # audio mix
    # narration is at index = len(image_files)
    if music_path and os.path.exists(music_path):
        a_narr = f"[{len(image_files)}:a]"
        a_music = f"[{len(image_files)+1}:a]"
        af = f"{a_music}volume=0.15[am];{a_narr}[am]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        amap = "[aout]"
    else:
        af = f"[{len(image_files)}:a]anull[aout]"
        amap = "[aout]"

    cmd += [
        "-filter_complex", vf + ";" + af,
        "-map", v_current, "-map", amap,
        "-shortest",
        "-r","30",
        "-c:v","libx264","-preset","veryfast","-crf","22",
        "-c:a","aac","-b:a","192k",
        "-pix_fmt","yuv420p",
        out_path
    ]
    run(cmd)

# -------------------------------
# Main
# -------------------------------
def main():
    env_qs = os.getenv("SEARCH_QUERIES")
    queries = [q.strip() for q in env_qs.split(",")] if env_qs else DEFAULT_QUERIES

    log("=== Amazon Video Bot ===")
    log(f"Version: {VERSION} (commit {COMMIT})")
    log(f"API Key: {mask_key(RAPIDAPI_KEY)}")
    log(f"API Host: {RAPIDAPI_HOST}")
    log(f"Country: {COUNTRY}")
    log(f"Queries: {queries}")
    log(f"Brand: {BRAND_NAME or '(none)'}")
    log("========================")

    if not RAPIDAPI_KEY:
        sys.exit("Missing RAPIDAPI_KEY")

    ensure_dirs(); ensure_bg_music()

    # 1) pick product
    asin = search_random_product(queries, COUNTERY := COUNTRY)

    # 2) details + photos
    title, hero, product_url, photos = fetch_product_details(asin, COUNTERY)
    log("=== Product Selected ===")
    log(f"ASIN: {asin}")
    log(f"Title: {title}")
    log(f"Hero:  {hero}")
    log(f"URL:   {product_url}")
    log("========================")

    # 3) reviews -> script (with feature fallback so we never speak "no reviews")
    reviews = []
    try:
        reviews = fetch_top_reviews(asin, COUNTERY, max_reviews=5)
    except Exception as e:
        dprint(f"Reviews fetch failed: {e}")
        reviews = []
    
    if not reviews:
        # try to pull bullets/features from product-details
        features = fetch_feature_snippets_from_details(asin, COUNTERY, limit=3)
        if features:
            # soften the intro so it doesn't imply reviews
            global INTRO_PREFIX
            INTRO_PREFIX = "{title}. Key features:"
            reviews = features
        else:
            # last resort: very short script, no filler line
            INTRO_PREFIX = "{title}."
            reviews = []
    
    script = build_script(title, reviews, product_url)
    log("=== Narration ==="); log(script); log("=================")

    # 4) TTS + VTT + SRT
    asyncio.run(synthesize_tts_with_subs(script, VOICE, AUDIO_PATH, VTT_PATH))
    vtt_to_srt(VTT_PATH, SRT_PATH)

    # 5) Download up to 5 images
    img_files = []
    headers = {"User-Agent": UA}
    
    for idx, url in enumerate([hero] + photos[1:6], start=1):
        try:
            fp = os.path.join(OUTPUT_DIR, f"frame_{idx}.png")
            fetch_image_as_png(url, fp, headers, prefer_highres=PREFER_HIGHRES)
            img_files.append(fp)
        except Exception as e:
            dprint(f"Image skip {url}: {e}")
    
    if not img_files:
        raise RuntimeError("No images to build video")
        

        
    # 6) Build slideshow with ffmpeg (burn captions, mix music)
    build_slideshow_ffmpeg(img_files, AUDIO_PATH, SRT_PATH, RAW_PATH, BG_MUSIC_PATH)

    # 7) Finalize (raw already has subs & music; keep as final)
    # (If we wanted a 2-pass encode or extra steps we could add them here)
    os.replace(RAW_PATH, FINAL_PATH)

    # 8) Thumbnail
    thumb = create_thumbnail(hero, title, BRAND_NAME, THUMBNAIL)

    log(f"Thumbnail written to: {thumb}")
    log(f"Video written to:     {FINAL_PATH}")

     # 9) Metadata artifact for easy upload
     if WRITE_METADATA_TXT:
        meta_path = os.path.join(OUTPUT_DIR, "metadata.txt")
        write_metadata_file(title, reviews, product_url, meta_path)
        log(f"Metadata written to: {meta_path}")

if __name__ == "__main__":
    # minimal bootstrap (edge‑tts pulls aiohttp; assume ffmpeg preinstalled)
    try:
        main()
    except Exception as e:
        print("ERROR:", e)
        sys.exit(1)
