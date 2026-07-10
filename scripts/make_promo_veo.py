#!/usr/bin/env python3
"""Multiverse Gazette site ad — Veo 3.1 edition (via kie.ai).

A 30-60s 16:9 promo explaining the site's core idea, built from real
stories already published on the site:
  - 5 Veo 3.1 image-to-video clips (16:9, 8s each) from story hero images,
    using the story-driven strong-motion prompt system from make_short.py
  - cinematic trailer narration (OpenAI TTS) that name-drops 3 real
    headlines and lands on the URL
  - lower-third chyrons (universe · year + headline), brand logo badge +
    watermark, end card

Segment fitting: plain trim, or slow-mo capped at 1.6x plus a last-frame
hold — time never runs backwards (the old ping-pong read as a yin-yang loop).

Cost: 5 clips x ~$0.33 (veo3_fast) ~= $1.63 per build.

Required env: KIE_API_KEY, OPENAI_API_KEY, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY
Optional env: PROMO_DATE (YYYY-MM-DD, default today UTC), SHORT_SKIP_UPLOAD

Outputs promo_build/promo_veo/<date>-ad.mp4 (+ .json metadata) and uploads
to R2 at promo/test-site-ad-<date>.mp4 for review. Never touches YouTube.
"""

import datetime as dt
import json
import os
import shutil
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_short import (  # noqa: E402
    BASE_TAGS, CREAM, GOLD, KIE_BASE, LOGO_BADGE, REPO_ROOT, VEO_MODEL,
    VeoError, die, download_file, ffprobe_json, find_font, kie_headers,
    lead_timeline_for, load_editions, media_duration, month_day,
    narration_headline, openai_tts, require_env, run, runway_prompt_for,
    stage_upload, veo_wait_for_task, wrap_to_pixels, write_textfile,
)

BUILD_ROOT = Path(os.environ.get("PROMO_BUILD_DIR",
                                 REPO_ROOT / "promo_build" / "promo_veo"))
W, H, FPS = 1920, 1080, 30
FADE = 0.3
OUTRO = 4.5
NARR_START = 0.6
NARR_MAX = 50.0
TOTAL_HARD_MAX = 60.0
N_CLIPS = 5
MAX_SLOWMO = 1.6

TTS_INSTRUCTIONS = (
    "Cinematic movie-trailer narrator: warm, deep, unhurried, with a wry "
    "twinkle — selling a gloriously absurd premise completely straight. "
    "Slight pause before the final website mention."
)


def promo_date():
    raw = os.environ.get("PROMO_DATE", "").strip()
    if raw:
        try:
            return dt.datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            die(f"PROMO_DATE must be YYYY-MM-DD (got {raw!r})")
    return dt.datetime.now(dt.timezone.utc).date()


def pick_stories(date, editions, n=N_CLIPS):
    """Lead universe first, then the punchiest headlines, n total."""
    by_id = {e["timeline_id"]: e for e in editions}
    picks = []
    lead = lead_timeline_for(date)
    if lead in by_id:
        picks.append(by_id[lead])

    def punchiness(e):
        return len(e["headline"]) + 0.3 * len(e.get("divergence") or "")

    rest = sorted((e for e in editions
                   if e["timeline_id"] not in {p["timeline_id"] for p in picks}),
                  key=lambda e: (punchiness(e), e["timeline_id"]))
    picks.extend(rest)
    if len(picks) < n:
        die(f"Only {len(picks)} usable editions for {date} — need {n}.")
    return picks[:n]


def ad_script(stories):
    """~105-120 words -> ~40-47s of trailer narration. Name-drops the three
    punchiest stories; the other clips play as pure montage."""
    s = stories[:3]
    h = [narration_headline(x["headline"]) for x in s]
    u = [x["universe_name"] for x in s]
    y = [x["universe_year"] for x in s]
    return (
        "Somewhere out there, another Earth just went to press. "
        "This is the Multiverse Gazette — the daily newspaper of one hundred "
        "parallel universes. Each with its own history. Its own year. "
        "Its own front page. "
        f"In {u[0]}, year {y[0]}: {h[0]} "
        f"In {u[1]}: {h[1]} "
        f"And in {u[2]}: {h[2]} "
        "Eight brand-new editions every single day — absurd, satirical "
        "dispatches from worlds that never were, lovingly typeset by no one "
        "you can sue. Read every front page, free. "
        "The Multiverse Gazette — at thejumpuniverse dot com."
    )


# ─── Veo (16:9) ─────────────────────────────────────────────────────────

