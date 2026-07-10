#!/usr/bin/env python3
"""Multiverse Gazette Short v2 — designed motion graphics, no AI video.

Replaces the uncanny image-to-video clips with a clean, deliberate look:
  - blurred slow-zoom backdrop + sharp drifting "photo card" of the hero image
  - kinetic typography: gold bar wipes in, kicker + headline lines slide in
    exactly when the anchor starts reading that story
  - segment cuts are synced to the narration via whisper word timestamps
    (segment N starts when the anchor says "In <universe N>:")
  - film grain + news-style slide transitions

Reuses story selection, narration, fonts and helpers from make_short.py.

Outputs promo_build/shorts_v2/YYYY-MM-DD-short.mp4 (+ .json metadata) and
uploads to R2 at shorts/test-YYYY-MM-DD-v2.mp4 for review (SHORT_SKIP_UPLOAD=1
skips). Never touches YouTube.
"""

import json
import math
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_short import (  # noqa: E402
    EDITIONS_DIR, FPS, GOLD, CREAM, HEIGHT, NARRATION_START, OUTRO_SECONDS,
    REPO_ROOT, TOTAL_HARD_MAX, WIDTH, build_metadata, die, find_font,
    load_editions, media_duration, month_day, narration_script, require_env,
    run, select_stories, short_date, stage_hero_images, stage_narration,
    stage_upload, wrap_to_pixels, write_textfile, ffprobe_json,
)

BUILD_ROOT = Path(os.environ.get("SHORT_BUILD_DIR",
                                 REPO_ROOT / "promo_build" / "shorts_v2"))
FADE = 0.25                 # news-style slide transition
MIN_SEG = 4.0               # sanity floor; below this fall back to equal thirds
CARD_W = 940                # sharp foreground card width
TEXT_X = 64
GOLD_RGB = (232, 184, 75)
CREAM_RGB = (216, 208, 192)


# ─── whisper word timing ────────────────────────────────────────────────

def transcribe_words(narration_path):
    """OpenAI whisper word timestamps for the final narration mp3."""
    from openai import OpenAI
    client = OpenAI(api_key=require_env("OPENAI_API_KEY", "whisper alignment"))
    with open(narration_path, "rb") as f:
        resp = client.audio.transcriptions.create(
            model="whisper-1", file=f, response_format="verbose_json",
            timestamp_granularities=["word"])
    words = getattr(resp, "words", None) or resp.model_dump().get("words") or []
    out = []
    for w in words:
        if not isinstance(w, dict):
            w = w.model_dump() if hasattr(w, "model_dump") else dict(w)
        out.append({"word": w.get("word", ""), "start": float(w.get("start", 0.0))})
    return out


def _clean(w):
    return re.sub(r"[^a-z0-9']", "", (w or "").lower())


def story_start_times(words, stories, ndur):
    """Narration-local start time for stories 2 and 3: the 'In'/'And in'
    directly before the universe name. Falls back to equal thirds."""
    fallback = [0.0, ndur / 3.0, 2.0 * ndur / 3.0]
    if not words:
        print("  whisper: no words returned; using equal thirds")
        return fallback
    starts = [0.0]
    idx = 0
    for s in stories[1:]:
        target = _clean(max(s["universe_name"].split(), key=len))
        found = None
        for i in range(idx, len(words)):
            if _clean(words[i]["word"]) == target:
                found = i
                break
        if found is None:
            print(f"  whisper: could not locate {s['universe_name']!r}; "
                  f"using equal thirds")
            return fallback
        j = found
        for back in (1, 2, 3):
            k = found - back
            if k >= 0 and _clean(words[k]["word"]) in ("in", "and"):
                j = k
            else:
                break
        starts.append(max(0.0, words[j]["start"] - 0.08))
        idx = found + 1
    return starts


# ─── PIL text sprites ───────────────────────────────────────────────────

def _load_pil():
    from PIL import Image, ImageDraw, ImageFont
    return Image, ImageDraw, ImageFont


