#!/usr/bin/env python3
"""Build the ~30s Multiverse Gazette YouTube promo video.

Pipeline stages (each cached on disk under promo_build/ so reruns are cheap):
  1. Runway image-to-video: 5 x 5s cinematic clips from edition hero images.
  2. OpenAI TTS narration (voice "onyx"), tempo-nudged to land 27-29s.
  3. End card rendered with PIL (logo + tagline), animated with ffmpeg zoompan.
  4. ffmpeg assembly: 1920x1080 30fps H.264, 0.5s crossfades, loudnorm audio,
     optional music ducked under the narration if assets/promo-music.mp3 exists.
  5. Upload to R2 at promo/multiverse-gazette-promo.mp4.

Required env (checked per-stage, only when the stage actually needs to run):
  RUNWAY_API_KEY, OPENAI_API_KEY, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY

Optional env:
  PROMO_HERO_URLS   comma-separated public image URLs (overrides the defaults)
  PROMO_BUILD_DIR   working directory (default: <repo>/promo_build)
  PROMO_SKIP_UPLOAD set to 1 to skip the R2 upload stage
  OPENAI_TTS_MODEL  override TTS model (default gpt-4o-mini-tts, falls back
                    to tts-1-hd automatically)
"""

import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import requests

# ─── CONFIG ─────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = Path(os.environ.get("PROMO_BUILD_DIR", REPO_ROOT / "promo_build"))
CLIPS_DIR = BUILD_DIR / "clips"

LOGO_PATH = REPO_ROOT / "logo.png"
MUSIC_PATH = REPO_ROOT / "assets" / "promo-music.mp3"

WIDTH, HEIGHT, FPS = 1920, 1080, 30
CLIP_SECONDS = 5
ENDCARD_SECONDS = 7.0
FADE_SECONDS = 0.5
NARRATION_START = 0.5  # narration begins ~0.5s into the video

# Runway API (docs.dev.runwayml.com). gen4_turbo image-to-video is billed at
# 5 credits/second => 25 credits per 5s clip, 125 credits for all five.
RUNWAY_API_BASE = "https://api.dev.runwayml.com/v1"
RUNWAY_VERSION = "2024-11-06"
RUNWAY_MODEL = "gen4_turbo"
RUNWAY_RATIO = "1280:720"  # widest 16:9-ish ratio gen4_turbo supports
RUNWAY_POLL_INTERVAL = 6  # seconds (docs recommend >=5s with jitter)
RUNWAY_TIMEOUT = 15 * 60  # give up on a single task after 15 minutes

NARRATION_TEXT = (
    "Every day, a hundred parallel universes print their front page. "
    "Multiverse Gazette — daily galactic news from worlds where Rome never fell… "
    "where the dinosaurs had a space program… where AI chose a career in middle "
    "management. One hundred universes. One newspaper. Alternate history, "
    "conspiracy, and deadpan satire, fresh every morning — from Year 200 to "
    "Year 5000. Read today's edition from a world that never was — at "
    "thejumpuniverse dot com."
)
TTS_INSTRUCTIONS = (
    "Measured, unhurried movie-trailer documentary narrator. Deep, warm, "
    "deadpan and slightly wry. Pause briefly at the ellipses and dashes. "
    "Do not rush."
)
NARRATION_MIN, NARRATION_MAX, NARRATION_TARGET = 27.0, 29.0, 28.0

