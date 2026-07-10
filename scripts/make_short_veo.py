#!/usr/bin/env python3
"""Multiverse Gazette Short — Veo 3.1 variant (via kie.ai).

Same news-bulletin format as make_short.py, but the per-story clips are
generated with Google Veo 3.1 (image-to-video, native 9:16) through the
kie.ai API instead of Runway. Veo clips are fixed 8s; segments (~9.5s)
are fitted with a gentle slow-mo stretch rather than a ping-pong loop.

Cost (kie.ai): veo3_fast ~= $0.325/clip 1080p -> ~$1/day for 3 clips.
Set VEO_MODEL=veo3 for the Quality tier (~$1.275/clip).

Required env: KIE_API_KEY, OPENAI_API_KEY, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY
Optional env: SHORT_DATE, SHORT_TIMELINES, SHORT_SKIP_UPLOAD, VEO_MODEL

Outputs promo_build/shorts_veo/YYYY-MM-DD-short.mp4 (+ .json) and uploads
to R2 at shorts/test-YYYY-MM-DD-veo.mp4 for review. Never touches YouTube.
"""

import json
import os
import random
import shutil
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_short import (  # noqa: E402
    FPS, NARRATION_START, OUTRO_SECONDS, REPO_ROOT, TOTAL_HARD_MAX, TOTAL_MIN,
    FADE_SECONDS, NORM, assemble, build_metadata, build_segment_kenburns,
    chyron_filters, die, ffprobe_json, find_font, load_editions,
    media_duration, narration_script, require_env, run, runway_prompt_for,
    select_stories, short_date, stage_hero_images, stage_narration,
    stage_upload,
)

BUILD_ROOT = Path(os.environ.get("SHORT_BUILD_DIR",
                                 REPO_ROOT / "promo_build" / "shorts_veo"))

KIE_BASE = "https://api.kie.ai/api/v1/veo"
VEO_MODEL = os.environ.get("VEO_MODEL", "veo3_fast")
VEO_POLL_INTERVAL = 12
VEO_TIMEOUT = 20 * 60

_KEY_LOGGED = False


class VeoError(Exception):
    pass


def kie_key():
    """The raw secret often arrives with a stray newline/space from
    copy-paste — strip it, and log a non-sensitive fingerprint once so
    auth failures are diagnosable from CI logs."""
    global _KEY_LOGGED
    raw = require_env("KIE_API_KEY", "kie.ai Veo generation")
    key = raw.strip()
    if not _KEY_LOGGED:
        stripped = " (whitespace stripped!)" if key != raw else ""
        print(f"  kie.ai key: length {len(key)}, "
              f"starts '{key[:4]}…', ends '…{key[-4:]}'{stripped}")
        _KEY_LOGGED = True
    return key


def kie_headers():
    return {"Authorization": f"Bearer {kie_key()}",
            "Content-Type": "application/json"}


def veo_start_task(image_url, prompt):
    body = {
        "prompt": prompt,
        "imageUrls": [image_url],
        "model": VEO_MODEL,
        "aspect_ratio": "9:16",
        "generationType": "FIRST_AND_LAST_FRAMES_2_VIDEO",
        "enableFallback": False,
        "enableTranslation": False,
    }
    last = None
    for attempt in range(4):
        try:
            resp = requests.post(f"{KIE_BASE}/generate", headers=kie_headers(),
                                 json=body, timeout=60)
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if resp.status_code == 200 and data.get("code") == 200:
                return data["data"]["taskId"]
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"HTTP {resp.status_code}: {resp.text[:300]}"
                time.sleep(2 ** attempt * 5)
                continue
            raise VeoError(f"kie.ai rejected generate (HTTP {resp.status_code}): "
                           f"{resp.text[:1000]}")
        except requests.RequestException as e:
            last = str(e)
            time.sleep(2 ** attempt * 5)
    raise VeoError(f"kie.ai generate failed after retries: {last}")


