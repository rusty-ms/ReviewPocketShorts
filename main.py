# main.py
from moviepy.config import change_settings
if os.getenv('GITHUB_ACTIONS'):
    change_settings({"IMAGEMAGICK_BINARY": None})
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
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_trending_product():
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    url = "https://www.amazon.com/Best-Sellers/zgbs"
    print("Launching headless Chrome to scrape Amazon best sellers...")

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    chrome_path = ChromeDriverManager().install()
    service = ChromeService(executable_path=chrome_path)
    driver = webdriver.Chrome(service=service, options=options)

    driver.get(url)

    if "captcha" in driver.page_source.lower() or "Enter the characters you see below" in driver.page_source:
        debug_path = os.path.join(OUTPUT_DIR, "amazon_debug.html")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print("CAPTCHA detected. Saved fallback HTML.")
        raise Exception("Blocked by Amazon CAPTCHA page.")

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div[data-asin]"))
        )
        soup = BeautifulSoup(driver.page_source, "html.parser")
    except Exception as e:
        print(f"Timeout or error waiting for content: {e}")
        html = driver.page_source
        debug_path = os.path.join(OUTPUT_DIR, "amazon_debug.html")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Saved fallback HTML to {debug_path}")
        raise Exception("Amazon page did not load correctly.")
    finally:
        driver.quit()

    seen_asins_path = os.path.join(OUTPUT_DIR, "seen_asins.txt")
    if os.path.exists(seen_asins_path):
        with open(seen_asins_path, "r") as f:
            seen_asins = set(line.strip() for line in f)
    else:
        seen_asins = set()

    products = soup.select("div[data-asin][data-asin!='']")
    for product in products:
        asin = product["data-asin"].strip()
        if asin in seen_asins:
            continue  # skip duplicates

        img_tag = product.select_one("img")
        if not img_tag:
            continue
        title = img_tag.get("alt", "Unknown Product").strip()
        img = img_tag.get("src", "")
        tag = os.getenv("AMAZON_AFFILIATE_TAG", "yourtag-20")
        link = f"https://www.amazon.com/dp/{asin}?tag={tag}"

        # Save this ASIN to prevent reuse
        with open(seen_asins_path, "a") as f:
            f.write(asin + "\n")

        print(f"Product Title: {title}")
        print(f"Product Link: {link}")
        print(f"Image URL: {img}")
        return title, link, img

    raise Exception("No new products found.")


def create_video(image_url, audio_path, output_path, caption):
    try:
        print(f"Downloading image from: {image_url}")
    response = requests.get(image_url)
    if response.status_code != 200:
        raise Exception(f"Failed to download image: {image_url}")

    with open("temp.jpg", "wb") as f:
        f.write(response.content)

    print("Image saved as temp.jpg")
    print(f"Loading audio from: {audio_path}")
    audio = AudioFileClip(audio_path)

    print("Composing video...")
    img = ImageClip("temp.jpg").set_duration(audio.duration).resize(height=1920).set_position("center")
    txt = TextClip(caption, fontsize=60, color='white', method='pillow').set_position(('center', 'bottom')).set_duration(audio.duration)

    video = CompositeVideoClip([img.set_audio(audio), txt])
            print(f"Writing video to: {output_path}")
        video.write_videofile(output_path, fps=24)
    except Exception as e:
        print(f"Video creation failed: {e}")
        raise
    print("Video creation complete.")


# Load TTS model once globally
tts_model = TTS(model_name="tts_models/en/ljspeech/tacotron2-DDC")
def generate_voiceover(text, filename):
    print(f"Generating voiceover for: {text}")
    tts_model.tts_to_file(text=text, file_path=filename)
    print(f"Voiceover saved to: {filename}")


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
            # NOTE: This won't work in CI. Replace with service account or pre-generated token for automation.
            flow = InstalledAppFlow.from_client_secrets_file("client_secrets.json", SCOPES)
            creds = flow.run_console()
        with open("token.pickle", "wb") as token:
            pickle.dump(creds, token)

    return build("youtube", "v3", credentials=creds)


def upload_video_to_youtube(file_path, title, description):
    print(f"Uploading {file_path} to YouTube...")
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
    print("Upload complete.")


def main():
    title, link, img = get_trending_product()
    short_desc = f"🔥 Trending on Amazon: {title}!"
    call_to_action = f"👉 Check it out here: {link}"
    full_description = short_desc + call_to_action

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_file = f"{OUTPUT_DIR}/audio_{timestamp}.mp3"
    video_file = f"{OUTPUT_DIR}/video_{timestamp}.mp4"
    desc_file = f"{OUTPUT_DIR}/description_{timestamp}.txt"

    generate_voiceover(short_desc, audio_file)
    create_video(img, audio_file, video_file, title)

    with open(desc_file, "w") as f:
        f.write(full_description)
    print(f"Description saved to: {desc_file}")

    upload_video_to_youtube(video_file, title, full_description)


if __name__ == "__main__":
    main()