# Five visually diverse hero images (URL, universe, theme, Runway prompt).
DEFAULT_SHOTS = [
    (
        "https://images.thejumpuniverse.com/2026-07-06-4-hero.webp",
        "Glasswing (cyberpunk)",
        "slow cinematic dolly-in through neon-lit haze, rain-slicked reflections, "
        "atmospheric, subtle motion, newspaper-documentary tone",
    ),
    (
        "https://images.thejumpuniverse.com/2026-07-07-45-hero.webp",
        "The Ninth Ledger (medieval)",
        "slow cinematic push-in, candlelight flicker and drifting dust motes, "
        "solemn medieval atmosphere, subtle motion, newspaper-documentary tone",
    ),
    (
        "https://images.thejumpuniverse.com/2026-07-06-6-hero.webp",
        "Mirrormarch (atomic)",
        "slow lateral cinematic dolly, retro-futurist atomic-age optimism, gentle "
        "parallax, subtle motion, newspaper-documentary tone",
    ),
    (
        "https://images.thejumpuniverse.com/2026-07-07-48-hero.webp",
        "The Tin Parallel (wasteland)",
        "slow cinematic dolly-in across a desolate expanse, drifting dust and heat "
        "shimmer, bleak atmosphere, subtle motion, newspaper-documentary tone",
    ),
    (
        "https://images.thejumpuniverse.com/2026-07-06-7-hero.webp",
        "Kessler's Wake (vaporwave)",
        "slow dreamlike cinematic zoom, surreal neon vaporwave glow, floating "
        "particles, uncanny alien mood, subtle motion, newspaper-documentary tone",
    ),
]

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "0e5ed33a08d98c5105dfd8fe5c65d7be")
R2_BUCKET = os.environ.get("R2_BUCKET", "gazette-images")
R2_PUBLIC_BASE = os.environ.get("R2_PUBLIC_BASE", "https://images.thejumpuniverse.com").rstrip("/")
R2_ENDPOINT = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
R2_KEY = "promo/multiverse-gazette-promo.mp4"

DEJAVU_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/Library/Fonts",
]


# ─── HELPERS ────────────────────────────────────────────────────────────

def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def require_env(name, why):
    val = os.environ.get(name)
    if not val:
        die(f"Missing required environment variable {name} (needed for {why}). "
            f"Set it as a repo/Actions secret and retry.")
    return val


def run(cmd, **kw):
    print("+ " + " ".join(str(c) for c in cmd))
    res = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if res.returncode != 0:
        print(res.stdout[-4000:], file=sys.stderr)
        print(res.stderr[-4000:], file=sys.stderr)
        die(f"Command failed with exit code {res.returncode}: {cmd[0]}")
    return res


def ffprobe_json(path):
    res = run(["ffprobe", "-v", "quiet", "-print_format", "json",
               "-show_format", "-show_streams", path])
    return json.loads(res.stdout)


def media_duration(path):
    return float(ffprobe_json(path)["format"]["duration"])


def find_serif_font(bold=False):
    names = ["DejaVuSerif-Bold.ttf"] if bold else ["DejaVuSerif.ttf"]
    for d in DEJAVU_DIRS:
        for n in names:
            p = Path(d) / n
            if p.exists():
                return str(p)
    die("DejaVu Serif font not found; install fonts-dejavu (apt-get install -y fonts-dejavu)")


# ─── STAGE 1: RUNWAY CLIPS ──────────────────────────────────────────────

def runway_headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "X-Runway-Version": RUNWAY_VERSION,
        "Content-Type": "application/json",
    }


def runway_start_task(api_key, image_url, prompt):
    body = {
        "model": RUNWAY_MODEL,
        "promptImage": image_url,
        "promptText": prompt,
        "ratio": RUNWAY_RATIO,
        "duration": CLIP_SECONDS,
    }
    last_err = None
    for attempt in range(5):
        try:
            resp = requests.post(f"{RUNWAY_API_BASE}/image_to_video",
                                 headers=runway_headers(api_key),
                                 json=body, timeout=60)
            if resp.status_code == 200:
                return resp.json()["id"]
            if resp.status_code == 429 or resp.status_code >= 500:
                last_err = f"HTTP {resp.status_code}: {resp.text[:500]}"
                wait = 2 ** attempt * 5
                print(f"  Runway transient error ({last_err}); retrying in {wait}s")
                time.sleep(wait)
                continue
            # 4xx: not retryable — surface the validation error clearly.
            die(f"Runway rejected the image_to_video request "
                f"(HTTP {resp.status_code}): {resp.text[:2000]}")
        except requests.RequestException as e:
            last_err = str(e)
            time.sleep(2 ** attempt * 5)
    die(f"Runway image_to_video request failed after retries: {last_err}")