def veo_wait_for_task(task_id):
    deadline = time.time() + VEO_TIMEOUT
    while time.time() < deadline:
        time.sleep(VEO_POLL_INTERVAL + random.uniform(0, 3))
        try:
            resp = requests.get(f"{KIE_BASE}/record-info",
                                params={"taskId": task_id},
                                headers=kie_headers(), timeout=60)
        except requests.RequestException as e:
            print(f"  poll error ({e}); will retry")
            continue
        if resp.status_code != 200:
            print(f"  poll HTTP {resp.status_code}; will retry")
            continue
        data = (resp.json() or {}).get("data") or {}
        flag = data.get("successFlag")
        if flag == 1:
            r = data.get("response") or {}
            urls = r.get("resultUrls") or r.get("originUrls") or []
            if not urls:
                raise VeoError(f"task {task_id} succeeded but returned no URLs")
            return urls[0]
        if flag in (2, 3):
            raise VeoError(f"task {task_id} failed: "
                           f"{data.get('errorMessage') or data.get('errorCode')}")
        print(f"  task {task_id}: generating…")
    raise VeoError(f"task {task_id} did not finish within {VEO_TIMEOUT}s")


def download_file(url, dest):
    tmp = Path(str(dest) + ".part")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    tmp.rename(dest)


def stage_veo_clips(work_dir, stories):
    """One 8s vertical Veo clip per story. Same resilience contract as the
    Runway stage: one retry per failed clip, then Ken Burns for that story."""
    paths = [work_dir / f"veoclip-{s['timeline_id']}.mp4" for s in stories]
    kb_flags = [False] * len(stories)
    missing = [i for i, p in enumerate(paths) if not p.exists()]
    if not missing:
        print("Stage 1: all Veo clips cached; skipping.")
        return paths, kb_flags
    tasks, failed = {}, []
    for i in missing:
        s = stories[i]
        prompt = runway_prompt_for(s)  # same story-driven scene prompt
        print(f"Stage 1: starting Veo task ({VEO_MODEL}) for {s['universe_name']} "
              f"(timeline {s['timeline_id']})")
        print(f"  promptText ({len(prompt)} chars): {prompt}")
        try:
            tasks[i] = veo_start_task(s["hero_image"], prompt)
        except VeoError as e:
            print(f"  WARNING: Veo task creation for clip {i + 1} failed: {e}")
            failed.append(i)
        time.sleep(1)
    for i in missing:
        if i in failed:
            continue
        print(f"Stage 1: waiting for clip {i + 1} (task {tasks[i]})")
        try:
            url = veo_wait_for_task(tasks[i])
            download_file(url, paths[i])
            print(f"  clip {i + 1} -> {paths[i]} ({paths[i].stat().st_size // 1024} KiB)")
        except (VeoError, requests.RequestException) as e:
            print(f"  WARNING: Veo clip {i + 1} failed: {e}")
            failed.append(i)
    for i in failed:
        s = stories[i]
        print(f"Stage 1: retrying Veo clip {i + 1} ({s['universe_name']})")
        try:
            task_id = veo_start_task(s["hero_image"], runway_prompt_for(s))
            url = veo_wait_for_task(task_id)
            download_file(url, paths[i])
            print(f"  clip {i + 1} -> {paths[i]}")
        except (VeoError, requests.RequestException) as e:
            print(f"  WARNING: Veo retry for clip {i + 1} also failed: {e}")
            print(f"  WARNING: Ken Burns fallback for story {i + 1} only.")
            paths[i] = stage_hero_images(work_dir, [s])[0]
            kb_flags[i] = True
    return paths, kb_flags