def veo_start_16x9(image_url, prompt):
    body = {
        "prompt": prompt,
        "imageUrls": [image_url],
        "model": VEO_MODEL,
        "aspect_ratio": "16:9",
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


def stage_clips(work_dir, stories):
    """One 8s 16:9 Veo clip per story; a failed clip is retried once, then
    that story degrades to a slow-zoom still so the ad always builds."""
    paths = [work_dir / f"adclip-{s['timeline_id']}.mp4" for s in stories]
    still_flags = [False] * len(stories)
    missing = [i for i, p in enumerate(paths) if not p.exists()]
    if not missing:
        print("Stage 1: all clips cached.")
        return paths, still_flags
    tasks, failed = {}, []
    for i in missing:
        s = stories[i]
        prompt = runway_prompt_for(s)
        print(f"Stage 1: Veo task ({VEO_MODEL}, 16:9) for {s['universe_name']}")
        print(f"  promptText ({len(prompt)} chars): {prompt}")
        try:
            tasks[i] = veo_start_16x9(s["hero_image"], prompt)
        except VeoError as e:
            print(f"  WARNING: task creation failed: {e}")
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
            print(f"  WARNING: clip {i + 1} failed: {e}")
            failed.append(i)
    for i in failed:
        s = stories[i]
        print(f"Stage 1: retrying clip {i + 1} ({s['universe_name']})")
        try:
            task_id = veo_start_16x9(s["hero_image"], runway_prompt_for(s))
            url = veo_wait_for_task(task_id)
            download_file(url, paths[i])
        except (VeoError, requests.RequestException) as e:
            print(f"  WARNING: retry also failed ({e}); slow-zoom still fallback.")
            hero = work_dir / f"hero-{s['timeline_id']}.img"
            if not hero.exists():
                download_file(s["hero_image"], hero)
            paths[i] = hero
            still_flags[i] = True
    return paths, still_flags


# ─── narration ──────────────────────────────────────────────────────────

def stage_narration(work_dir, text):
    import hashlib
    key = hashlib.md5(text.encode()).hexdigest()[:10]
    final = work_dir / f"ad-narration-{key}.mp3"
    raw = work_dir / f"ad-narration-{key}-raw.mp3"
    if final.exists():
        return final
    api_key = require_env("OPENAI_API_KEY", "TTS narration")
    if not raw.exists():
        model = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        print(f"Stage 2: narration with {model} (voice: onyx, trailer read)")
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        kwargs = dict(model=model, voice="onyx", input=text, response_format="mp3")
        if model == "gpt-4o-mini-tts":
            kwargs["instructions"] = TTS_INSTRUCTIONS
        resp = client.audio.speech.create(**kwargs)
        raw.write_bytes(resp.content)
    dur = media_duration(raw)
    print(f"Stage 2: raw narration {dur:.2f}s")
    if dur > NARR_MAX:
        tempo = min(1.2, dur / NARR_MAX)
        run(["ffmpeg", "-y", "-i", raw, "-filter:a", f"atempo={tempo:.4f}",
             "-c:a", "libmp3lame", "-q:a", "2", final])
    else:
        run(["ffmpeg", "-y", "-i", raw, "-c:a", "libmp3lame", "-q:a", "2", final])
    print(f"Stage 2: final narration {media_duration(final):.2f}s")
    return final


# ─── segments ───────────────────────────────────────────────────────────

NORM = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},fps={FPS}")


def chyron_16x9(work_dir, idx, story, bold, hide_after=None):
    head_size, kick_size = 46, 32
    lines = wrap_to_pixels(story["headline"], bold, head_size, 1300, max_lines=2)
    kicker = f"{story['universe_name'].upper()}  ·  YEAR {story['universe_year']}"
    kick_file = write_textfile(work_dir / f"adchy{idx}-k.txt", kicker)
    head_file = write_textfile(work_dir / f"adchy{idx}-h.txt", "\n".join(lines))
    line_h = head_size + 10
    box_h = 78 + len(lines) * line_h + 22
    box_y = 985 - box_h
    en = "" if hide_after is None else f":enable='lt(t,{max(0.0, hide_after):.3f})'"
    return (
        f"drawbox=x=64:y={box_y}:w=1480:h={box_h}:color=black@0.55:t=fill{en},"
        f"drawbox=x=64:y={box_y}:w=12:h={box_h}:color={GOLD}:t=fill{en},"
        f"drawtext=fontfile={bold}:textfile={kick_file}:fontsize={kick_size}:"
        f"fontcolor={GOLD}:x=112:y={box_y + 24}:expansion=none{en},"
        f"drawtext=fontfile={bold}:textfile={head_file}:fontsize={head_size}:"
        f"fontcolor=white:line_spacing=10:x=112:y={box_y + 74}:expansion=none{en}"
    )


