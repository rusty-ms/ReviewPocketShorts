# main.py
import os
import requests
from bs4 import BeautifulSoup
from moviepy.editor import *
from TTS.api import TTS
from datetime import datetime
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle

OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_trending_product():
    url = "https://www.amazon.com/Best-Sellers/zgbs"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.content, "html.parser")

    first_product = soup.select_one(".zg-item .p13n-sc-uncoverable-faceout")
    if not first_product:
        raise Exception("Failed to find trending product")

    title = first_product.select_one(".p13n-sc-truncate, ._cDEzb_p13n-sc-css-line-clamp-1").get_text(strip=True)
    link = "https://www.amazon.com" + first_product.select_one("a")['href']
    img = first_product.select_one("img")['src']

    return title, link, img


def generate_voiceover(text, filename):
    tts = TTS(model_name="tts_models/en/ljspeech/tacotron2-DDC")
    tts.tts_to_file(text=text, file_path=filename)


def create_video(image_url, audio_path, output_path, caption):
    response = requests.get(image_url)
    with open("temp.jpg", "wb") as f:
        f.write(response.content)

    audio = AudioFileClip(audio_path)
    img = ImageClip("temp.jpg").set_duration(audio.duration).resize(height=1920).set_position("center")
    txt = TextClip(caption, fontsize=60, color='white', method='caption', size=(1080, 200)).set_position(('center', 'bottom')).set_duration(audio.duration)

    video = CompositeVideoClip([img.set_audio(audio), txt])
    video.write_videofile(output_path, fps=24)


def authenticate_youtube():
    creds = None
    if os.path.exists("token.pickle"):
        with open("token.pickle", "rb") as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("client_secrets.json", SCOPES)
            creds = flow.run_console()
        with open("token.pickle", "wb") as token:
            pickle.dump(creds, token)

    return build("youtube", "v3", credentials=creds)


def upload_video_to_youtube(file_path, title, description):
    youtube = authenticate_youtube()
    request_body = {
        "snippet": {
            "categoryId": "22",
            "title": title,
            "description": description,
            "tags": ["amazon", "review", "product"]
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }

    mediaFile = MediaFileUpload(file_path)
    youtube.videos().insert(
        part="snippet,status",
        body=request_body,
        media_body=mediaFile
    ).execute()


def main():
    title, link, img = get_trending_product()
    short_desc = f"Check out this trending product on Amazon: {title}. It has great reviews!"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    audio_file = f"{OUTPUT_DIR}/audio_{timestamp}.mp3"
    video_file = f"{OUTPUT_DIR}/video_{timestamp}.mp4"
    desc_file = f"{OUTPUT_DIR}/description_{timestamp}.txt"

    generate_voiceover(short_desc, audio_file)
    create_video(img, audio_file, video_file, title)

    with open(desc_file, "w") as f:
        f.write(f"{short_desc}\nAffiliate link: {link}")

    upload_video_to_youtube(video_file, title, f"{short_desc}\nAffiliate link: {link}")


if __name__ == "__main__":
    main()
