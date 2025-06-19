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
    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    driver.get(url)
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-asin] img"))
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

    product = soup.select_one("div[data-asin][data-asin!='']")
    if not product:
        debug_path = os.path.join(OUTPUT_DIR, "amazon_debug.html")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(str(soup))
        print(f"Saved debug HTML to {debug_path}")
        raise Exception("No valid product found on Amazon best sellers page.")

    title_tag = product.select_one("img")
    img_tag = product.select_one("img")
    asin = product["data-asin"]
    tag = os.getenv("AMAZON_AFFILIATE_TAG", "yourtag-20")
    title = title_tag.get("alt") if title_tag else "Unknown Product"
    link = f"https://www.amazon.com/dp/{asin}?tag={tag}"
    img = img_tag.get("src") if img_tag else ""

    print(f"Product Title: {title}")
    print(f"Product Link: {link}")
    print(f"Image URL: {img}")
    return title, link, img


def create_video(image_url, audio_path, output_path, caption):
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
    txt = TextClip(caption, fontsize=60, color='white', method='caption', size=(1080, 200))\
        .set_position(('center', 'bottom')).set_duration(audio.duration)

    video = CompositeVideoClip([img.set_audio(audio), txt])
    print(f"Writing video to: {output_path}")
    video.write_videofile(output_path, fps=24)
    print("Video creation complete.")
