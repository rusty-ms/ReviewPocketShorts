"""
video_assembler.py - Assemble YouTube Short using FFmpeg.
Creates a 9:16 vertical video (1080x1920) optimized for Shorts:
  - Product images as slideshow with fast crossfade transitions
  - Text overlays (product name, price, star rating)
  - TTS voiceover audio
  - Optional background music

Designed to be FAST on a headless VPS — no slow zoompan/Ken Burns.

Cost: FREE (FFmpeg)
"""
import logging
import os
import subprocess
import urllib.request
import config

logger = logging.getLogger(__name__)

W = config.VIDEO_WIDTH    # 1080
H = config.VIDEO_HEIGHT   # 1920
FPS = config.VIDEO_FPS    # 30


def download_images(image_urls: list[str], temp_dir: str) -> list[str]:
    """Download product images to temp dir. Falls back to placeholder if all fail."""
    os.makedirs(temp_dir, exist_ok=True)
    paths = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    for i, url in enumerate(image_urls[:6]):
        if not url or url.startswith("PLACEHOLDER"):
            continue
        dest = os.path.join(temp_dir, f"product_{i}.jpg")
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r, open(dest, "wb") as f:
                f.write(r.read())
            paths.append(dest)
        except Exception as e:
            logger.warning(f"Failed to download image {i}: {e}")

    # Fallback: generate a branded placeholder if nothing downloaded
    if not paths:
        logger.warning("No images downloaded — generating placeholder")
        placeholder = _generate_placeholder(temp_dir)
        if placeholder:
            paths.append(placeholder)

    logger.info(f"Downloaded {len(paths)} product images")
    return paths


def _generate_placeholder(temp_dir: str) -> str:
    """Generate a simple branded placeholder image using FFmpeg."""
    out = os.path.join(temp_dir, "placeholder.jpg")
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c=0x1a1a2e:size={W}x{H}:rate=1",
            "-frames:v", "1",
            "-vf", (
                f"drawtext=text='Review Pocket Shorts':x=(w-text_w)/2:y=(h-text_h)/2-80"
                f":fontsize=60:fontcolor=white:fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf,"
                f"drawtext=text='🛒 Amazon Product Review':x=(w-text_w)/2:y=(h-text_h)/2+20"
                f":fontsize=40:fontcolor=#FFD700:fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            ),
            out
        ], capture_output=True, timeout=15)
        return out if os.path.exists(out) else None
    except Exception as e:
        logger.warning(f"Placeholder generation failed: {e}")
        return None


def _get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=10
        )
        return float(result.stdout.strip())
    except Exception:
        return 45.0