def text_sprite(path, text, font_path, size, fill, box_rgba=(0, 0, 0, 150),
                pad_x=26, pad_y=14):
    """One line of text on a translucent box, saved as an RGBA PNG sprite."""
    Image, ImageDraw, ImageFont = _load_pil()
    font = ImageFont.truetype(font_path, size)
    bbox = font.getbbox(text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    img = Image.new("RGBA", (tw + 2 * pad_x, th + 2 * pad_y), box_rgba)
    d = ImageDraw.Draw(img)
    d.text((pad_x - bbox[0], pad_y - bbox[1]), text, font=font, fill=fill)
    img.save(path)
    return path, img.size


def bar_sprite(path, w, h, rgb):
    Image, _, _ = _load_pil()
    Image.new("RGBA", (w, h), rgb + (255,)).save(path)
    return path


# ─── segment render ─────────────────────────────────────────────────────

def build_segment(seg_idx, story, hero, seg_dur, work_dir, out_path,
                  hide_text_after=None):
    """Backdrop (blurred slow zoom) + drifting sharp card + staggered
    slide-in typography. All motion is deterministic ffmpeg math — smooth
    by construction."""
    bold = find_font(bold=True)
    frames = int(round(seg_dur * FPS))

    # text sprites --------------------------------------------------------
    kicker = f"{story['universe_name'].upper()}  ·  YEAR {story['universe_year']}"
    k_path, (kw, kh) = text_sprite(
        work_dir / f"s{seg_idx}-kick.png", kicker, bold, 34,
        GOLD_RGB + (255,), box_rgba=(0, 0, 0, 165))
    lines = wrap_to_pixels(story["headline"], bold, 54, WIDTH - 2 * TEXT_X - 40,
                           max_lines=3)
    line_sprites = []
    for li, line in enumerate(lines):
        p, (lw, lh) = text_sprite(
            work_dir / f"s{seg_idx}-l{li}.png", line, bold, 54,
            (255, 255, 255, 255), box_rgba=(0, 0, 0, 165))
        line_sprites.append((p, lw, lh))
    line_h = line_sprites[0][2] + 10
    block_h = kh + 14 + len(line_sprites) * line_h
    block_top = 1500 - block_h
    b_path = bar_sprite(work_dir / f"s{seg_idx}-bar.png", 14, block_h + 12,
                        GOLD_RGB)

    # timing (segment-local): bar 0.10s, kicker 0.30s, lines stagger 0.24s --
    reveals = [0.10, 0.30] + [0.55 + 0.24 * i for i in range(len(line_sprites))]
    slide = 1600.0  # px/s slide-in speed

    def slide_x(final_x, t0):
        start_x = -(WIDTH + 200)
        return (f"if(lt(t,{t0:.3f}),{start_x},"
                f"min({final_x},{start_x}+(t-{t0:.3f})*{slide:.0f}))")

    en = ""
    if hide_text_after is not None:
        en = f":enable='lt(t,{max(0.0, hide_text_after):.3f})'"

    # inputs: 0 hero (backdrop), 1 hero (card), 2 bar, 3 kicker, 4.. lines --
    inputs = ["-loop", "1", "-t", f"{seg_dur:.3f}", "-i", hero,
              "-loop", "1", "-t", f"{seg_dur:.3f}", "-i", hero,
              "-loop", "1", "-t", f"{seg_dur:.3f}", "-i", b_path,
              "-loop", "1", "-t", f"{seg_dur:.3f}", "-i", k_path]
    for p, _, _ in line_sprites:
        inputs += ["-loop", "1", "-t", f"{seg_dur:.3f}", "-i", str(p)]

    f = []
    f.append(
        f"[0:v]scale={WIDTH * 2}:{HEIGHT * 2}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH * 2}:{HEIGHT * 2},"
        f"zoompan=z='1.02+0.12*(on/{max(frames - 1, 1)})'"
        f":x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'"
        f":d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},"
        f"boxblur=22:2,eq=brightness=-0.16:saturation=0.8[bg]")
    # sharp card: white hairline border, gentle two-axis drift
    f.append(
        f"[1:v]scale={CARD_W}:-2,"
        f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.92:t=5,fps={FPS}[card]")
    card_x = f"(W-w)/2+9*sin(2*PI*t/8.3)"
    card_y = f"236+7*sin(2*PI*t/5.9)"
    f.append(f"[bg][card]overlay=x='{card_x}':y='{card_y}':shortest=1[v0]")
    f.append(f"[v0][2:v]overlay=x='{slide_x(TEXT_X - 22, reveals[0])}'"
             f":y={block_top - 6}:shortest=1{en}[v1]")
    f.append(f"[v1][3:v]overlay=x='{slide_x(TEXT_X, reveals[1])}'"
             f":y={block_top}:shortest=1{en}[v2]")
    prev = "v2"
    for li in range(len(line_sprites)):
        y = block_top + kh + 14 + li * line_h
        f.append(f"[{prev}][{4 + li}:v]overlay="
                 f"x='{slide_x(TEXT_X, reveals[2 + li])}':y={y}"
                 f":shortest=1{en}[v{3 + li}]")
        prev = f"v{3 + li}"
    f.append(f"[{prev}]format=yuv420p,settb=AVTB[v]")

    run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(f),
         "-map", "[v]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-r", FPS, "-t", f"{seg_dur:.3f}", "-pix_fmt", "yuv420p", out_path])