def build_segment(src, is_still, seg_dur, chyron, out_path):
    if is_still:
        frames = int(round(seg_dur * FPS))
        chain = (f"[0:v]scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,"
                 f"crop={W * 2}:{H * 2},"
                 f"zoompan=z='1.05+0.15*(on/{max(frames - 1, 1)})'"
                 f":x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'"
                 f":d={frames}:s={W}x{H}:fps={FPS}")
        vf = f"{chain},format=yuv420p,settb=AVTB,{chyron}[v]"
        run(["ffmpeg", "-y", "-i", src, "-filter_complex", vf,
             "-map", "[v]", "-frames:v", frames, "-c:v", "libx264",
             "-preset", "medium", "-crf", "19", "-r", FPS,
             "-pix_fmt", "yuv420p", out_path])
        return
    # Time NEVER runs backwards: trim, or slow-mo (<= MAX_SLOWMO) plus a
    # last-frame hold for any remainder. No forward+reverse ping-pong.
    clip_dur = media_duration(src)
    if seg_dur <= clip_dur + 0.01:
        chain = f"[0:v]{NORM},trim=duration={seg_dur:.3f},setpts=PTS-STARTPTS"
    else:
        factor = min(seg_dur / clip_dur, MAX_SLOWMO)
        hold = max(0.0, seg_dur - clip_dur * factor)
        if hold > 0.05:
            print(f"  note: slow-mo {factor:.2f}x + {hold:.2f}s last-frame hold "
                  f"to fill {seg_dur:.2f}s from a {clip_dur:.2f}s clip")
        chain = (f"[0:v]{NORM},setpts={factor:.4f}*PTS,fps={FPS},"
                 f"tpad=stop_mode=clone:stop_duration={hold + 0.5:.3f},"
                 f"trim=duration={seg_dur:.3f},setpts=PTS-STARTPTS")
    vf = f"{chain},format=yuv420p,settb=AVTB,{chyron}[v]"
    run(["ffmpeg", "-y", "-i", src, "-filter_complex", vf,
         "-map", "[v]", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
         "-r", FPS, "-pix_fmt", "yuv420p", out_path])


def assemble(work_dir, date, segments, seg_dur, total, narration, out_path):
    bold = find_font(bold=True)
    reg = find_font(bold=False)
    brand = write_textfile(work_dir / "ad-brand.txt", "MULTIVERSE GAZETTE")
    wm = write_textfile(work_dir / "ad-wm.txt", "thejumpuniverse.com")
    o1 = write_textfile(work_dir / "ad-o1.txt", "NEW FRONT PAGES DAILY")
    o2 = write_textfile(work_dir / "ad-o2.txt", "thejumpuniverse.com")
    o3 = write_textfile(work_dir / "ad-o3.txt",
                        "8 EDITIONS  ·  100 UNIVERSES  ·  EVERY DAY")

    inputs = []
    for p in segments:
        inputs += ["-i", p]
    inputs += ["-i", narration]
    nar_idx = len(segments)
    logo_idx = None
    brand_x = 64
    if LOGO_BADGE.exists():
        inputs += ["-i", LOGO_BADGE]
        logo_idx = nar_idx + 1
        brand_x = 192

    f = []
    prev = "0:v"
    for k in range(1, len(segments)):
        off = k * seg_dur - k * FADE
        lbl = f"x{k}"
        f.append(f"[{prev}][{k}:v]xfade=transition=fade:duration={FADE}:"
                 f"offset={off:.3f}[{lbl}]")
        prev = lbl
    outro_st = total - OUTRO
    fade_expr = f"'if(lt(t,{outro_st:.3f}),0,min(1,(t-{outro_st:.3f})/0.7))'"
    overlays = (
        f"drawtext=fontfile={bold}:textfile={brand}:fontsize=34:"
        f"fontcolor=white@0.85:x={brand_x}:y=52:expansion=none,"
        f"drawtext=fontfile={reg}:textfile={wm}:fontsize=28:"
        f"fontcolor=white@0.7:x=w-text_w-64:y=52:expansion=none,"
        f"drawbox=x=0:y=0:w={W}:h={H}:color=black@0.55:t=fill:"
        f"enable='gte(t,{outro_st:.3f})',"
        f"drawtext=fontfile={bold}:textfile={o1}:fontsize=54:"
        f"fontcolor={GOLD}:x=(w-text_w)/2:y=420:alpha={fade_expr}:expansion=none,"
        f"drawtext=fontfile={bold}:textfile={o2}:fontsize=88:"
        f"fontcolor=white:x=(w-text_w)/2:y=500:alpha={fade_expr}:expansion=none,"
        f"drawtext=fontfile={reg}:textfile={o3}:fontsize=32:"
        f"fontcolor={CREAM}:x=(w-text_w)/2:y=640:alpha={fade_expr}:expansion=none"
    )
    if logo_idx is not None:
        f.append(f"[{prev}]{overlays}[vpre]")
        f.append(f"[{logo_idx}:v]scale=112:112[lg]")
        f.append(f"[vpre][lg]overlay=56:24[vout]")
    else:
        f.append(f"[{prev}]{overlays}[vout]")
    delay_ms = int(NARR_START * 1000)
    f.append(
        f"[{nar_idx}:a]adelay={delay_ms}:all=1,apad,atrim=0:{total:.3f},"
        f"loudnorm=I=-15:TP=-1.5:LRA=11,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo,aresample=48000,"
        f"afade=t=out:st={max(0.0, total - 1.0):.3f}:d=1.0[aout]")

    run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(f),
         "-map", "[vout]", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "19",
         "-profile:v", "high", "-r", FPS, "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", "-t", f"{total:.3f}", out_path])
    print(f"Assembled {out_path} ({media_duration(out_path):.2f}s)")


