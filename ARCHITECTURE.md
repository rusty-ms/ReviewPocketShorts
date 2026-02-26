# ReviewPocketShorts — Architecture & Operations Guide

> **Last updated:** 2026-02-26  
> **Maintainer:** Rusty (rusty-ms) + FRIDAY (AI assistant)

---

## Overview

ReviewPocketShorts is a fully automated, hands-free YouTube Shorts channel that:

1. Discovers trending Amazon products daily
2. Summarizes real customer reviews with AI
3. Generates a punchy 45-second voiceover script
4. Assembles a 9:16 vertical video with product images
5. Uploads to YouTube Shorts
6. Publishes a product page to the companion website
7. (Future) Posts to Instagram Reels

Affiliate links are embedded in every video description and product page using tag `reviewpockets-20`.

**Estimated cost per video: ~$0.03–0.05**

---

## Infrastructure Map

```
┌─────────────────────────────────────────────────────────┐
│  vps-n8n  (185.164.111.86)                              │
│                                                         │
│  ┌──────────┐    ┌──────────────────────────────────┐   │
│  │   n8n    │───▶│  webhook_server.py (:8765)       │   │
│  │ (Docker) │    │  main.py (pipeline orchestrator) │   │
│  └──────────┘    │  FFmpeg, edge-tts, Python venv   │   │
│       │          └──────────────────────────────────┘   │
│       │                                                 │
│  Traefik (TLS)                                          │
└───────┼─────────────────────────────────────────────────┘
        │ ntfy notifications
        ▼
┌───────────────────────┐
│  vps  (5.183.11.32)   │
│  ntfy  (port 8080)    │
│  Uptime Kuma (:3001)  │
└───────────────────────┘

External services:
  - Amazon PA API     → product discovery + images
  - RapidAPI          → fallback product images
  - OpenAI GPT-4o-mini → script generation
  - edge-tts (free)  → voiceover
  - YouTube Data API v3 → video upload
  - GitHub Pages      → website (rusty-ms/ReviewPocketShortsWeb)
  - Meta Graph API    → Instagram (not yet configured)
```

### SSH Aliases

| Alias    | IP               | Purpose                          |
|----------|------------------|----------------------------------|
| `vps`    | 5.183.11.32      | ntfy + Uptime Kuma only          |
| `vps-n8n`| 185.164.111.86   | n8n + ReviewPocketShorts pipeline |

> ⚠️ **Do not confuse these two servers.** The pipeline lives on `vps-n8n`, not `vps`.

---

## Daily Pipeline Flow

```
n8n (16:00 UTC / 10:00 AM CST)
  │
  ├── ntfy: "🎬 RPS Pipeline Starting"
  │
  ├── POST http://127.0.0.1:8765/run  ← webhook_server.py
  │     │
  │     └── main.py (background process)
  │           │
  │           ├── Step 1: Pick fresh Amazon product (PA API → RapidAPI fallback → mock)
  │           ├── Step 2: Scrape top 5 customer reviews
  │           ├── Step 3: GPT-4o-mini generates 45s script + title + hashtags
  │           ├── Step 4: edge-tts generates MP3 voiceover (FREE)
  │           ├── Step 5: Download product images
  │           ├── Step 6: FFmpeg assembles 9:16 vertical MP4 (1080x1920)
  │           ├── Step 7: Upload to YouTube Shorts
  │           ├── Step 7b: Publish product page to GitHub Pages website
  │           ├── Step 8: Post to Instagram Reels (skipped if not configured)
  │           └── mark_used() — prevents ASIN from being reused
  │
  ├── n8n polls /status every 90s until complete
  │
  └── ntfy: "✅ RPS Posted!" or "❌ RPS FAILED"
```

---

## Server Setup (vps-n8n)

### Docker Services

```yaml
# /root/docker-compose.yml
services:
  traefik:   # TLS termination, reverse proxy
  n8n:       # Workflow automation, cron, ntfy notifications
```

n8n URL: **https://n8n.srv1081937.hstgr.cloud**

### Project Location