# ─── assembly ───────────────────────────────────────────────────────────

def assemble(work_dir, date, segments, durs, total, narration, out_path):
    bold = find_font(bold=True)
    reg = find_font(bold=False)
    banner = write_textfile(work_dir / "banner.txt",
                            "MULTIVERSE GAZETTE — DAILY BRIEFING")
    bdate = write_textfile(work_dir / "banner-date.txt",
                           date.strftime("%A, %B %d, %Y").upper())
    wm = write_textfile(work_dir / "watermark.txt", "thejumpuniverse.com")
    o1f = write_textfile(work_dir / "outro1.txt", "NEW FRONT PAGES DAILY")
    o2f = write_textfile(work_dir / "outro2.txt", "thejumpuniverse.com")

    inputs = []
    for p in segments:
        inputs += ["-i", p]
    inputs += ["-i", narration]
    nar_idx = len(segments)

    f = []
    o1 = durs[0] - FADE
    o2 = durs[0] + durs[1] - 2 * FADE
    f.append(f"[0:v][1:v]xfade=transition=slideleft:duration={FADE}:offset={o1:.3f}[x1]")
    f.append(f"[x1][2:v]xfade=transition=slideleft:duration={FADE}:offset={o2:.3f}[bd]")

    outro_st = total - OUTRO_SECONDS
    fade_expr = f"'if(lt(t,{outro_st:.3f}),0,min(1,(t-{outro_st:.3f})/0.6))'"
    f.append(
        f"[bd]noise=alls=5:allf=t,"
        f"drawbox=x=0:y=96:w={WIDTH}:h=118:color=black@0.55:t=fill,"
        f"drawtext=fontfile={bold}:textfile={banner}:fontsize=38:"
        f"fontcolor=white:x=(w-text_w)/2:y=118:expansion=none,"
        f"drawtext=fontfile={reg}:textfile={bdate}:fontsize=26:"
        f"fontcolor={CREAM}:x=(w-text_w)/2:y=168:expansion=none,"
        f"drawtext=fontfile={bold}:textfile={wm}:fontsize=34:"
        f"fontcolor=white@0.75:x=(w-text_w)/2:y=1806:expansion=none,"
        f"drawbox=x=0:y=0:w={WIDTH}:h={HEIGHT}:color=black@0.5:t=fill:"
        f"enable='gte(t,{outro_st:.3f})',"
        f"drawtext=fontfile={bold}:textfile={o1f}:fontsize=54:"
        f"fontcolor={GOLD}:x=(w-text_w)/2:y=830:alpha={fade_expr}:expansion=none,"
        f"drawtext=fontfile={bold}:textfile={o2f}:fontsize=76:"
        f"fontcolor=white:x=(w-text_w)/2:y=930:alpha={fade_expr}:expansion=none"
        f"[vout]")
    delay_ms = int(NARRATION_START * 1000)
    f.append(
        f"[{nar_idx}:a]adelay={delay_ms}:all=1,apad,atrim=0:{total:.3f},"
        f"loudnorm=I=-16:TP=-1.5:LRA=11,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo,aresample=48000,"
        f"afade=t=out:st={max(0.0, total - 0.8):.3f}:d=0.8[aout]")

    run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(f),
         "-map", "[vout]", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-profile:v", "high", "-r", FPS, "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", "-t", f"{total:.3f}", out_path])
    print(f"Assembled {out_path} ({media_duration(out_path):.2f}s, "
          f"target {total:.2f}s)")