def runway_wait_for_task(api_key, task_id):
    deadline = time.time() + RUNWAY_TIMEOUT
    while time.time() < deadline:
        time.sleep(RUNWAY_POLL_INTERVAL + random.uniform(0, 2))
        try:
            resp = requests.get(f"{RUNWAY_API_BASE}/tasks/{task_id}",
                                headers=runway_headers(api_key), timeout=60)
        except requests.RequestException as e:
            print(f"  poll error ({e}); will retry")
            continue
        if resp.status_code != 200:
            print(f"  poll HTTP {resp.status_code}; will retry")
            continue
        task = resp.json()
        status = task.get("status")
        if status == "SUCCEEDED":
            output = task.get("output") or []
            if not output:
                die(f"Runway task {task_id} succeeded but returned no output")
            return output[0]
        if status in ("FAILED", "CANCELED"):
            die(f"Runway task {task_id} ended with status {status}: "
                f"{task.get('failure') or task.get('failureCode') or json.dumps(task)[:1000]}")
        print(f"  task {task_id}: {status}")
    die(f"Runway task {task_id} did not finish within {RUNWAY_TIMEOUT}s")


def download_file(url, dest):
    tmp = Path(str(dest) + ".part")
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    tmp.rename(dest)


def stage_runway_clips(shots):
    """Generate (or reuse cached) clips. Returns list of clip paths."""
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    paths = [CLIPS_DIR / f"clip{i + 1}.mp4" for i in range(len(shots))]
    missing = [i for i, p in enumerate(paths) if not p.exists()]
    if not missing:
        print("Stage 1: all Runway clips cached; skipping generation.")
        return paths

    api_key = require_env("RUNWAY_API_KEY", "Runway image-to-video generation")
    # Kick off all missing tasks first, then poll each — cheaper wall clock.
    tasks = {}
    for i in missing:
        url, label, prompt = shots[i]
        print(f"Stage 1: starting Runway task for clip{i + 1} [{label}]")
        tasks[i] = runway_start_task(api_key, url, prompt)
        time.sleep(1)
    for i in missing:
        print(f"Stage 1: waiting for clip{i + 1} (task {tasks[i]})")
        video_url = runway_wait_for_task(api_key, tasks[i])
        download_file(video_url, paths[i])
        print(f"  clip{i + 1} downloaded -> {paths[i]} "
              f"({paths[i].stat().st_size // 1024} KiB)")
    return paths


# ─── STAGE 2: NARRATION (OpenAI TTS) ────────────────────────────────────

def openai_tts(api_key, model, out_path):
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    kwargs = dict(model=model, voice="onyx", input=NARRATION_TEXT,
                  response_format="mp3")
    if model == "gpt-4o-mini-tts":
        kwargs["instructions"] = TTS_INSTRUCTIONS
    resp = client.audio.speech.create(**kwargs)
    out_path.write_bytes(resp.content)