```
/opt/ReviewPocketShorts/
├── main.py                  # Pipeline orchestrator
├── config.py                # Config loaded from .env
├── webhook_server.py        # Flask server on :8765 (triggers main.py)
├── .env                     # All secrets (chmod 600, never in git)
├── client_secret.json       # Google OAuth (gitignored)
├── youtube_token.json       # YouTube auth token (gitignored)
├── venv/                    # Python virtualenv
├── scripts/
│   ├── amazon_products.py   # PA API product discovery + dedup
│   ├── review_scraper.py    # Scrapes Amazon reviews
│   ├── ai_summarize.py      # GPT-4o-mini script generation
│   ├── tts_generator.py     # edge-tts voiceover (free)
│   ├── video_assembler.py   # FFmpeg 9:16 video assembly
│   ├── youtube_uploader.py  # YouTube Data API v3 upload
│   ├── instagram_poster.py  # Meta Graph API (not yet active)
│   ├── website_publisher.py # Publishes to ReviewPocketShortsWeb repo
│   ├── product_tracker.py   # ASIN deduplication (used_products.json)
│   └── url_shortener.py     # Affiliate URL helpers
├── n8n/
│   └── workflow.json        # Importable n8n workflow
├── assets/
│   └── background_music.mp3 # Optional royalty-free background music (gitignored)
├── output/                  # Finished videos (gitignored)
├── temp/                    # Working files, auto-cleaned (gitignored)
├── data/
│   └── used_products.json   # Tracks used ASINs (gitignored)
└── logs/                    # Daily log files (gitignored)
    ├── YYYY-MM-DD.log
    └── webhook.log
```

---

## n8n Workflow

**Workflow ID:** `rps-daily-pipeline`  
**Workflow Name:** ReviewPocketShorts - Daily Pipeline

### Nodes

| Node | Type | Purpose |
|------|------|---------|
| Daily 10am CST Trigger | Schedule | Cron: `0 16 * * *` (16:00 UTC = 10am CST) |
| Notify Start | HTTP Request | POST to ntfy — "Pipeline Starting" |
| Trigger Pipeline | HTTP Request | POST to `http://127.0.0.1:8765/run` |
| Wait 90s | Wait | Initial wait for pipeline to run |
| Poll Status | HTTP Request | GET `http://127.0.0.1:8765/status` |
| Still Running? | IF | Loops back if `running == true` |
| Wait 60s More | Wait | Extra wait per poll loop |
| Success? | IF | Branches on `last_result.success` |
| Format Success | Code | Builds ntfy success message |
| Format Error | Code | Builds ntfy failure message |
| Notify Success | HTTP Request | POST to ntfy — "✅ RPS Posted!" |
| Notify Error | HTTP Request | POST to ntfy — "❌ RPS FAILED" |

### ntfy Configuration

- **Server:** `http://5.183.11.32:8080`
- **Topic:** `rusty-alerts-e1856d11`
- **No API key required** (internal LAN access)

### Reimporting the Workflow

If n8n is reset or migrated:

```bash
scp n8n/workflow.json vps-n8n:/tmp/rps-workflow.json
ssh vps-n8n "docker cp /tmp/rps-workflow.json root-n8n-1:/tmp/ && \
  docker exec root-n8n-1 n8n import:workflow --input=/tmp/rps-workflow.json && \
  docker exec root-n8n-1 n8n publish:workflow --id=rps-daily-pipeline && \
  cd /root && docker compose restart n8n"
```

> **Note:** Strip `tags` array from workflow.json before importing (n8n CLI limitation).

---

## Environment Variables (.env)