# ─── main ───────────────────────────────────────────────────────────────

def main():
    date = short_date()
    slug = date.strftime("%Y-%m-%d")
    work_dir = BUILD_ROOT / slug
    work_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = BUILD_ROOT / f"{slug}-short.mp4"
    out_meta = BUILD_ROOT / f"{slug}-short.json"
    r2_key = f"shorts/test-{slug}-v2.mp4"

    editions = load_editions(slug)
    if not editions:
        die(f"No editions for {slug}")
    stories = select_stories(date, editions)
    print(f"Short v2 (motion graphics) for {slug}:")
    for i, s in enumerate(stories, 1):
        print(f"  {i}. [{s['timeline_id']}] {s['universe_name']}: {s['headline']}")

    heroes = stage_hero_images(work_dir, stories)
    script = narration_script(date, stories)
    print(f"Narration ({len(script)} chars): {script}")
    narration = stage_narration(work_dir, script)
    ndur = media_duration(narration)

    print("Aligning narration with whisper word timestamps…")
    words = transcribe_words(narration)
    starts = story_start_times(words, stories, ndur)
    print(f"  story starts (narration-local): "
          f"{', '.join(f'{t:.2f}s' for t in starts)}")

    total = NARRATION_START + ndur + OUTRO_SECONDS
    if total > TOTAL_HARD_MAX:
        die(f"total {total:.1f}s exceeds hard cap")
    vstarts = [0.0,
               NARRATION_START + starts[1],
               NARRATION_START + starts[2]]
    durs = [vstarts[1] - vstarts[0] + FADE,
            vstarts[2] - vstarts[1] + FADE,
            total - vstarts[2] + FADE]
    if min(durs) < MIN_SEG:
        print("  a segment came out too short; using equal thirds")
        d = (total + 2 * FADE) / 3
        durs = [d, d, d]
    print(f"Timeline: narration {ndur:.2f}s, total {total:.2f}s, segments "
          f"{', '.join(f'{d:.2f}s' for d in durs)} (slide {FADE}s)")

    segs = []
    for i, (story, hero, d) in enumerate(zip(stories, heroes, durs)):
        out = work_dir / f"v2seg{i + 1}-{story['timeline_id']}-{int(d * 1000)}.mp4"
        segs.append(out)
        if out.exists():
            print(f"segment {i + 1} cached")
            continue
        print(f"Rendering segment {i + 1} ({story['universe_name']}, {d:.2f}s)")
        hide = (d - OUTRO_SECONDS) if i == 2 else None
        build_segment(i + 1, story, hero, d, work_dir, out, hide_text_after=hide)

    final = work_dir / "short-v2.mp4"
    assemble(work_dir, date, segs, durs, total, narration, final)
    import shutil
    shutil.copyfile(final, out_mp4)

    info = ffprobe_json(out_mp4)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    print(f"Final: {v['width']}x{v['height']} "
          f"{float(info['format']['duration']):.2f}s "
          f"{int(info['format']['size']) // 1024} KiB")

    url = stage_upload(out_mp4, r2_key)
    meta = build_metadata(date, stories, r2_key, out_mp4)
    meta["variant"] = "v2-motion-graphics"
    out_meta.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print("\nDone.")
    if url:
        print(f"  Review URL: {url}")


if __name__ == "__main__":
    main()
