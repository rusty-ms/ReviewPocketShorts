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
import json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_trending_product():
    url = "https://www.amazon.com/Best-Sellers/zgbs"
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, "html.parser")
        product_blocks = soup.select(".zg-grid-general-faceout")
        if product_blocks:
            return extract_product_data(product_blocks[0])
    except Exception as e:
        print(f"Requests failed: {e}")

    print("Falling back to Selenium...")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(ChromeDriverManager().install(), options=options)
    driver.get(url)
    html = driver.page_source
    driver.quit()
    
    soup = BeautifulSoup(html, "html.parser")
    product_blocks = soup.select(".zg-grid-general-faceout")
    if not product_blocks:
        with open("output/amazon_debug.html", "w") as f:
            f.write(html)
        raise Exception("No trending product blocks found with requests or selenium.")

    return extract_product_data(product_blocks[0])


def extract_product_data(product):
    title_tag = product.select_one(".p13n-sc-truncate, ._cDEzb_p13n-sc-css-line-clamp-1")
    link_tag = product.select_one("a")
    img_tag = product.select_one("img")

    if not (title_tag and link_tag and img_tag):
        raise Exception("Incomplete product data in HTML.")

    title = title_tag.get_text(strip=True)
    raw_link = link_tag['href']
    asin = raw_link.split("/dp/")[1].split("/")[0] if "/dp/" in raw_link else None
    tag = os.getenv("AMAZON_AFFILIATE_TAG", "yourtag-20")
    link = f"https://www.amazon.com/dp/{asin}?tag={tag}" if asin else "https://www.amazon.com" + raw_link
    img = img_tag['src']
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

    credentials_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_json:
        raise Exception("GOOGLE_APPLICATION_CREDENTIALS not set in environment variables")

    with open("client_secrets.json", "w") as f:
        f.write(credentials_json)

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
    short_desc = f"\U0001F525 Trending on Amazon: {title}!"
    call_to_action = f"\n\n👉 Check it out here: {link}"
    full_description = short_desc + call_to_action

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_file = f"{OUTPUT_DIR}/audio_{timestamp}.mp3"
    video_file = f"{OUTPUT_DIR}/video_{timestamp}.mp4"
    desc_file = f"{OUTPUT_DIR}/description_{timestamp}.txt"

    generate_voiceover(short_desc, audio_file)
    create_video(img, audio_file, video_file, title)

    with open(desc_file, "w") as f:
        f.write(full_description)

    upload_video_to_youtube(video_file, title, full_description)


if __name__ == "__main__":
    main()