| Variable | Description |
|----------|-------------|
| `RAPIDAPI_HOST` | `real-time-amazon-data.p.rapidapi.com` |
| `RAPIDAPI_KEY` | RapidAPI key (secret) |
| `AMAZON_PARTNER_TAG` | `reviewpockets-20` |
| `AMAZON_ACCESS_KEY` | PA API access key (secret) |
| `AMAZON_SECRET_KEY` | PA API secret key (secret) |
| `AMAZON_HOST` | `webservices.amazon.com` |
| `AMAZON_REGION` | `us-east-1` |
| `OPENAI_API_KEY` | OpenAI key (secret) |
| `OPENAI_MODEL` | `gpt-4o-mini` |
| `TTS_VOICE` | `nova` (edge-tts voice) |
| `YOUTUBE_CHANNEL_ID` | `ReviewPocketShorts` |
| `WEBHOOK_SECRET` | Shared secret for webhook auth (secret) |
| `RPS_WEBHOOK_SECRET` | Same secret, used in n8n env (secret) |
| `META_APP_ID` | Facebook app ID (not yet configured) |
| `INSTAGRAM_ACCOUNT_ID` | Instagram account ID (not yet configured) |
| `VIDEO_OUTPUT_DIR` | `/opt/ReviewPocketShorts/output` |
| `TEMP_DIR` | `/opt/ReviewPocketShorts/temp` |
| `DATA_DIR` | `/opt/ReviewPocketShorts/data` |
| `AMAZON_CATEGORIES` | `Electronics,Beauty,Kitchen,Toys,Sports` |
| `VIDEO_WIDTH` | `1080` |
| `VIDEO_HEIGHT` | `1920` |
| `VIDEO_FPS` | `30` |
| `VIDEO_DURATION_TARGET` | `45` |

---

## API Keys & Credentials

### Amazon Product Advertising API
- Associates account required with active qualifying sales
- Dashboard: https://affiliate-program.amazon.com → Tools → Product Advertising API
- Partner tag: `reviewpockets-20`

### OpenAI
- Dashboard: https://platform.openai.com/api-keys
- Model: `gpt-4o-mini` (~$0.01-0.02/video)
- Recommended monthly cap: $5–10

### YouTube Data API v3
- Console: https://console.cloud.google.com
- OAuth 2.0 credentials (Desktop app type)
- `client_secret.json` → project root (gitignored)
- `youtube_token.json` → auto-generated on first auth run (gitignored)
- Re-auth: `python authorize_youtube.py`

### Instagram (Meta Graph API) — Not Yet Configured
- Requires: Professional Instagram account + Facebook Page
- Long-lived token expires every 60 days (must refresh)
- Setup: https://developers.facebook.com/apps/

---

## Deployed Videos

| Date | Product | ASIN | YouTube |
|------|---------|------|---------|
| 2026-02-24 | (test runs) | B08N5KWB9H, B07PXGQC1Q, B09B8YWXDF, B08DFPV5RP, B07MQWQJBT | various |
| 2026-02-25 | KitchenAid Shears | B07PZF3QS3 | https://www.youtube.com/shorts/GQjVuAmyuhk |

---

## Companion Website

- **Repo:** `rusty-ms/ReviewPocketShortsWeb`
- **Live:** https://rusty-ms.github.io/ReviewPocketShortsWeb/
- **Publishes:** One product page per video at `/products/{ASIN}.html`
- **Auto-updated:** Every pipeline run commits + pushes via `website_publisher.py`

---

## Manual Operations

### Trigger a Pipeline Run Now
```bash
ssh vps-n8n
curl -s -X POST http://localhost:8765/run \
  -H "X-Webhook-Secret: $WEBHOOK_SECRET" \
  -H "Content-Type: application/json"
```

### Check Pipeline Status
```bash
ssh vps-n8n "curl -s http://localhost:8765/status | python3 -m json.tool"
```

### Dry Run (no uploads)
```bash
ssh vps-n8n "cd /opt/ReviewPocketShorts && source venv/bin/activate && python main.py --dry-run"
```

### View Today's Log
```bash
ssh vps-n8n "tail -f /opt/ReviewPocketShorts/logs/$(date +%Y-%m-%d).log"
```

### View Webhook Log
```bash
ssh vps-n8n "tail -50 /opt/ReviewPocketShorts/logs/webhook.log"
```

### Re-auth YouTube (if token expires)
```bash
ssh vps-n8n "cd /opt/ReviewPocketShorts && source venv/bin/activate && python authorize_youtube.py"
```

### Update Code from GitHub
```bash
ssh vps-n8n "cd /opt/ReviewPocketShorts && git pull && source venv/bin/activate && pip install -r requirements.txt -q"
```

