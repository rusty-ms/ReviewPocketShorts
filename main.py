# main.py
import os
import requests
from bs4 import BeautifulSoup
from moviepy.editor import *
from TTS.api import TTS
from datetime import datetime

OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)


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


def main():
    title, link, img = get_trending_product()
    short_desc = f"Check out this trending product on Amazon: {title}. It has great reviews!"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_file = f"{OUTPUT_DIR}/audio_{timestamp}.mp3"
    video_file = f"{OUTPUT_DIR}/video_{timestamp}.mp4"

    generate_voiceover(short_desc, audio_file)
    create_video(img, audio_file, video_file, title)

    with open(f"{OUTPUT_DIR}/description_{timestamp}.txt", "w") as f:
        f.write(f"{short_desc}\nAffiliate link: {link}")


if __name__ == "__main__":
    main()