def _run_ffmpeg(args: list[str], step: str):
    """Run ffmpeg command, raise on failure with useful error."""
    result = subprocess.run(
        ["ffmpeg", "-y"] + args,
        capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        logger.error(f"FFmpeg failed at {step}:\n{result.stderr[-2000:]}")
        raise RuntimeError(f"FFmpeg failed at step: {step}")
    return result


def _prepare_image(img_path: str, out_path: str):
    """
    Resize image to fill 1080x1920 (9:16) with blurred background fill.
    Fast: no zoompan, just scale + pad.
    """
    _run_ffmpeg([
        "-i", img_path,
        # Scale to fit within frame, then pad remainder with blurred version
        "-vf", (
            f"split[orig][blur];"
            f"[blur]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},boxblur=20:20[bg];"
            f"[orig]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
        ),
        "-q:v", "2",
        out_path
    ], "prepare_image")


def _build_slideshow(prepared_images: list[str], audio_duration: float, out_path: str):
    """
    Build slideshow from prepared images using xfade crossfade transitions.
    Fast alternative to zoompan — looks clean and professional.
    """
    n = len(prepared_images)
    video_duration = min(audio_duration + 1.0, 59.0)
    fade_duration = 0.5
    slide_duration = video_duration / n

    if n == 1:
        # Single image — just hold it for the full duration
        _run_ffmpeg([
            "-loop", "1",
            "-i", prepared_images[0],
            "-t", str(video_duration),
            "-vf", f"fps={FPS},format=yuv420p",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            out_path
        ], "single_image_slideshow")
        return video_duration

    # Multi-image: use xfade filter for smooth crossfades
    # Build input args
    input_args = []
    for img in prepared_images:
        input_args += ["-loop", "1", "-t", str(slide_duration + fade_duration), "-i", img]

    # Build xfade filter chain
    # [0][1]xfade=transition=fade:duration=0.5:offset=slide_dur[x01];
    # [x01][2]xfade=...etc
    filter_parts = []
    offset = slide_duration - fade_duration

    prev_label = "0:v"
    for i in range(1, n):
        out_label = f"x{i}" if i < n - 1 else "vout"
        filter_parts.append(
            f"[{prev_label}][{i}:v]xfade=transition=fade:"
            f"duration={fade_duration}:offset={offset:.3f}[{out_label}]"
        )
        prev_label = out_label
        offset += slide_duration

    filter_complex = ";".join(filter_parts)
    # Add fps and format
    filter_complex += f";[vout]fps={FPS},format=yuv420p[final]"

    _run_ffmpeg(
        input_args + [
            "-filter_complex", filter_complex,
            "-map", "[final]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-t", str(video_duration),
            out_path
        ],
        "multi_image_slideshow"
    )
    return video_duration


def _add_text_overlay(input_path: str, output_path: str, product: dict):
    """
    Add text overlays to the video:
    - Brand banner at top
    - Product name, price, star rating at bottom
    """
    title = product.get("title", "")[:45]
    price = product.get("price", "")
    rating = product.get("rating", "")
    stars = "★" * round(float(rating)) + "☆" * (5 - round(float(rating))) if rating and str(rating) not in ("N/A", "None", "") else ""

    def esc(s):
        return str(s).replace("'", "\u2019").replace(":", r"\:").replace(",", r"\,").replace("[", r"\[").replace("]", r"\]")

    drawtext = (
        # Top brand banner
        f"drawbox=x=0:y=0:w={W}:h=100:color=black@0.65:t=fill,"
        f"drawtext=text='Review Pocket Shorts':x=(w-text_w)/2:y=28"
        f":fontsize=38:fontcolor=white:fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        f":borderw=2:bordercolor=black@0.8,"
        # Bottom info banner — taller to fit CTA
        f"drawbox=x=0:y={H-340}:w={W}:h=340:color=black@0.65:t=fill,"
        # Product title
        f"drawtext=text='{esc(title)}':x=20:y={H-318}"
        f":fontsize=36:fontcolor=white:fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        f":borderw=1:bordercolor=black@0.8,"
        # Price
        f"drawtext=text='{esc(price)}':x=20:y={H-215}"
        f":fontsize=52:fontcolor=#FFD700:fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        f":borderw=2:bordercolor=black@0.8,"
        # Stars
        f"drawtext=text='{esc(stars)} {esc(str(rating))}/5':x=20:y={H-138}"
        f":fontsize=38:fontcolor=#FFD700:fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        f":borderw=1:bordercolor=black@0.8,"
        # Link CTA
        f"drawtext=text='Link in Description':x=20:y={H-68}"
        f":fontsize=36:fontcolor=white:fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        f":borderw=1:bordercolor=black@0.8"
    )

    _run_ffmpeg([
        "-i", input_path,
        "-vf", drawtext,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "copy",
        output_path
    ], "text_overlay")


def assemble_video(
    image_paths: list[str],
    audio_path: str,
    output_path: str,
    product: dict,
    script_data: dict,
    background_music: str = None,
) -> str:
    """
    Full video assembly pipeline for YouTube Shorts (1080x1920, <60s).
    Returns path to the final video file.
    """
    os.makedirs(config.TEMP_DIR, exist_ok=True)
    os.makedirs(config.VIDEO_OUTPUT_DIR, exist_ok=True)

    if not image_paths:
        raise ValueError("No images provided for video assembly")

    audio_duration = _get_audio_duration(audio_path)
    if audio_duration <= 0:
        audio_duration = 45.0

    logger.info(f"  Audio duration: {audio_duration:.1f}s, images: {len(image_paths)}")

    # Step 1: Prepare all images (resize/pad to 9:16 with blurred bg)
    prepared = []
    for i, img in enumerate(image_paths):
        out = os.path.join(config.TEMP_DIR, f"prepared_{i}.jpg")
        _prepare_image(img, out)
        prepared.append(out)
    logger.info(f"  Prepared {len(prepared)} images")

    # Step 2: Build slideshow
    slideshow_path = os.path.join(config.TEMP_DIR, "slideshow.mp4")
    video_duration = _build_slideshow(prepared, audio_duration, slideshow_path)
    logger.info(f"  Slideshow built: {video_duration:.1f}s")

    # Step 3: Mix audio
    if background_music and os.path.exists(background_music):
        mixed_audio = os.path.join(config.TEMP_DIR, "mixed_audio.aac")
        _run_ffmpeg([
            "-i", audio_path,
            "-i", background_music,
            "-filter_complex", "[0:a]volume=1.0[v];[1:a]volume=0.12[b];[v][b]amix=inputs=2:duration=first[out]",
            "-map", "[out]", "-c:a", "aac", "-b:a", "192k",
            "-t", str(video_duration),
            mixed_audio
        ], "mix_audio")
        final_audio = mixed_audio
    else:
        final_audio = audio_path

    # Step 4: Combine video + audio
    combined_path = os.path.join(config.TEMP_DIR, "combined.mp4")
    _run_ffmpeg([
        "-i", slideshow_path,
        "-i", final_audio,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-t", str(video_duration),
        combined_path
    ], "combine_av")

    # Step 5: Add text overlays → final output
    _add_text_overlay(combined_path, output_path, product)

    size_mb = os.path.getsize(output_path) / 1024 / 1024
    logger.info(f"  ✓ Final video: {output_path} ({size_mb:.1f} MB, {video_duration:.1f}s)")
    return output_path
