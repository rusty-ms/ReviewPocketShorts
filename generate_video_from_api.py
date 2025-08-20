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
DEBUG         = os.getenv("DEBUG", "0") == "1"

OUTPUT_DIR = "output"
ASSETS_DIR = "assets"
AUDIO_PATH = os.path.join(OUTPUT_DIR, "voice.mp3")
VTT_PATH   = os.path.join(OUTPUT_DIR, "voice.vtt")
SRT_PATH   = os.path.join(OUTPUT_DIR, "voice.srt")
RAW_PATH   = os.path.join(OUTPUT_DIR, "raw.mp4")
FINAL_PATH = os.path.join(OUTPUT_DIR, "video.mp4")
THUMBNAIL  = os.path.join(OUTPUT_DIR, "thumbnail.jpg")

DEFAULT_QUERIES = [
    "books","electronics","home kitchen","toys games","beauty",
    "office products","clothing shoes jewelry","sports outdoors",
    "best sellers","trending gadgets","top rated","new release"
]

# Royalty‑free background music (Pixabay)
BG_MUSIC_URL  = "https://cdn.pixabay.com/download/audio/2023/03/01/audio_4c6a7f9b8f.mp3?filename=corporate-technology-123447.mp3"
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

# -------------------------------
# TTS + captions
# -------------------------------
async def synthesize_tts_with_subs(text: str, voice: str, out_audio: str, out_vtt: str):
    com = edge_tts.Communicate(text, voice=voice)
    await com.save(out_audio)
    await com.save(out_vtt, format="vtt")
    return out_audio, out_vtt

def vtt_to_srt(vtt_path: str, srt_path: str) -> None:
    """
    Quick VTT -> SRT converter good enough for edge-tts output.
    """
    with open(vtt_path, "r", encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f]

    entries = []
    buf = []
    for ln in lines:
        if ln.strip() == "" and buf:
            entries.append(buf); buf = []
        else:
            buf.append(ln)
    if buf: entries.append(buf)

    idx = 1
    out = []
    ts_re = re.compile(r"(?P<s>\d+:\d+:\d+\.\d+)\s*-->\s*(?P<e>\d+:\d+:\d+\.\d+)")

    def fix_ts(ts: str) -> str:
        # 00:00:01.234 -> 00:00:01,234
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
            out.append("")
            idx += 1

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))

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
def build_script(title: str, reviews: List[str], product_url: str) -> str:
    lines = [f"{title}. Quick takeaways from top reviews:"]
    for r in reviews[:3]:
        lines.append(r)
    tag = os.getenv("AMAZON_AFFILIATE_TAG", "").strip()
    if tag:
        sep = "&" if "?" in product_url else "?"
        lines.append(f"Full details and current price: {product_url}{sep}tag={tag}")
    else:
        lines.append("Check the link for details and current price.")
    return " ".join(lines)

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
        vf_parts.append(f"[{i}:v]scale=1080:-2:flags=lanczos,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}]")
    vf_concat_in = "".join(f"[v{i}]" for i in range(len(image_files)))
    vf = f"{';'.join(vf_parts)};{vf_concat_in}concat=n={len(image_files)}:v=1:a=0[vout]"
    # burn subtitles
    if os.path.exists(srt_path):
        # Use libass subtitles filter; force style for readability
        style = "Fontsize=42,OutlineColour=&H80000000&,BorderStyle=3,Outline=2,Shadow=0,PrimaryColour=&H00FFFFFF&"
        vf += f";[vout]subtitles={SRT_PATH}:force_style='{style}'[vfinal]"
        vmap = "[vfinal]"
    else:
        vmap = "[vout]"

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
        "-map", vmap, "-map", amap,
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

    # 3) reviews -> script
    try:
        reviews = fetch_top_reviews(asin, COUNTERY, max_reviews=3)
    except Exception as e:
        dprint(f"Reviews fetch failed: {e}")
        reviews = []
    if not reviews:
        reviews = ["Review data not available right now."]
    script = build_script(title, reviews, product_url)
    log("=== Narration ==="); log(script); log("=================")

    # 4) TTS + VTT + SRT
    asyncio.run(synthesize_tts_with_subs(script, VOICE, AUDIO_PATH, VTT_PATH))
    vtt_to_srt(VTT_PATH, SRT_PATH)

    # 5) Download up to 5 images
    img_files = []
    for idx, url in enumerate([hero] + photos[1:6], start=1):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            r.raise_for_status()
            fp = os.path.join(OUTPUT_DIR, f"frame_{idx}.jpg")
            with open(fp, "wb") as f:
                f.write(r.content)
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

if __name__ == "__main__":
    # minimal bootstrap (edge‑tts pulls aiohttp; assume ffmpeg preinstalled)
    try:
        main()
    except Exception as e:
        print("ERROR:", e)
        sys.exit(1)