---

## Troubleshooting

### Pipeline ran but no video posted
1. Check today's log: `tail -100 /opt/ReviewPocketShorts/logs/$(date +%Y-%m-%d).log`
2. Check webhook log: `tail -50 /opt/ReviewPocketShorts/logs/webhook.log`
3. Check n8n execution history at https://n8n.srv1081937.hstgr.cloud

### `KeyError: 'used'` in product_tracker.py
- Usually stale `.pyc` cache. Fix: `find /opt/ReviewPocketShorts -name '*.pyc' -delete`
- Verify `data/used_products.json` has correct structure: `{"used": [], "history": []}`

### IndentationError in website_publisher.py
- Caused by a known formatting bug introduced during editing
- Fix: review lines around `git config user.name` in `scripts/website_publisher.py`

### Amazon PA API returning 404 (active issue as of 2026-02-26)
- PA API endpoint `https://webservices.amazon.com/paapi5/searchitems` returning 404
- Pipeline automatically falls back: PA API → RapidAPI → mock data
- Not blocking but means real product data isn't being used
- Check PA API credentials in `.env` — verify `AMAZON_ACCESS_KEY`, `AMAZON_SECRET_KEY`, `AMAZON_PARTNER_TAG`
- May require active qualifying sales on the Associates account before API is fully enabled

### Amazon API returning empty results / 503
- PA API requires active Associates account with qualifying sales
- 503 on direct Amazon URLs is bot blocking — not an issue (pipeline uses PA API directly)
- Pipeline falls back to RapidAPI, then mock data automatically

### YouTube upload fails / auth error
- Re-run auth: `python authorize_youtube.py`
- Check quota at https://console.cloud.google.com

### Instagram upload fails
- Long-lived tokens expire every 60 days — refresh via Graph API Explorer
- Ensure Instagram is a Professional account linked to a Facebook Page

### n8n workflow not firing
- Check workflow is active at https://n8n.srv1081937.hstgr.cloud
- Check n8n container: `ssh vps-n8n "docker ps"`
- Restart if needed: `ssh vps-n8n "cd /root && docker compose restart n8n"`

### Webhook server not responding
- Check if webhook_server.py is running: `ssh vps-n8n "ps aux | grep webhook_server"`
- Restart: `ssh vps-n8n "systemctl restart reviewpocketshorts"` (if service is configured)

---

## Cost Breakdown

| Service | Cost |
|---------|------|
| Amazon PA API | FREE |
| RapidAPI (fallback) | FREE tier |
| OpenAI GPT-4o-mini | ~$0.01–0.02/video |
| edge-tts (Microsoft) | FREE |
| FFmpeg | FREE |
| YouTube Data API | FREE |
| Meta Graph API | FREE |
| GitHub Pages (website) | FREE |
| **Total** | **~$0.30–0.60/month** |

---

## Changelog

| Date | Change |
|------|--------|
| 2026-02-24 | Initial project setup, first test videos generated |
| 2026-02-24 | Deployed to vps-n8n (185.164.111.86) |
| 2026-02-25 | Added website_publisher.py — auto-publishes to GitHub Pages |
| 2026-02-25 | Fixed product image handling (PA API → RapidAPI → mock fallback) |
| 2026-02-25 | First successful full pipeline run — KitchenAid Shears (B07PZF3QS3) |
| 2026-02-26 | Fixed IndentationError in website_publisher.py (git config user.name) |
| 2026-02-26 | Cleared stale __pycache__ causing product_tracker KeyError |
| 2026-02-26 | Replaced cron job with n8n workflow (visual flow + ntfy notifications) |
| 2026-02-26 | Updated n8n workflow: ntfy instead of Telegram, trigger time 16:00 UTC |
| 2026-02-26 | Workflow imported to n8n and activated (ID: rps-daily-pipeline) |
| 2026-02-26 | Dry run test passed ✅ — full pipeline clean, video assembled (1.7MB, 46s) |
| 2026-02-26 | Known issue: Amazon PA API returning 404 — pipeline falls back to RapidAPI → mock |
