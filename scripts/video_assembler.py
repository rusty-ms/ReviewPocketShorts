"""
video_assembler.py - Assemble YouTube Short using FFmpeg.
Creates a 9:16 vertical video (1080x1920) with:
  - Product images (Ken Burns pan/zoom effect)
  - Text overlays (product name, price, star rating)
  - TTS voiceover audio
  - Subtle background music (optional)

Cost: FREE (FFmpeg)
"""
import json
import logging
import math
import os
import subprocess
import tempfile
import urllib.request
from pathlib import Path
import config

logger = logging.getLogger(__name__)

W = config.VIDEO_WIDTH    # 1080
H = config.VIDEO_HEIGHT   # 1920
FPS = config.VIDEO_FPS    # 30


def download_images(image_urls: list[str], temp_dir: str) -> list[str]:
    """Download product images to temp dir. Returns list of local paths."""
    os.makedirs(temp_dir, exist_ok=True)
    paths = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    for i, url in enumerate(image_urls[:6]):  # Max 6 images
        dest = os.path.join(temp_dir, f"product_{i}.jpg")
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r, open(dest, "wb") as f:
                f.write(r.read())
            paths.append(dest)
            logger.debug(f"Downloaded image {i+1}: {dest}")
        except Exception as e:
            logger.warning(f"Failed to download image {i}: {e}")
    logger.info(f"Downloaded {len(paths)} product images")
    return paths


def _get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True, timeout=10
    )
    try:
        return float(result.stdout.strip())
    except Exception:
        return 45.0


def _prepare_image(img_path: str, out_path: str):
    """Resize and pad image to 1080x1920 (9:16) with blurred background."""
    subprocess.run([
        "ffmpeg", "-y", "-i", img_path,
        "-vf", (
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black"
        ),
        "-q:v", "2", out_path
    ], capture_output=True, check=True)


def _build_slideshow_filter(num_images: int, duration_per_image: float, audio_duration: float) -> str:
    """
    Build FFmpeg filtergraph for Ken Burns effect slideshow.
    Each image gets equal time, with smooth fade transitions.
    """
    fade_duration = 0.5  # seconds for crossfade
    total_frames_per_slide = int(duration_per_image * FPS)
    fade_frames = int(fade_duration * FPS)

    parts = []

    # Scale and apply Ken Burns (zoompan) to each image
    for i in range(num_images):
        # Alternate zoom direction for variety
        if i % 2 == 0:
            zoom_expr = f"min(1+{i*0.0002}+on/{total_frames_per_slide}*0.05,1.05)"
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
        else:
            zoom_expr = f"min(1.05-on/{total_frames_per_slide}*0.05,1.05)"
            x_expr = "iw/2-(iw/zoom/2)+5"
            y_expr = "ih/2-(ih/zoom/2)+5"

        parts.append(
            f"[{i}:v]scale={W*2}:{H*2},"
            f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}'"
            f":d={total_frames_per_slide}:s={W}x{H}:fps={FPS},"
            f"trim=duration={duration_per_image},"
            f"setpts=PTS-STARTPTS[v{i}]"
        )

    # Concatenate all image streams
    concat_inputs = "".join(f"[v{i}]" for i in range(num_images))
    parts.append(f"{concat_inputs}concat=n={num_images}:v=1:a=0[vconcat]")

    # Add fade in/out
    parts.append(
        f"[vconcat]fade=t=in:st=0:d=0.5,"
        f"fade=t=out:st={audio_duration - 0.5:.2f}:d=0.5[vfinal]"
    )

    return ";".join(parts)


