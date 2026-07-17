#!/usr/bin/env python3
"""Collect per-video stats for the Multiverse Gazette YouTube channel.

Uses the public channel RSS feed (no API key needed): views, likes,
title, publish date, thumbnail. Merges with the previous snapshot so
videos that fall out of the 15-entry feed window are retained with
their last-known numbers.

Writes data/youtube-stats.json.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

CHANNEL_ID = "UCyPIYczypKJ6dIuoHhv_9ow"
FEED_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "youtube-stats.json")

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


class FeedUnavailable(Exception):
    """The RSS feed could not be fetched after retries."""


def fetch_feed() -> bytes:
    """Fetch the channel RSS feed, retrying with backoff.

    YouTube intermittently serves 404/5xx for the feed (observed daily on
    the run right after the nightly Short upload). Retry a few times; if
    it never recovers, raise FeedUnavailable so the caller can skip this
    cycle instead of failing the workflow.
    """
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": "Mozilla/5.0 (gazette-stats)"})
    last_err: Exception | None = None
    for attempt, delay in enumerate((0, 20, 60, 120), start=1):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last_err = e
            print(f"Feed fetch attempt {attempt} failed: {e}")
    raise FeedUnavailable(str(last_err))


def parse_entries(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    entries = []
    for e in root.findall("atom:entry", NS):
        vid = e.findtext("yt:videoId", default="", namespaces=NS)
        if not vid:
            continue
        group = e.find("media:group", NS)
        views = likes = 0
        rating = None
        thumb = ""
        if group is not None:
            comm = group.find("media:community", NS)
            if comm is not None:
                stats = comm.find("media:statistics", NS)
                if stats is not None:
                    views = int(stats.get("views", "0") or 0)
                star = comm.find("media:starRating", NS)
                if star is not None:
                    likes = int(star.get("count", "0") or 0)
                    rating = float(star.get("average", "0") or 0)
            t = group.find("media:thumbnail", NS)
            if t is not None:
                thumb = t.get("url", "")
        entries.append({
            "id": vid,
            "title": e.findtext("atom:title", default="", namespaces=NS),
            "url": f"https://www.youtube.com/watch?v={vid}",
            "published": e.findtext("atom:published", default="", namespaces=NS),
            "thumbnail": thumb,
            "views": views,
            "likes": likes,
            "rating": rating,
            "is_short": "short" in (e.findtext("atom:title", default="", namespaces=NS) or "").lower()
                        or "bulletin" in (e.findtext("atom:title", default="", namespaces=NS) or "").lower(),
        })
    return entries


def main() -> None:
    try:
        fresh = parse_entries(fetch_feed())
    except FeedUnavailable as e:
        # Transient upstream problem; the next scheduled run will catch up.
        print(f"Feed unavailable after retries ({e}); skipping this cycle.")
        sys.exit(0)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    old_by_id = {}
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                for v in json.load(f).get("videos", []):
                    old_by_id[v["id"]] = v
        except (json.JSONDecodeError, KeyError):
            pass

    fresh_ids = {v["id"] for v in fresh}
    merged = list(fresh)
    for vid, v in old_by_id.items():
        if vid not in fresh_ids:
            v["stale"] = True  # fell out of the 15-entry feed window
            merged.append(v)

    # keep per-video history of (timestamp, views) so the dashboard can chart growth
    for v in merged:
        hist = old_by_id.get(v["id"], {}).get("history", [])
        if not v.get("stale"):
            if not hist or hist[-1][1] != v["views"]:
                hist = hist + [[now, v["views"]]]
            hist = hist[-60:]  # cap
        v["history"] = hist

    merged.sort(key=lambda v: v.get("published", ""), reverse=True)
    out = {
        "generated_at": now,
        "channel_id": CHANNEL_ID,
        "channel_url": f"https://www.youtube.com/channel/{CHANNEL_ID}",
        "totals": {
            "videos": len(merged),
            "views": sum(v["views"] for v in merged),
            "likes": sum(v["likes"] for v in merged),
        },
        "videos": merged,
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"Wrote {OUT_PATH}: {out['totals']}")


if __name__ == "__main__":
    main()