def main():
    date = promo_date()
    slug = date.strftime("%Y-%m-%d")
    work_dir = BUILD_ROOT / slug
    work_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = BUILD_ROOT / f"{slug}-ad.mp4"
    out_meta = BUILD_ROOT / f"{slug}-ad.json"
    r2_key = f"promo/test-site-ad-{slug}.mp4"

    editions = load_editions(slug)
    if not editions:
        die(f"No editions for {slug} (set PROMO_DATE to a date that has some)")
    stories = pick_stories(date, editions)
    print(f"Site ad (Veo 3.1 {VEO_MODEL}, 16:9) for {slug}:")
    for i, s in enumerate(stories, 1):
        print(f"  {i}. [{s['timeline_id']}] {s['universe_name']}: {s['headline']}")
    print(f"  est. Veo cost: {N_CLIPS} clips x ~$0.33 = ~${N_CLIPS * 0.325:.2f}")

    sources, still_flags = stage_clips(work_dir, stories)
    script = ad_script(stories)
    print(f"Narration ({len(script)} chars): {script}")
    narration = stage_narration(work_dir, script)
    ndur = media_duration(narration)

    total = NARR_START + ndur + OUTRO
    if total > TOTAL_HARD_MAX:
        die(f"total {total:.1f}s exceeds {TOTAL_HARD_MAX:.0f}s — trim the script")
    if total < 30.0:
        total = 30.0
    seg_dur = (total + (N_CLIPS - 1) * FADE) / N_CLIPS
    print(f"Timeline: narration {ndur:.2f}s -> total {total:.2f}s, "
          f"{N_CLIPS} segments of {seg_dur:.2f}s")

    bold = find_font(bold=True)
    segments = []
    for i, (story, src, still) in enumerate(zip(stories, sources, still_flags)):
        out = work_dir / f"adseg{i + 1}-{story['timeline_id']}-{int(seg_dur * 1000)}.mp4"
        segments.append(out)
        if out.exists():
            print(f"segment {i + 1} cached")
            continue
        print(f"Stage 3: segment {i + 1} ({story['universe_name']}, "
              f"{seg_dur:.2f}s, {'still' if still else 'Veo'})")
        hide = (seg_dur - OUTRO) if i == N_CLIPS - 1 else None
        chyron = chyron_16x9(work_dir, i + 1, story, bold, hide)
        build_segment(src, still, seg_dur, chyron, out)

    final = work_dir / "site-ad.mp4"
    assemble(work_dir, date, segments, seg_dur, total, narration, final)
    shutil.copyfile(final, out_mp4)

    info = ffprobe_json(out_mp4)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    print(f"Final: {v['width']}x{v['height']} "
          f"{float(info['format']['duration']):.2f}s "
          f"{int(info['format']['size']) // 1024} KiB")

    url = stage_upload(out_mp4, r2_key)
    meta = {
        "date": slug,
        "title": "One Newspaper. 100 Universes. | Multiverse Gazette",
        "description": (
            "Every day, one hundred parallel universes go to press — and the "
            "Multiverse Gazette prints their front pages. Eight new editions "
            "daily: satirical news from worlds that never were.\n\n"
            "Read every front page, free: https://thejumpuniverse.com\n\n"
            "#multiverse #alternatehistory #satire #scifi #galacticnews"
        ),
        "tags": [t for t in BASE_TAGS if t != "shorts"],
        "categoryId": "24",
        "video": str(out_mp4),
        "r2_key": r2_key,
        "r2_url": url or "",
        "stories": [{"timeline_id": s["timeline_id"],
                     "universe_name": s["universe_name"],
                     "headline": s["headline"]} for s in stories],
        "variant": f"site-ad-veo3.1-{VEO_MODEL}",
    }
    out_meta.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print("\nDone.")
    if url:
        print(f"  Review URL: {url}")


if __name__ == "__main__":
    main()
