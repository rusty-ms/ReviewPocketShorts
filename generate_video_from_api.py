"""
Generate an Amazon product review video using the Real‑Time Amazon Data API.

This script fetches product details directly from the OpenWeb Ninja
Real‑Time Amazon Data API on RapidAPI and assembles a short review
video using the `edge‑tts` library for narration and MoviePy for
video composition.  It downloads the product image, synthesises a
voice‑over describing the product, creates a simple title slide and
stitches everything together into a 1080×1920 MP4 video.  The
resulting video is saved to the ``output/`` directory relative to the
script location.

Required environment variables:

  RAPIDAPI_KEY      – Your RapidAPI key (X‑RapidAPI‑Key).  Obtain this
                      from your RapidAPI developer dashboard.
  PRODUCT_ASIN      – The ASIN of the Amazon product to fetch.  You
                      can supply multiple ASINs separated by commas to
                      fetch the first available product.

Optional environment variables:

  RAPIDAPI_HOST     – The RapidAPI host for the service
                      (default: ``real-time-amazon-data.p.rapidapi.com``).
  REGION            – Amazon marketplace region code (default: ``US``).
  AMAZON_AFFILIATE_TAG – Your Amazon Associates affiliate tag.  When
                      present, the product link in the voice‑over will
                      include this tag.

Example usage:

    RAPIDAPI_KEY=xxxxx PRODUCT_ASIN=B07ZPKBL9V python generate_video_from_api.py

The script will contact the API endpoint ``/product`` with the
specified ASIN and region, parse the product title and image URL,
generate a voice‑over that mentions the product and optionally
promotes a link with your affiliate tag, then create a video.  You can
open the resulting ``output/video.mp4`` file locally or upload it
wherever you like.

Note:  Never commit your RapidAPI key directly into your source code
or repository.  Use environment variables or secret managers to
protect sensitive credentials.  This script reads the key at runtime
so you can configure it securely in GitHub Actions or your local
environment.
"""

import asyncio
import json
import os
from typing import Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import AudioFileClip, ImageClip, concatenate_videoclips
import edge_tts


def fetch_product_details(asin: str, region: str, rapidapi_key: str, host: str) -> Tuple[str, str, str]:
    """Fetch product details from the Real‑Time Amazon Data API.

    Parameters
    ----------
    asin : str
        The Amazon Standard Identification Number for the product.
    region : str
        The Amazon marketplace region (e.g. ``US``, ``GB``, ``DE``).
    rapidapi_key : str
        Your RapidAPI key used for authentication.
    host : str
        The RapidAPI host for the Real‑Time Amazon Data API.  Defaults
        to ``real-time-amazon-data.p.rapidapi.com``.

    Returns
    -------
    Tuple[str, str, str]
        A tuple containing the product title, a primary image URL, and
        the canonical product URL on Amazon.  Raises an exception if
        the API request fails or returns unexpected data.
    """
    url = f"https://{host}/product"
    headers = {
        "X-RapidAPI-Key": rapidapi_key,
        "X-RapidAPI-Host": host,
    }
    params = {
        "asin": asin,
        "region": region,
    }
    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    # Expect the API to return a top-level dictionary with product
    # details under keys like "product_title", "product_photo" and
    # "product_url".  These names are based on the API documentation
    # and may need adjustment if the provider changes their schema.
    try:
        title: str = data["product_title"]
        # product_photo may be a string or a list; normalise to str
        photo = data["product_photo"]
        if isinstance(photo, list):
            image_url = photo[0]
        else:
            image_url = photo
        product_url: str = data.get("product_url", f"https://www.amazon.com/dp/{asin}")
        return title, image_url, product_url
    except Exception as exc:
        raise ValueError(
            f"Unexpected response format when fetching product {asin}: {json.dumps(data)[:500]}"
        ) from exc


async def generate_voiceover(text: str, output_path: str, voice: str = "en-US-AriaNeural") -> None:
    """Generate a voiceover using edge‑tts and save it to output_path."""
    communicate = edge_tts.Communicate(text, voice)
    with open(output_path, "wb") as out_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                out_file.write(chunk["data"])


def download_image(url: str, path: str) -> None:
    """Download an image from a URL and save it to the given path."""
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    with open(path, "wb") as f:
        f.write(resp.content)


