"""
authorize_youtube.py - One-time YouTube OAuth setup.
Run this on your Mac (where you have a browser).
It saves youtube_token.json, which you then SCP to the VPS.

Usage:
    python3 authorize_youtube.py --secret ~/Downloads/client_secret.json
"""
import argparse
import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

parser = argparse.ArgumentParser()
parser.add_argument("--secret", default="client_secret.json",
                    help="Path to your client_secret.json from Google Cloud Console")
parser.add_argument("--out", default="youtube_token.json",
                    help="Where to save the token (default: youtube_token.json)")
args = parser.parse_args()

if not os.path.exists(args.secret):
    print(f"❌ client_secret.json not found at: {args.secret}")
    print("Download it from: console.cloud.google.com → APIs & Services → Credentials → your OAuth 2.0 Client → Download JSON")
    exit(1)

print("Opening browser for YouTube authorization...")
flow = InstalledAppFlow.from_client_secrets_file(args.secret, SCOPES)
creds = flow.run_local_server(port=0)

with open(args.out, "wb") as f:
    pickle.dump(creds, f)

print(f"\n✅ Token saved to: {args.out}")
print("\nNow copy it to the VPS:")
print(f"  scp {args.out} root@185.164.111.86:/opt/ReviewPocketShorts/youtube_token.json")