def _add_text_overlay(input_path: str, output_path: str, product: dict, script_data: dict):
    """Add text overlays: product name, price, star rating banner."""
    title = product.get("title", "")[:50]  # Truncate long titles
    price = product.get("price", "")
    rating = product.get("rating", "")
    stars = "★" * round(float(rating)) if rating and rating != "N/A" else ""

    # Escape special chars for FFmpeg drawtext
    def esc(s):
        return str(s).replace("'", "\\'").replace(":", "\\:").replace(",", "\\,")

    title_esc = esc(title)
    price_esc = esc(price)
    stars_esc = esc(f"{stars} {rating}/5" if stars else "")

    drawtext_filter = (
        # Semi-transparent bottom banner
        f"drawbox=x=0:y={H - 280}:w={W}:h=280:color=black@0.6:t=fill,"
        # Product title (wraps)
        f"drawtext=text='{title_esc}'"
        f":x=30:y={H - 260}"
        f":fontsize=38:fontcolor=white:line_spacing=8"
        f":borderw=2:bordercolor=black,"
        # Price
        f"drawtext=text='{price_esc}'"
        f":x=30:y={H - 120}"
        f":fontsize=48:fontcolor=#FFD700:borderw=2:bordercolor=black,"
        # Stars
        f"drawtext=text='{stars_esc}'"
        f":x=30:y={H - 65}"
        f":fontsize=36:fontcolor=#FFD700:borderw=1:bordercolor=black,"
        # Top badge
        f"drawbox=x=0:y=0:w={W}:h=90:color=black@0.5:t=fill,"
        f"drawtext=text='Review Pocket Shorts 🎬'"
        f":x=(w-text_w)/2:y=25"
        f":fontsize=36:fontcolor=white:borderw=1:bordercolor=black"
    )

    subprocess.run([
        "ffmpeg", "-y", "-i", input_path,
        "-vf", drawtext_filter,
        "-c:a", "copy",
        output_path
    ], capture_output=True, check=True)


def assemble_video(
    image_paths: list[str],
    audio_path: str,
    output_path: str,
    product: dict,
    script_data: dict,
    background_music: str = None,
) -> str:
    """
    Full video assembly pipeline:
    1. Prepare images (resize/pad to 9:16)
    2. Build Ken Burns slideshow
    3. Mix audio (voiceover + optional background music)
    4. Add text overlays
    5. Export final MP4

    Returns path to the final video file.
    """
    os.makedirs(config.TEMP_DIR, exist_ok=True)
    os.makedirs(config.VIDEO_OUTPUT_DIR, exist_ok=True)

    if not image_paths:
        raise ValueError("No images provided for video assembly")

    audio_duration = _get_audio_duration(audio_path)
    if audio_duration <= 0:
        audio_duration = 45.0

    # Cap at 59s for Shorts
    video_duration = min(audio_duration + 1.5, 59.0)
    num_images = len(image_paths)
    duration_per_image = video_duration / num_images

    # Step 1: Prepare (resize) all images
    prepared_images = []
    for i, img in enumerate(image_paths):
        prepared = os.path.join(config.TEMP_DIR, f"prepared_{i}.jpg")
        _prepare_image(img, prepared)
        prepared_images.append(prepared)

    # Step 2: Build slideshow with Ken Burns
    slideshow_path = os.path.join(config.TEMP_DIR, "slideshow.mp4")
    filter_complex = _build_slideshow_filter(num_images, duration_per_image, video_duration)

    input_args = []
    for img in prepared_images:
        input_args += ["-loop", "1", "-t", str(duration_per_image), "-i", img]

    subprocess.run([
        "ffmpeg", "-y",
        *input_args,
        "-filter_complex", filter_complex,
        "-map", "[vfinal]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-r", str(FPS),
        "-t", str(video_duration),
        slideshow_path
    ], capture_output=True, check=True)

    # Step 3: Combine video + audio
    if background_music and os.path.exists(background_music):
        # Mix voiceover (loud) + background music (quiet)
        combined_audio = os.path.join(config.TEMP_DIR, "mixed_audio.mp3")
        subprocess.run([
            "ffmpeg", "-y",
            "-i", audio_path,
            "-i", background_music,
            "-filter_complex", "[0:a]volume=1.0[v];[1:a]volume=0.15[b];[v][b]amix=inputs=2:duration=first[out]",
            "-map", "[out]",
            "-t", str(video_duration),
            combined_audio
        ], capture_output=True, check=True)
        final_audio = combined_audio
    else:
        final_audio = audio_path

    # Combine slideshow + audio
    raw_output = os.path.join(config.TEMP_DIR, "raw_output.mp4")
    subprocess.run([
        "ffmpeg", "-y",
        "-i", slideshow_path,
        "-i", final_audio,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-t", str(video_duration),
        raw_output
    ], capture_output=True, check=True)

    # Step 4: Add text overlays
    _add_text_overlay(raw_output, output_path, product, script_data)

    size_mb = os.path.getsize(output_path) / 1024 / 1024
    logger.info(f"Video assembled: {output_path} ({size_mb:.1f} MB, {video_duration:.1f}s)")
    return output_path