def build_segment_veo(clip_path, seg_dur, chyron, out_path):
    """Fit an 8s Veo clip to the segment: plain trim if it's long enough,
    gentle slow-mo stretch (up to 1.35x) otherwise — no reverse loops.
    Veo audio track is dropped (we only map the video chain)."""
    clip_dur = media_duration(clip_path)
    if seg_dur <= clip_dur + 0.01:
        chain = f"[0:v]{NORM},trim=duration={seg_dur:.3f},setpts=PTS-STARTPTS"
    elif seg_dur <= clip_dur * 1.35:
        factor = seg_dur / clip_dur
        chain = (f"[0:v]{NORM},setpts={factor:.4f}*PTS,fps={FPS},"
                 f"trim=duration={seg_dur:.3f},setpts=PTS-STARTPTS")
    else:
        chain = (f"[0:v]{NORM},split[fw][bw];[bw]reverse[rv];"
                 f"[fw][rv]concat=n=2:v=1:a=0,"
                 f"trim=duration={seg_dur:.3f},setpts=PTS-STARTPTS")
    vf = f"{chain},format=yuv420p,settb=AVTB,{chyron}[v]"
    run(["ffmpeg", "-y", "-i", clip_path, "-filter_complex", vf,
         "-map", "[v]", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
         "-r", FPS, "-pix_fmt", "yuv420p", out_path])


def main():
    date = short_date()
    slug = date.strftime("%Y-%m-%d")
    work_dir = BUILD_ROOT / slug
    work_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = BUILD_ROOT / f"{slug}-short.mp4"
    out_meta = BUILD_ROOT / f"{slug}-short.json"
    r2_key = f"shorts/test-{slug}-veo.mp4"

    editions = load_editions(slug)
    if not editions:
        die(f"No editions for {slug}")
    stories = select_stories(date, editions)
    print(f"Short (Veo 3.1 {VEO_MODEL} via kie.ai) for {slug}:")
    for i, s in enumerate(stories, 1):
        print(f"  {i}. [{s['timeline_id']}] {s['universe_name']}: {s['headline']}")

    sources, kb_flags = stage_veo_clips(work_dir, stories)
    if any(kb_flags):
        print(f"  NOTE: Ken Burns fallback for {sum(kb_flags)}/3 segment(s).")

    script = narration_script(date, stories)
    print(f"Narration ({len(script)} chars): {script}")
    narration = stage_narration(work_dir, script)
    ndur = media_duration(narration)

    total = max(TOTAL_MIN, NARRATION_START + ndur + OUTRO_SECONDS)
    if total > TOTAL_HARD_MAX:
        die(f"total {total:.1f}s exceeds hard cap")
    seg_dur = (total + 2 * FADE_SECONDS) / 3
    print(f"Timeline: narration {ndur:.2f}s -> total {total:.2f}s, "
          f"3 segments of {seg_dur:.2f}s")

    bold = find_font(bold=True)
    segments = []
    for i, (story, src, kb) in enumerate(zip(stories, sources, kb_flags)):
        mode = "kb" if kb else "veo"
        out = work_dir / f"seg{i + 1}-{story['timeline_id']}-{mode}-{int(seg_dur * 1000)}.mp4"
        segments.append(out)
        if out.exists():
            print(f"Stage 3: segment {i + 1} cached; skipping.")
            continue
        print(f"Stage 3: rendering segment {i + 1} ({story['universe_name']}, "
              f"{seg_dur:.2f}s, {'Ken Burns' if kb else 'Veo'})")
        hide = (seg_dur - OUTRO_SECONDS) if i == 2 else None
        chyron = chyron_filters(work_dir, i + 1, story, bold, hide)
        if kb:
            build_segment_kenburns(src, seg_dur, i, chyron, out,
                                   seed=f"{slug}-{story['timeline_id']}")
        else:
            build_segment_veo(src, seg_dur, chyron, out)

    final = work_dir / "short-veo.mp4"
    assemble(work_dir, date, segments, seg_dur, total, narration, final)
    shutil.copyfile(final, out_mp4)

    info = ffprobe_json(out_mp4)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    print(f"Final: {v['width']}x{v['height']} "
          f"{float(info['format']['duration']):.2f}s "
          f"{int(info['format']['size']) // 1024} KiB")

    url = stage_upload(out_mp4, r2_key)
    meta = build_metadata(date, stories, r2_key, out_mp4)
    meta["variant"] = f"veo3.1-{VEO_MODEL}-kie"
    out_meta.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print("\nDone.")
    if url:
        print(f"  Review URL: {url}")


if __name__ == "__main__":
    main()