def create_text_image(
    text: str,
    size: Tuple[int, int] = (1080, 1920),
    bg_color: Tuple[int, int, int] = (255, 255, 255),
    font_path: Optional[str] = None,
    font_size: int = 64,
) -> str:
    """
    Create a centred text slide using Pillow and return the filename.

    Parameters
    ----------
    text : str
        The message to display on the slide.
    size : tuple, optional
        The (width, height) of the image in pixels.  Defaults to
        1080×1920 (portrait orientation).
    bg_color : tuple, optional
        The background colour as an RGB tuple.  Defaults to white.
    font_path : str or None, optional
        Path to a TrueType font file.  If not provided, attempts to
        load a common system font; falls back to Pillow's default.
    font_size : int, optional
        Font size in points.  Defaults to 64.

    Returns
    -------
    str
        The path to the temporary image file saved in the current
        working directory.
    """
    img = Image.new("RGB", size, bg_color)
    draw = ImageDraw.Draw(img)
    # Load a TrueType font if available
    if font_path and os.path.exists(font_path):
        font = ImageFont.truetype(font_path, font_size)
    else:
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()
    # Wrap text within the slide width minus padding
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        candidate = (current_line + " " + word).strip()
        if draw.textsize(candidate, font=font)[0] < size[0] - 80:
            current_line = candidate
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    total_height = sum(draw.textsize(line, font=font)[1] for line in lines) + (len(lines) - 1) * 20
    y = (size[1] - total_height) // 2
    for line in lines:
        w, h = draw.textsize(line, font=font)
        x = (size[0] - w) // 2
        draw.text((x, y), line, font=font, fill=(0, 0, 0))
        y += h + 20
    out_name = "title_slide.jpg"
    img.save(out_name)
    return out_name


def create_video(
    product_title: str,
    image_path: str,
    audio_path: str,
    tagline: str,
    output_dir: str,
) -> str:
    """Assemble the title slide, product image and voice‑over into a video.

    Parameters
    ----------
    product_title : str
        The product title used for narration and display.
    image_path : str
        Path to the downloaded product image.
    audio_path : str
        Path to the recorded voice‑over audio file.
    tagline : str
        The headline text for the title slide.
    output_dir : str
        Directory where the final video will be saved.

    Returns
    -------
    str
        Path to the generated MP4 video file.
    """
    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    title_image_filename = create_text_image(tagline)
    title_image_path = os.path.join(output_dir, "title.jpg")
    os.replace(title_image_filename, title_image_path)
    # Load audio and compute its duration
    audio_clip = AudioFileClip(audio_path)
    duration = audio_clip.duration
    # Create a 3 second title clip and a main image clip of matching duration
    title_clip = ImageClip(title_image_path).set_duration(3).set_fps(30)
    main_clip = (
        ImageClip(image_path)
        .set_duration(duration)
        .set_fps(30)
        .set_audio(audio_clip)
    )
    final_clip = concatenate_videoclips([title_clip, main_clip])
    video_path = os.path.join(output_dir, "video.mp4")
    final_clip.write_videofile(video_path, codec="libx264", audio_codec="aac", fps=30)
    return video_path


def main() -> None:
    # Read configuration from environment variables
    rapidapi_key = os.getenv("RAPIDAPI_KEY")
    if not rapidapi_key:
        raise RuntimeError(
            "RAPIDAPI_KEY is not set. Please export your RapidAPI key as an environment variable."
        )
    host = os.getenv("RAPIDAPI_HOST", "real-time-amazon-data.p.rapidapi.com")
    region = os.getenv("REGION", "US")
    raw_asins = (os.getenv("PRODUCT_ASIN", "").strip())
    if not raw_asins:
        raise RuntimeError(
            "PRODUCT_ASIN is not set. Please specify the ASIN of the product you wish to fetch."
        )
    # If multiple ASINs are provided (comma separated), pick the first non-empty entry
    asin = next((a for a in raw_asins.split(",") if a.strip()), None)
    if not asin:
        raise RuntimeError("No valid ASIN provided.")
    # Fetch product details from API
    title, image_url, product_url = fetch_product_details(asin, region, rapidapi_key, host)
    # Construct voice‑over text
    affiliate_tag = os.getenv("AMAZON_AFFILIATE_TAG", "").strip()
    if affiliate_tag:
        affiliate_link = f"https://www.amazon.com/dp/{asin}?tag={affiliate_tag}"
    else:
        affiliate_link = product_url
    tagline = f"🔥 Trending on Amazon: {title}!"
    description = f"Check out {title} on Amazon!"
    if affiliate_link:
        description += f"\n\n👉 Buy it here: {affiliate_link}"
    # Prepare output directories
    output_dir = os.path.join(os.getcwd(), "output")
    os.makedirs(output_dir, exist_ok=True)
    image_path = os.path.join(output_dir, "product.jpg")
    audio_path = os.path.join(output_dir, "voice.mp3")
    # Download product image
    download_image(image_url, image_path)
    # Generate voiceover
    asyncio.run(generate_voiceover(description, audio_path))
    # Create video
    create_video(title, image_path, audio_path, tagline, output_dir)
    print(f"Successfully created video for {title}")


if __name__ == "__main__":
    main()