#!/usr/bin/env python3
"""Upload the daily Multiverse Gazette Short to YouTube (optional stage).

Reads the metadata JSON written by scripts/make_short.py
(promo_build/shorts/YYYY-MM-DD-short.json, date from SHORT_DATE or today UTC;
an explicit path may also be passed as argv[1]) and uploads the mp4 via the
YouTube Data API v3 using refresh-token auth — no browser involved, so it
works in GitHub Actions.

Required env (all three, otherwise the upload is SKIPPED, not failed):
  YT_CLIENT_ID       OAuth client ID (Google Cloud, YouTube Data API v3 enabled)
  YT_CLIENT_SECRET   OAuth client secret
  YT_REFRESH_TOKEN   refresh token authorized for scope
                     https://www.googleapis.com/auth/youtube.upload

Behavior:
  - status: public, selfDeclaredMadeForKids: false
  - altered-content / synthetic-media disclosure: sent as
    status.containsSyntheticMedia=true. This field exists in the current
    YouTube Data API v3 videos resource; if a given API revision does not
    support it the API silently drops unknown fields, so the upload still
    succeeds — the script checks the response and reports whether the
    disclosure was acknowledged.
  - quota: one videos.insert costs ~1600 quota units (default daily quota
    10,000), so one Short per day is comfortably within budget.
"""

import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_ROOT = Path(os.environ.get("SHORT_BUILD_DIR",
                                 REPO_ROOT / "promo_build" / "shorts"))
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
TOKEN_URI = "https://oauth2.googleapis.com/token"


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def metadata_path():
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    raw = os.environ.get("SHORT_DATE", "").strip()
    if raw:
        try:
            date = dt.datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            die(f"SHORT_DATE must be YYYY-MM-DD (got {raw!r})")
    else:
        date = dt.datetime.now(dt.timezone.utc).date()
    return BUILD_ROOT / f"{date.strftime('%Y-%m-%d')}-short.json"


def main():
    meta_file = metadata_path()
    if not meta_file.exists():
        die(f"Metadata not found: {meta_file} — run scripts/make_short.py first.")
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    video = Path(meta["video"])
    if not video.exists():
        # The metadata stores an absolute path from build time; fall back to
        # the conventional location next to the metadata file.
        video = meta_file.with_suffix(".mp4")
    if not video.exists():
        die(f"Video file not found: {meta['video']}")

    needed = ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")
    missing = [n for n in needed if not os.environ.get(n)]
    if missing:
        print(f"SKIPPED: YouTube upload not attempted — missing secrets: "
              f"{', '.join(missing)}")
        print(f"  Upload manually to https://studio.youtube.com — the video is at:")
        print(f"    R2:    {meta.get('r2_url', '(not uploaded to R2)')}")
        print(f"    Local: {video}")
        print(f"  Title:       {meta['title']}")
        print(f"  Description:\n{meta['description']}")
        return

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri=TOKEN_URI,
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=[UPLOAD_SCOPE],
    )
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta.get("tags", []),
            "categoryId": meta.get("categoryId", "24"),
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
            # Altered-content (synthetic media) disclosure — the video is
            # AI-generated. Unknown fields are ignored by older API revisions.
            "containsSyntheticMedia": True,
        },
    }

    media = MediaFileUpload(str(video), mimetype="video/mp4",
                            chunksize=8 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body,
                                      media_body=media)

    print(f"Uploading {video} ({video.stat().st_size // 1024} KiB) to YouTube…")
    response, retries = None, 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"  {int(status.progress() * 100)}%")
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and retries < 5:
                retries += 1
                wait = 2 ** retries
                print(f"  transient HTTP {e.resp.status}; retrying in {wait}s")
                time.sleep(wait)
                continue
            if e.resp.status == 403 and b"uploadLimitExceeded" in (e.content or b""):
                die("YouTube upload limit exceeded for this channel today.")
            die(f"YouTube API error: {e}")

    vid = response["id"]
    print(f"Uploaded: https://youtu.be/{vid}")
    synthetic = (response.get("status") or {}).get("containsSyntheticMedia")
    if synthetic is True:
        print("Altered-content disclosure set (status.containsSyntheticMedia=true).")
    else:
        print("NOTE: the API response did not echo containsSyntheticMedia — this "
              "API revision may not support the synthetic-media disclosure field; "
              "verify/set the altered-content disclosure in YouTube Studio.")


if __name__ == "__main__":
    main()
