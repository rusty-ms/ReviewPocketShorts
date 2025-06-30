# main.py
import os
import time
import json
import requests
from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from moviepy.editor import AudioFileClip, ImageClip, CompositeVideoClip
from datetime import datetime
from TTS.api import TTS

# Set ImageMagick path for moviepy
os.environ["IMAGEMAGICK_BINARY"] = "/usr/bin/convert"

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Load TTS model once
tts_model = TTS(model_name="tts_models/en/ljspeech/tacotron2-DDC", progress_bar=False, gpu=False)

def authenticate_youtube():
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    return build("youtube", "v3", credentials=creds)

def get_trending_product():
    url = "https://www.amazon.com/Best-Sellers/zgbs"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36"
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Save the response HTML for debugging
        with open(os.path.join(OUTPUT_DIR, "amazon_debug.html"), "w", encoding="utf-8") as f:
            f.write(response.text)

        item = soup.select_one(".zg-grid-general-faceout a")
        img_tag = soup.select_one(".zg-grid-general-faceout img")

        if not item or not img_tag:
            raise Exception("Could not find product info in Amazon Best Sellers page.")

        title = item.get("title") or item.text.strip()
        tag = os.getenv("AMAZON_AFFILIATE_TAG", "reviewpockets-20")
        link = f"https://www.amazon.com{item.get('href')}?tag={tag}"
        img = img_tag.get("src")

        # Track ASINs to prevent repeats
        seen_asins_path = os.path.join(OUTPUT_DIR, "seen_asins.txt")
        asin = item.get("href", "").split("/dp/")[-1].split("/")[0]

        if asin:
            if os.path.exists(seen_asins_path):
                with open(seen_asins_path, "r") as f:
                    seen_asins = set(line.strip() for line in f)
            else:
                seen_asins = set()

            if asin in seen_asins:
                raise Exception("ASIN already used. Skipping.")

            with open(seen_asins_path, "a") as f:
                f.write(asin + "\n")

        return title, link, img
    except Exception as e:
        print("Failed to scrape Amazon Best Sellers:", e)
        raise

def generate_voiceover(text, filepath):
    print(f"Generating voiceover for: {text}")
    tts_model.tts_to_file(text=text, file_path=filepath)
    print(f"Voiceover saved to: {filepath}")

def create_video(image_path, audio_path, output_path):
    audio = AudioFileClip(audio_path)
    clip = ImageClip(image_path).set_duration(audio.duration).set_audio(audio).set_fps(24)
    final = CompositeVideoClip([clip], size=clip.size)
    final.write_videofile(output_path, codec='libx264', audio_codec='aac')

def upload_video_to_youtube(video_path, title, description):
    youtube = authenticate_youtube()

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": ["Amazon", "Review", "Trending"],
            "categoryId": "22"
        },
        "status": {
            "privacyStatus": "public",
        }
    }

    print(f"Uploading {video_path} to YouTube...")
    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=video_path
    )
    response = request.execute()
    print("Upload complete.", json.dumps(response, indent=2))

def main():
    title, link, img = get_trending_product()
    now = datetime.now().strftime("%Y%m%d_%H%M%S")

    image_file = f"{OUTPUT_DIR}/image_{now}.jpg"
    audio_file = f"{OUTPUT_DIR}/audio_{now}.mp3"
    video_file = f"{OUTPUT_DIR}/video_{now}.mp4"
    description_file = f"{OUTPUT_DIR}/description_{now}.txt"

    # Download image
    img_data = requests.get(img).content
    with open(image_file, 'wb') as handler:
        handler.write(img_data)

    # Generate voiceover
    short_desc = f"🔥 Trending on Amazon: {title}!"
    generate_voiceover(short_desc, audio_file)

    # Save description
    full_description = f"{short_desc}\n\n👉 Check it out here: {link}"
    with open(description_file, "w") as f:
        f.write(full_description)

    print(f"Description saved to: {description_file}")

    # Create video
    create_video(image_file, audio_file, video_file)
    print(f"Moviepy - video ready {video_file}")

    # Upload to YouTube
    upload_video_to_youtube(video_file, title, full_description)

if __name__ == "__main__":
    main()