def stage_narration():
    """Generate narration.mp3 tempo-adjusted to land 27-29s."""
    final = BUILD_DIR / "narration.mp3"
    raw = BUILD_DIR / "narration_raw.mp3"
    if final.exists():
        print(f"Stage 2: narration cached ({media_duration(final):.1f}s); skipping.")
        return final

    api_key = require_env("OPENAI_API_KEY", "OpenAI TTS narration")
    if not raw.exists():
        model = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        try:
            print(f"Stage 2: generating narration with {model} (voice: onyx)")
            openai_tts(api_key, model, raw)
        except Exception as e:
            if model != "tts-1-hd":
                print(f"  {model} failed ({e}); falling back to tts-1-hd")
                openai_tts(api_key, "tts-1-hd", raw)
            else:
                raise

    dur = media_duration(raw)
    print(f"Stage 2: raw narration is {dur:.2f}s")
    if NARRATION_MIN <= dur <= NARRATION_MAX:
        run(["ffmpeg", "-y", "-i", raw, "-c:a", "libmp3lame", "-q:a", "2", final])
    else:
        # atempo > 1 speeds up, < 1 slows down; keep the nudge subtle.
        tempo = max(0.88, min(1.15, dur / NARRATION_TARGET))
        print(f"Stage 2: retiming narration with atempo={tempo:.4f} "
              f"(-> ~{dur / tempo:.1f}s)")
        run(["ffmpeg", "-y", "-i", raw, "-filter:a", f"atempo={tempo:.4f}",
             "-c:a", "libmp3lame", "-q:a", "2", final])
    print(f"Stage 2: final narration {media_duration(final):.2f}s -> {final}")
    return final


# ─── STAGE 3: END CARD ──────────────────────────────────────────────────

