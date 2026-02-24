# 🎬 ReviewPocketShorts

Automated daily pipeline that finds trending Amazon products, summarizes reviews with AI, and posts a YouTube Short + Instagram Reel — completely hands-free.

**Affiliate tag:** `reviewpockets-20` (baked in — every link earns)

---

## How It Works

```
Daily 10am (n8n cron)
  → Pick trending Amazon product (PA API)
  → Scrape top customer reviews
  → GPT-4o-mini writes punchy 45s script
  → edge-tts generates voiceover (FREE)
  → FFmpeg assembles 9:16 vertical video
  → Upload to YouTube Shorts
  → Post to Instagram Reels
  → Telegram notification (success or fail)
```

**Estimated cost per video: ~$0.03–0.05**

---

## Setup Guide

### 1. Clone & Install

```bash
git clone https://github.com/rusty-ms/ReviewPocketShorts
cd ReviewPocketShorts
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
sudo apt install ffmpeg   # or: brew install ffmpeg
```

### 2. Environment Variables

```bash
cp .env.example .env
nano .env   # Fill in your secrets
```

**Never commit `.env` to GitHub — it's gitignored.**

### 3. API Keys You'll Need

#### Amazon Product Advertising API (Free)
- Go to: https://affiliate-program.amazon.com → Tools → Product Advertising API
- Requires: Active Associates account with at least 3 qualifying sales
- Get: Access Key, Secret Key
- Your Partner Tag is already set: `reviewpockets-20`

#### OpenAI (GPT-4o-mini — very cheap)
- Go to: https://platform.openai.com/api-keys
- Create a key, set a monthly spend limit ($5–10/mo is plenty)

#### YouTube Data API v3 (Free)
1. Go to https://console.cloud.google.com
2. Create a project → Enable "YouTube Data API v3"
3. Create OAuth 2.0 credentials (Desktop app type)
4. Download `client_secret.json` → place in project root
5. Run `python main.py --dry-run` once — it opens a browser to authorize your YouTube account
6. Token is saved to `youtube_token.json` (gitignored) — never needs re-auth

#### Meta Graph API / Instagram (Free)
1. Go to https://developers.facebook.com/apps/
2. Create an app → Add "Instagram Graph API" product
3. Connect your Instagram Professional account via a Facebook Page
4. Generate a Long-Lived User Access Token (60-day expiry — must refresh periodically)
5. Set `INSTAGRAM_ACCOUNT_ID` (find it: Settings → Professional Account)

### 4. Test the Pipeline

```bash
# Dry run — full pipeline but no uploads
python main.py --dry-run

# Real run
python main.py
```

### 5. Deploy to VPS (srv1152590)

```bash
# Copy project to VPS
scp -r . user@your-vps:/opt/ReviewPocketShorts

# Install dependencies on VPS
ssh user@your-vps
cd /opt/ReviewPocketShorts
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
sudo apt install ffmpeg

# Copy your .env (never via git!)
scp .env user@your-vps:/opt/ReviewPocketShorts/.env
scp client_secret.json user@your-vps:/opt/ReviewPocketShorts/
scp youtube_token.json user@your-vps:/opt/ReviewPocketShorts/
```

### 6. Import n8n Workflow

1. Open n8n on your VPS
2. **Import** → Upload `n8n/workflow.json`
3. Configure credentials:
   - **Telegram bot** (for success/failure notifications)
   - Set n8n variable `TELEGRAM_CHAT_ID` to your chat ID
4. Activate the workflow — it runs daily at 10am

---

## Optional: Background Music

Drop a royalty-free MP3 into `assets/background_music.mp3`.
It will be mixed in quietly under the voiceover (15% volume).

Good sources:
- https://pixabay.com/music/
- https://www.bensound.com/
- YouTube Audio Library (free)

---

## File Structure

```
ReviewPocketShorts/
├── main.py                    # Pipeline entry point
├── config.py                  # All config from .env
├── requirements.txt
├── .env.example               # Template — copy to .env
├── .gitignore                 # Keeps secrets out of git
├── scripts/
│   ├── amazon_products.py     # PA API product discovery
│   ├── review_scraper.py      # Amazon review scraping
│   ├── ai_summarize.py        # GPT-4o-mini script writer
│   ├── tts_generator.py       # edge-tts voiceover (FREE)
│   ├── video_assembler.py     # FFmpeg video assembly
│   ├── youtube_uploader.py    # YouTube Data API v3
│   ├── instagram_poster.py    # Meta Graph API
│   └── product_tracker.py     # Deduplication (used_products.json)
├── n8n/
│   └── workflow.json          # Importable n8n workflow
├── assets/
│   └── background_music.mp3   # Optional (gitignored if present)
├── templates/                 # AI prompt templates
├── output/                    # Finished videos (gitignored)
├── temp/                      # Working files, auto-cleaned (gitignored)
├── data/
│   └── used_products.json     # Tracks used ASINs (gitignored)
└── logs/                      # Daily log files (gitignored)
```

---

## Cost Breakdown

| Service | Cost |
|---|---|
| Amazon PA API | FREE |
| OpenAI GPT-4o-mini | ~$0.01–0.02/video |
| edge-tts (Microsoft TTS) | FREE |
| FFmpeg | FREE |
| YouTube Data API | FREE |
| Meta Graph API | FREE |
| **Total** | **~$0.30–0.60/month** |

---

## Troubleshooting

**Amazon API returning empty results?**
- PA API requires an active Associates account with qualifying sales
- New accounts may have a delay before API access is granted
- Pipeline falls back to mock data automatically for testing

**YouTube upload fails?**
- Re-run to trigger OAuth re-auth: `python main.py --dry-run`
- Check your API quota at console.cloud.google.com

**Instagram upload fails?**
- Long-lived tokens expire every 60 days — refresh via the Graph API Explorer
- Ensure your Instagram is a Professional account linked to a Facebook Page

**Video looks bad?**
- Add more product images to the Amazon PA API resources list
- Try different product categories in `.env` (AMAZON_CATEGORIES)

---

## Security Notes

- All secrets live in `.env` only — gitignored
- `client_secret.json` and `youtube_token.json` are gitignored
- `data/used_products.json` is gitignored (no product history in git)
- n8n stores its own credentials encrypted in its database
- Only your authorized Google account can upload to YouTube
- Only your Meta access token can post to your Instagram
