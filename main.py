# main.py
import os
os.environ["IMAGEMAGICK_BINARY"] = "/usr/bin/convert"

import time
import json
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from moviepy.editor import AudioFileClip, ImageClip, CompositeVideoClip
from datetime import datetime
from TTS.api import TTS

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Load TTS model once
tts_model = TTS(model_name="tts_models/en/ljspeech/tacotron2-DDC", progress_bar=False, gpu=False)

def authenticate_youtube():
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    return build("youtube", "v3", credentials=creds)

def get_trending_product():
    cache_path = os.path.join(OUTPUT_DIR, "cached_amazon_response.json")

    if os.getenv("USE_CACHE") == "1" and os.path.exists(cache_path):
        print("Using cached Amazon data for testing.")
        with open(cache_path, "r") as f:
            cached = json.load(f)
        return cached["title"], cached["link"], cached["img"]

    try:
        api_key = os.getenv("RAPIDAPI_KEY")
        if not api_key:
            raise Exception("RAPIDAPI_KEY environment variable is missing.")

        url = "https://real-time-amazon-data.p.rapidapi.com/bestsellers"
        headers = {
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": "real-time-amazon-data.p.rapidapi.com"
        }
        params = {"category_id": "aps", "country": "US"}

        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        product = data["data"]["products"][0]
        title = product["title"]
        asin = product["asin"]
        img = product["image"]
        tag = os.getenv("AMAZON_AFFILIATE_TAG", "yourtag-20")
        link = f"https://www.amazon.com/dp/{asin}?tag={tag}"

        # Track ASINs to prevent repeats
        seen_asins_path = os.path.join(OUTPUT_DIR, "seen_asins.txt")
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

        # Cache result
        with open(cache_path, "w") as f:
            json.dump({"title": title, "link": link, "img": img}, f)

        return title, link, img
    except Exception as e:
        print("Failed to fetch from RapidAPI:", e)
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
