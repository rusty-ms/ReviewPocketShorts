"""
authorize_youtube.py - One-time YouTube OAuth setup.

Best run on your Mac (where you have a browser) — then SCP the token to the VPS.
Can also run headless (manual copy/paste URL flow).

Usage:
    python3 authorize_youtube.py                          # auto-detect
    python3 authorize_youtube.py --headless               # force manual URL flow
    python3 authorize_youtube.py --secret ~/Downloads/client_secret.json
"""
import argparse
import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # Required for posting/pinning comments
]

parser = argparse.ArgumentParser()
parser.add_argument("--secret", default="client_secret.json",
                    help="Path to client_secret.json from Google Cloud Console")
parser.add_argument("--out", default="youtube_token.json",
                    help="Where to save the token (default: youtube_token.json)")
parser.add_argument("--headless", action="store_true",
                    help="Force manual URL flow (no browser required)")
args = parser.parse_args()

if not os.path.exists(args.secret):
    print(f"❌ client_secret.json not found at: {args.secret}")
    print("Download it from: console.cloud.google.com → APIs & Services → Credentials → OAuth 2.0 Client → Download JSON")
    exit(1)

flow = InstalledAppFlow.from_client_secrets_file(args.secret, SCOPES)

import sys
# macOS always has a browser; headless = Linux without a display
headless = args.headless or (
    sys.platform != "darwin" and
    not os.environ.get("DISPLAY") and
    not os.environ.get("WAYLAND_DISPLAY")
)

if headless:
    # Google deprecated OOB — use localhost redirect with SSH port forwarding
    print("\n⚠️  Headless server detected.")
    print("Run this from your Mac instead:\n")
    print("  cd /Users/rusty/.openclaw/workspace/projects/ReviewPocketShorts")
    print("  python3 authorize_youtube.py")
    print("\nThen SCP the token:")
    print("  scp youtube_token.json vps-n8n:/opt/ReviewPocketShorts/youtube_token.json")
    exit(1)
else:
    print("Opening browser for YouTube authorization...")
    creds = flow.run_local_server(port=8080)

with open(args.out, "wb") as f:
    pickle.dump(creds, f)

print(f"\n✅ Token saved to: {args.out}")
print("\nIf you ran this on your Mac, copy it to the VPS:")
print(f"  scp {args.out} vps-n8n:/opt/ReviewPocketShorts/youtube_token.json")