def build_endcard_png(out_path):
    """Render the end card at 2x (3840x2160) so the zoompan has headroom."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

    w, h = WIDTH * 2, HEIGHT * 2  # 3840 x 2160

    # Dark aged-paper background: warm near-black with a soft radial glow.
    base = Image.new("RGB", (w, h), (16, 14, 12))
    glow = Image.new("L", (w, h), 0)
    gd = ImageDraw.Draw(glow)
    gd.ellipse([w * 0.12, h * 0.05, w * 0.88, h * 0.95], fill=46)
    glow = glow.filter(ImageFilter.GaussianBlur(220))
    warm = Image.new("RGB", (w, h), (58, 46, 32))
    bg = Image.composite(warm, base, glow)

    # Subtle paper grain.
    rnd = random.Random(45)  # deterministic
    noise = Image.effect_noise((w // 4, h // 4), 18).resize((w, h))
    grain = Image.merge("RGB", (noise, noise, noise))
    bg = ImageChops.add(bg, grain.point(lambda p: p // 22))

    # Logo (cream text on near-black): screen-blend so its dark plate melts
    # into the background. Scale 600x120 -> 1800x360 (2x canvas) with LANCZOS.
    logo = Image.open(LOGO_PATH).convert("RGB")
    lw, lh = 1800, 360
    logo = logo.resize((lw, lh), Image.LANCZOS)
    lx, ly = (w - lw) // 2, int(h * 0.34)
    region = bg.crop((lx, ly, lx + lw, ly + lh))
    bg.paste(ImageChops.screen(region, logo), (lx, ly))

    draw = ImageDraw.Draw(bg)
    serif = ImageFont.truetype(find_serif_font(), 108)
    serif_bold = ImageFont.truetype(find_serif_font(bold=True), 88)

    def centered(text, font, y, fill):
        tw = draw.textlength(text, font=font)
        draw.text(((w - tw) / 2, y), text, font=font, fill=fill)
        return tw

    # Thin newspaper rule between logo and tagline.
    rule_y = ly + lh + int(h * 0.045)
    draw.rectangle([w * 0.36, rule_y, w * 0.64, rule_y + 4], fill=(150, 128, 96))

    centered("100 universes. One newspaper.", serif,
             rule_y + int(h * 0.035), (232, 224, 205))
    centered("thejumpuniverse.com", serif_bold,
             rule_y + int(h * 0.035) + 190, (208, 168, 92))

    # Gentle vignette.
    vin = Image.new("L", (w, h), 0)
    vd = ImageDraw.Draw(vin)
    vd.ellipse([-w * 0.25, -h * 0.25, w * 1.25, h * 1.25], fill=255)
    vin = vin.filter(ImageFilter.GaussianBlur(300))
    bg = Image.composite(bg, Image.new("RGB", (w, h), (8, 7, 6)), vin)

    bg.save(out_path, "PNG")
    print(f"Stage 3: end card rendered -> {out_path}")


def stage_endcard():
    """End card PNG + slow-zoom MP4 (silent, WIDTHxHEIGHT, FPS)."""
    png = BUILD_DIR / "endcard.png"
    mp4 = BUILD_DIR / "endcard.mp4"
    if mp4.exists():
        print("Stage 3: end card video cached; skipping.")
        return mp4
    if not png.exists():
        build_endcard_png(png)
    frames = int(ENDCARD_SECONDS * FPS)
    # Single-image zoompan Ken Burns: d=<frames> emits that many frames from
    # the one input frame; zoom creeps ~1.0 -> ~1.10 over the card.
    zoom = (f"zoompan=z='min(zoom+0.0005,1.2)':d={frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={WIDTH}x{HEIGHT}:fps={FPS}")
    run(["ffmpeg", "-y", "-i", png, "-vf", zoom, "-frames:v", frames,
         "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-pix_fmt", "yuv420p", mp4])
    print(f"Stage 3: end card video -> {mp4} ({media_duration(mp4):.2f}s)")
    return mp4


# ─── STAGE 4: ASSEMBLY ──────────────────────────────────────────────────

def assemble(clip_paths, endcard_mp4, narration_path, music_path, out_path):
    """Concatenate clips + end card with crossfades; mix and master audio.

    Timeline (5 clips x 5s, 7s end card, 0.5s crossfades):
      total = 5*5 + 7 - 5*0.5 = 29.5s
    """
    segments = [CLIP_SECONDS] * len(clip_paths) + [ENDCARD_SECONDS]
    total = sum(segments) - FADE_SECONDS * len(clip_paths)

    inputs = []
    for p in clip_paths + [endcard_mp4]:
        inputs += ["-i", p]
    n_video = len(clip_paths) + 1
    narration_idx = n_video
    inputs += ["-i", narration_path]
    music_idx = None
    if music_path is not None:
        music_idx = n_video + 1
        inputs += ["-stream_loop", "-1", "-i", music_path]

    f = []
    # Normalize every video input: fill-crop to 1920x1080, 30fps, yuv420p,
    # identical timebase (xfade requires matching properties).
    for i, dur in enumerate(segments):
        f.append(
            f"[{i}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},fps={FPS},trim=duration={dur},"
            f"setpts=PTS-STARTPTS,format=yuv420p,settb=AVTB[v{i}]"
        )
    # Chain crossfades; the final one fades into the end card.
    prev = "v0"
    elapsed = segments[0]
    for i in range(1, n_video):
        offset = elapsed - FADE_SECONDS
        out = "vout" if i == n_video - 1 else f"x{i}"
        f.append(f"[{prev}][v{i}]xfade=transition=fade:"
                 f"duration={FADE_SECONDS}:offset={offset:.3f}[{out}]")
        elapsed = offset + segments[i]
        prev = out

    delay_ms = int(NARRATION_START * 1000)
    fade_start = max(0.0, total - 1.5)
    master = (f"loudnorm=I=-16:TP=-1.5:LRA=11,"
              f"aformat=sample_fmts=fltp:channel_layouts=stereo,aresample=48000,"
              f"afade=t=out:st={fade_start:.3f}:d=1.5,atrim=0:{total:.3f}[aout]")
    if music_idx is None:
        f.append(f"[{narration_idx}:a]adelay={delay_ms}:all=1,apad,"
                 f"atrim=0:{total:.3f},{master}")
    else:
        # Duck the music under the narration (sidechain), then mix.
        f.append(f"[{narration_idx}:a]adelay={delay_ms}:all=1,apad,"
                 f"atrim=0:{total:.3f},asplit=2[nar1][nar2]")
        f.append(f"[{music_idx}:a]atrim=0:{total:.3f},volume=0.35,"
                 f"aformat=channel_layouts=stereo[mus]")
        f.append("[mus][nar1]sidechaincompress=threshold=0.03:ratio=8:"
                 "attack=20:release=400[duck]")
        f.append(f"[nar2][duck]amix=inputs=2:duration=first:normalize=0,{master}")

    run(["ffmpeg", "-y", *inputs,
         "-filter_complex", ";".join(f),
         "-map", "[vout]", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-profile:v", "high", "-r", FPS, "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", "-t", f"{total:.3f}", out_path])
    print(f"Stage 4: assembled {out_path} ({media_duration(out_path):.2f}s, "
          f"target {total:.2f}s)")
    return out_path


def stage_assemble(clip_paths, endcard_mp4, narration_path):
    out = BUILD_DIR / "promo.mp4"
    if out.exists():
        print("Stage 4: promo.mp4 cached; skipping assembly.")
        return out
    music = MUSIC_PATH if MUSIC_PATH.exists() else None
    if music:
        print(f"Stage 4: found {MUSIC_PATH}; ducking it under the narration.")
    else:
        print("Stage 4: no assets/promo-music.mp3; narration only.")
    return assemble(clip_paths, endcard_mp4, narration_path, music, out)


# ─── STAGE 5: R2 UPLOAD ─────────────────────────────────────────────────

def stage_upload(path):
    if os.environ.get("PROMO_SKIP_UPLOAD"):
        print("Stage 5: PROMO_SKIP_UPLOAD set; skipping R2 upload.")
        return None
    access_key = require_env("R2_ACCESS_KEY_ID", "R2 upload")
    secret_key = require_env("R2_SECRET_ACCESS_KEY", "R2 upload")
    import boto3
    client = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )
    print(f"Stage 5: uploading {path} -> s3://{R2_BUCKET}/{R2_KEY}")
    client.upload_file(
        str(path), R2_BUCKET, R2_KEY,
        ExtraArgs={"ContentType": "video/mp4",
                   "CacheControl": "public, max-age=3600"},
    )
    url = f"{R2_PUBLIC_BASE}/{R2_KEY}"
    print(f"Stage 5: uploaded. Public URL: {url}")
    return url


# ─── MAIN ───────────────────────────────────────────────────────────────

def resolve_shots():
    override = os.environ.get("PROMO_HERO_URLS", "").strip()
    if not override:
        return DEFAULT_SHOTS
    urls = [u.strip() for u in override.split(",") if u.strip()]
    if len(urls) != len(DEFAULT_SHOTS):
        die(f"PROMO_HERO_URLS must list exactly {len(DEFAULT_SHOTS)} URLs "
            f"(got {len(urls)})")
    generic = ("slow cinematic dolly-in, atmospheric, subtle motion, "
               "newspaper-documentary tone")
    return [(u, f"custom image {i + 1}", generic) for i, u in enumerate(urls)]


def main():
    for tool in ("ffmpeg", "ffprobe"):
        if subprocess.run(["which", tool], capture_output=True).returncode != 0:
            die(f"{tool} not found on PATH (apt-get install -y ffmpeg)")
    if not LOGO_PATH.exists():
        die(f"logo not found at {LOGO_PATH}")

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    shots = resolve_shots()
    print("Promo pipeline starting. Shots:")
    for url, label, _ in shots:
        print(f"  - {label}: {url}")

    clips = stage_runway_clips(shots)
    narration = stage_narration()
    endcard = stage_endcard()
    promo = stage_assemble(clips, endcard, narration)

    info = ffprobe_json(promo)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    print(f"Final video: {v['width']}x{v['height']} {v['codec_name']} "
          f"{info['format']['duration']}s, "
          f"{int(info['format']['size']) // 1024} KiB")

    url = stage_upload(promo)
    print("\nDone.")
    print(f"  Local file: {promo}")
    if url:
        print(f"  Public URL: {url}")


if __name__ == "__main__":
    main()
