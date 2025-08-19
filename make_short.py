"""
Minimal script to generate a short video from a product title and image.

This script uses the `edge-tts` library to synthesize a voiceover without
requiring an API key, the `Pillow` library to render a simple text title
slide, and `moviepy` for assembling the audio and images into a 1080×1920
video.  All outputs are written into an `output/` directory relative to
the script location.

Environment variables:
  PRODUCT_TITLE:        Title of the product to display (default: "Example Product").
  PRODUCT_IMAGE_URL:    URL of the product image (default: placeholder image).
  PRODUCT_LINK:         Optional affiliate link appended to the voiceover.

To use this script in a GitHub Actions workflow you can set these
variables via `env:` in your YAML.  Make sure to install the
dependencies from `requirements.txt` before running.
"""

import os
import asyncio
import requests
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import (
    AudioFileClip,
    ImageClip,
    concatenate_videoclips,
)
import edge_tts


async def generate_voiceover(text: str, output_path: str, voice: str = "en-US-AriaNeural") -> None:
    """Generate a voiceover using edge-tts and save to output_path."""
    communicate = edge_tts.Communicate(text, voice)
    # Stream the audio data to file
    with open(output_path, "wb") as out_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                out_file.write(chunk["data"])


def download_image(url: str, path: str) -> None:
    """Download an image from a URL and save it to the given path."""
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    with open(path, "wb") as f:
        f.write(resp.content)


def create_text_image(
    text: str,
    size: tuple[int, int] = (1080, 1920),
    bg_color: tuple[int, int, int] = (255, 255, 255),
    font_path: str | None = None,
    font_size: int = 64,
) -> str:
    """
    Create a simple centered text slide using Pillow and return the filename.

    The returned image path is a temporary file saved in the current
    working directory.  You can move or rename it as needed.
    """
    img = Image.new("RGB", size, bg_color)
    draw = ImageDraw.Draw(img)
    # Try to use a TrueType font if available; fall back to default
    if font_path and os.path.exists(font_path):
        font = ImageFont.truetype(font_path, font_size)
    else:
        try:
            # Attempt to use a common system font
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()
    # Wrap text to fit within the width
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        if draw.textsize(candidate, font=font)[0] < size[0] - 80:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    # Compute starting y coordinate to vertically center the text
    total_height = sum(draw.textsize(line, font=font)[1] for line in lines) + (len(lines) - 1) * 20
    y = (size[1] - total_height) // 2
    for line in lines:
        w, h = draw.textsize(line, font=font)
        x = (size[0] - w) // 2
        draw.text((x, y), line, font=font, fill=(0, 0, 0))
        y += h + 20
    tmp_path = "title_slide.jpg"
    img.save(tmp_path)
    return tmp_path


def main() -> None:
    # Read environment variables or use defaults
    # Use environment variables if provided; otherwise fall back to sensible defaults.
    # Note: os.getenv returns an empty string if the variable is set but blank, so
    # we explicitly use the "or" operator to handle blank values.
    product_title = os.getenv("PRODUCT_TITLE") or "Example Product"
    product_image_url = (
        os.getenv("PRODUCT_IMAGE_URL")
        or "https://upload.wikimedia.org/wikipedia/commons/6/6b/Placeholder_image.png"
    )
    product_link = os.getenv("PRODUCT_LINK") or ""
    tagline = f"\U0001F525 Trending on Amazon: {product_title}!"
    description = tagline
    if product_link:
        description += f"\n\n👉 Check it out here: {product_link}"

    # Prepare output directory and filenames
    output_dir = os.path.join(os.getcwd(), "output")
    os.makedirs(output_dir, exist_ok=True)
    image_path = os.path.join(output_dir, "product.jpg")
    audio_path = os.path.join(output_dir, "voice.mp3")
    video_path = os.path.join(output_dir, "video.mp4")
    title_image_path = os.path.join(output_dir, "title.jpg")

    # Download the product image
    download_image(product_image_url, image_path)
    # Synthesize the voiceover asynchronously
    asyncio.run(generate_voiceover(description, audio_path))
    # Create a simple title slide and move it into output
    tmp_title = create_text_image(tagline)
    os.replace(tmp_title, title_image_path)

    # Load the audio to compute its duration
    audio_clip = AudioFileClip(audio_path)
    duration = audio_clip.duration

    # Create a short title clip (e.g. 3 seconds) and a main image clip
    title_clip = ImageClip(title_image_path).set_duration(3).set_fps(30)
    main_clip = (
        ImageClip(image_path)
        .set_duration(duration)
        .set_fps(30)
        .set_audio(audio_clip)
    )
    # Concatenate the clips
    final_clip = concatenate_videoclips([title_clip, main_clip])
    # Write the video file using H.264 codec
    final_clip.write_videofile(video_path, codec="libx264", audio_codec="aac", fps=30)


if __name__ == "__main__":
    main()
