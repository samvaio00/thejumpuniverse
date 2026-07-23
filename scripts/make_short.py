#!/usr/bin/env python3
"""Build the daily Multiverse Gazette YouTube Short (vertical news bulletin).

Format: a ~20-28s (hard max 45s) 1080x1920 30fps news-bulletin rundown of the
day's 3 top stories from 3 different universes. Anchor-style narration reads
the headlines; each story gets a quick vertical video segment from its
edition's hero image, with TV-news chyrons burned in.

Pipeline stages (cached on disk under promo_build/shorts/<date>/ so reruns
are cheap; delete that directory to force a full rebuild):
  1. Story selection: deterministic heuristic over that date's editions
     (lead/default universe first, then shortest/punchiest headlines).
  2. Visual clips, one per story, from the story's hero_image:
       - default: Google Veo 3.1 image-to-video via kie.ai (model veo3_fast,
         native 9:16, 8s per clip, ~$0.33/clip -> ~$1/day), with a
         story-driven promptText (scene action from headline/deck keywords +
         universe inhabitants + theme motion flavor); a clip whose task fails
         is retried once, then degrades to Ken Burns for that story only so
         the daily Short always ships. Set VEO_MODEL=veo3 for Quality tier.
       - SHORT_NO_VEO=1 (or legacy SHORT_NO_RUNWAY=1): randomized Ken Burns
         on the still image (diagonal pan + alternating zoom, zero cost)
  3. OpenAI TTS narration (voice onyx, brisk anchor pacing), <= ~24.5s
     (clause-trimming + atempo 0.95-1.25 keep it in budget).
  4. Per-segment renders: 8s clip fitted to the segment (plain trim, or a
     slow-mo stretch up to 1.6x plus a last-frame hold for any remainder —
     time NEVER runs backwards), lower-third chyron burned in via
     drawtext textfile=.
  5. Final assembly: 0.3s xfades, persistent top banner (with the brand logo
     badge from assets/brand/) + watermark, 3s outro, loudness-normalized
     narration. Total = narration + 3s outro.
  6. Upload to R2 at shorts/<date>.mp4 + write <date>-short.json metadata
     (YouTube title/description/tags) next to the mp4.

Required env (checked only when the stage actually needs to run):
  KIE_API_KEY, OPENAI_API_KEY, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY

Optional env:
  SHORT_DATE         edition date YYYY-MM-DD (default: today UTC)
  SHORT_TIMELINES    e.g. "45,3,90" — exact universe IDs to feature (order kept)
  SHORT_NO_VEO       set to 1 for the zero-cost Ken Burns fallback visuals
  SHORT_SKIP_UPLOAD  set to 1 to skip the R2 upload stage
  SHORT_BUILD_DIR    working directory (default: <repo>/promo_build/shorts)
  VEO_MODEL          veo3_fast (default) or veo3 (Quality tier)
  OPENAI_TTS_MODEL   override TTS model (default gpt-4o-mini-tts, falls back
                     to tts-1-hd automatically)

Outputs:
  promo_build/shorts/YYYY-MM-DD-short.mp4
  promo_build/shorts/YYYY-MM-DD-short.json   (metadata for the YouTube upload)
"""

import datetime as dt
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

# ─── CONFIG ─────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
EDITIONS_DIR = REPO_ROOT / "editions"
BUILD_ROOT = Path(os.environ.get("SHORT_BUILD_DIR", REPO_ROOT / "promo_build" / "shorts"))
LOGO_BADGE = REPO_ROOT / "assets" / "brand" / "logo-badge-512.png"

WIDTH, HEIGHT, FPS = 1080, 1920, 30
FADE_SECONDS = 0.3          # fast news-style crossfade
OUTRO_SECONDS = 3.0         # end-card overlay over the last frames
NARRATION_START = 0.4       # narration begins ~0.4s in
NARRATION_MAX = 24.5        # keeps total <= ~28s (target window 20-28s)
TOTAL_MIN, TOTAL_HARD_MAX = 20.0, 45.0

# Veo 3.1 via kie.ai (docs.kie.ai/veo3-api). Native 9:16, fixed 8s clips.
KIE_BASE = "https://api.kie.ai/api/v1/veo"
VEO_MODEL = os.environ.get("VEO_MODEL", "veo3_fast")
VEO_CLIP_SECONDS = 8
VEO_POLL_INTERVAL = 12
VEO_TIMEOUT = 20 * 60
VEO_COST_PER_CLIP = {"veo3_fast": 0.325, "veo3": 1.275}
MAX_SLOWMO = 1.6          # max slow-mo stretch before holding the last frame
RUNWAY_PROMPT_MAX = 700   # prompt length cap (name kept for compat)

# Story-driven scene animation: promptText is built per segment from the
# edition (headline/deck/theme) + universe registry (inhabitants), instead of
# a generic camera move, so the motion matches the story. First keyword hit
# (headline+deck, checked in order) picks the scene-action clause.
ACTION_MOTIONS = [
    (("raid", "march", "protest", "riot", "strike", "mob", "crowd", "rally",
      "uprising", "revolt"),
     "the crowd surges forward, banners and raised arms swaying"),
    (("float", "fly", "flying", "drift", "levitat", "airship", "balloon", "soar"),
     "objects and figures drift and rise slowly through the air"),
    (("burn", "fire", "flame", "blaze", "scorch", "ember"),
     "flames lick upward while embers and smoke swirl"),
    (("collapse", "crumble", "topple", "fall", "ruin", "sink"),
     "debris crumbles and dust billows as structures give way"),
    (("explod", "blast", "erupt", "detonat"),
     "a shockwave ripples outward, dust and sparks flying"),
    (("flood", "wave", "tide", "storm", "rain", "sea", "river"),
     "water churns and ripples, spray catching the light"),
    (("dance", "festival", "celebrat", "parade", "feast", "wedding", "victory"),
     "figures dance and celebrate, streamers and lanterns swaying"),
    (("decree", "proclaim", "announc", "council", "vote", "tribunal", "court",
      "law", "ban "),
     "officials gesture emphatically while onlookers react and murmur"),
    (("machine", "engine", "factory", "gear", "automat", "robot", "mechan"),
     "machinery whirs, pistons pump and gears turn steadily"),
    (("market", "trade", "price", "merchant", "auction", "bazaar"),
     "traders haggle and goods change hands in a bustling scene"),
    (("escape", "flee", "chase", "hunt", "pursu", "smuggl"),
     "figures rush past, coats and dust trailing behind them"),
    (("ghost", "spirit", "haunt", "curse", "ritual", "summon", "omen"),
     "spectral wisps curl through the air as figures recoil"),
    (("ship", "sail", "harbor", "dock", "fleet", "voyage"),
     "vessels rock gently, rigging and flags snapping in the wind"),
    (("snow", "ice", "frost", "freez", "winter"),
     "snow flurries drift across the scene as figures huddle and move"),
    (("light", "glow", "beacon", "lantern", "neon", "signal", "flicker"),
     "lights pulse and flicker, casting moving shadows over the figures"),
]
DEFAULT_ACTION = "the crowd and machinery in the scene come alive with motion"

# Per-theme atmospheric motion flavor (the 8 registry themes).
THEME_MOTION = {
    "victorian": "Gas lamps flicker, steam curls from pipes, coat-tails stir in the breeze",
    "artdeco": "Searchlights sweep the sky, cigarette smoke curls, chrome glints as traffic glides past",
    "soviet": "Red banners ripple, factory smoke drifts past concrete facades",
    "cyberpunk": "Neon signs flicker, rain streaks the air, drones drift between holographic ads",
    "medieval": "Banners wave, candle and torch flames gutter, straw and dust swirl",
    "atomic": "Chrome fins gleam, atomic-age signage buzzes and blinks, sprinklers arc in the sun",
    "vaporwave": "Pastel light shimmers, palm fronds sway, glitchy scanlines roll across surfaces",
    "wasteland": "Dust gusts across the ground, heat shimmer bends the horizon, scrap metal creaks",
}
DEFAULT_THEME_MOTION = "Atmospheric light shifts and haze drifts across the scene"
THEME_MOTION["modern"] = ("Everyday American life in motion — flags rippling, store signs flickering, "
                          "pedestrians crossing, traffic (or its conspicuous absence) flowing past")


TTS_INSTRUCTIONS = (
    "Confident, brisk TV news anchor delivering a headline rundown. Crisp "
    "diction, energetic but controlled, a hint of deadpan amusement. Very "
    "short beats between stories; do not drag."
)

GOLD = "0xE8B84B"
CREAM = "0xD8D0C0"

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "0e5ed33a08d98c5105dfd8fe5c65d7be")
R2_BUCKET = os.environ.get("R2_BUCKET", "gazette-images")
R2_PUBLIC_BASE = os.environ.get("R2_PUBLIC_BASE", "https://images.thejumpuniverse.com").rstrip("/")
R2_ENDPOINT = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

DEJAVU_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/Library/Fonts",
    str(Path.home() / "Library" / "Fonts"),
]

HASHTAGS = "#shorts #whatif #alternatehistory #satire #funny #multiverse"
BASE_TAGS = [
    "what if", "what if history", "counterfactual", "thought experiment",
    "alternate history", "satire", "parallel universe", "news parody",
    "funny news", "brain teaser", "multiverse gazette", "ai news", "shorts",
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


def find_font(bold=True):
    names = ["DejaVuSans-Bold.ttf"] if bold else ["DejaVuSans.ttf"]
    for d in DEJAVU_DIRS:
        for n in names:
            p = Path(d) / n
            if p.exists():
                return str(p)
    die("DejaVu Sans font not found; install fonts-dejavu (apt-get install -y fonts-dejavu)")


def short_date():
    raw = os.environ.get("SHORT_DATE", "").strip()
    if raw:
        try:
            return dt.datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            die(f"SHORT_DATE must be YYYY-MM-DD (got {raw!r})")
    return dt.datetime.now(dt.timezone.utc).date()


def month_day(date):
    return f"{date.strftime('%B')} {date.day}"


def download_file(url, dest):
    tmp = Path(str(dest) + ".part")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    tmp.rename(dest)


def write_textfile(path, text):
    """drawtext textfile= sidesteps filtergraph escaping entirely (headlines
    contain apostrophes, colons and percent signs)."""
    Path(path).write_text(text, encoding="utf-8")
    return str(path)


def wrap_to_pixels(text, font_path, font_size, max_px, max_lines=3):
    """Greedy word-wrap measured with the actual font so chyron lines fit."""
    from PIL import ImageFont
    font = ImageFont.truetype(font_path, font_size)
    lines, cur = [], ""
    for word in text.split():
        cand = f"{cur} {word}".strip()
        if not cur or font.getlength(cand) <= max_px:
            cur = cand
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and font.getlength(last + "…") > max_px:
            last = last.rsplit(" ", 1)[0] if " " in last else last[:-2]
        lines[-1] = (last or lines[-1][:8]) + "…"
    return lines


# ─── STAGE 0: STORY SELECTION ───────────────────────────────────────────

def lead_timeline_for(date):
    """generate.default_timeline_for(date); falls back to the same formula
    computed locally if the import misbehaves."""
    try:
        cwd = os.getcwd()
        os.chdir(REPO_ROOT)  # generate.py reads universes.json relative to cwd
        sys.path.insert(0, str(REPO_ROOT))
        try:
            import generate
            return generate.default_timeline_for(date)
        finally:
            os.chdir(cwd)
    except Exception as e:
        print(f"  note: could not import generate.py ({e}); using local formula")
        try:
            n = len(json.loads((REPO_ROOT / "universes.json").read_text(encoding="utf-8")))
        except Exception:
            n = 8
        return (date.timetuple().tm_yday * 13) % n + 1


def load_editions(date_slug):
    editions = []
    for f in sorted(EDITIONS_DIR.glob(f"{date_slug}-*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if d.get("headline") and str(d.get("hero_image", "")).startswith("http"):
            editions.append(d)
    return editions


def select_stories(date, editions):
    """Pick 3 editions: lead/default universe first, then the shortest,
    punchiest headline+divergence combos. Deterministic; SHORT_TIMELINES
    (e.g. "45,3,90") overrides completely."""
    by_id = {e["timeline_id"]: e for e in editions}
    override = os.environ.get("SHORT_TIMELINES", "").strip()
    if override:
        try:
            ids = [int(x) for x in override.split(",") if x.strip()]
        except ValueError:
            die(f"SHORT_TIMELINES must be comma-separated integers (got {override!r})")
        missing = [i for i in ids if i not in by_id]
        if missing or len(ids) < 3:
            die(f"SHORT_TIMELINES={override}: need 3 IDs with editions on this "
                f"date (available: {sorted(by_id)}; missing: {missing})")
        return [by_id[i] for i in ids[:3]]

    picks = []
    lead = lead_timeline_for(date)
    if lead in by_id:
        picks.append(by_id[lead])

    def punchiness(e):  # lower is better: short headline, short divergence
        return len(e["headline"]) + 0.3 * len(e.get("divergence") or "")

    rest = sorted((e for e in editions
                   if e["timeline_id"] not in {p["timeline_id"] for p in picks}),
                  key=lambda e: (punchiness(e), e["timeline_id"]))
    picks.extend(rest)
    if len(picks) < 3:
        die(f"Only {len(picks)} usable editions for {date} — need 3. "
            f"Has the daily generation run yet?")
    return picks[:3]


# ─── STAGE 1: VISUAL CLIPS ──────────────────────────────────────────────

_UNIVERSE_REGISTRY = None


def universe_registry():
    """universes.json keyed by id (cached). Empty dict if unreadable — the
    prompt builder then degrades to edition-only wording, never aborts."""
    global _UNIVERSE_REGISTRY
    if _UNIVERSE_REGISTRY is None:
        try:
            data = json.loads((REPO_ROOT / "universes.json").read_text(encoding="utf-8"))
            _UNIVERSE_REGISTRY = {u["id"]: u for u in data}
        except Exception as e:
            print(f"  note: could not load universes.json ({e}); "
                  f"using generic prompt details")
            _UNIVERSE_REGISTRY = {}
    return _UNIVERSE_REGISTRY


def action_hint_for(text):
    """First keyword hit in headline+deck picks the scene-action clause."""
    low = text.lower()
    for keywords, motion in ACTION_MOTIONS:
        if any(k in low for k in keywords):
            return motion
    return DEFAULT_ACTION


def inhabitants_short(inhabitants):
    """Compress the registry's inhabitants description to a short noun phrase
    (first clause, capped at a word boundary ~70 chars)."""
    s = " ".join((inhabitants or "").split()).split(",")[0].strip()
    if len(s) > 70:
        s = s[:70].rsplit(" ", 1)[0]
    return s or "inhabitants"


def runway_prompt_for(story):
    """Story-driven promptText: scene action derived from the headline+deck,
    inhabitants from the universe registry, theme-flavored ambient motion.
    Demands strong, immediately visible motion (painterly newspaper stills
    otherwise tempt the model into near-static shimmer). Capped at
    RUNWAY_PROMPT_MAX chars. (Name kept from the Runway era for compat.)"""
    uni = universe_registry().get(story.get("timeline_id"), {})
    action = action_hint_for(f"{story.get('headline', '')} {story.get('deck', '')}")
    who = inhabitants_short(uni.get("inhabitants", ""))
    theme = story.get("theme") or uni.get("theme") or ""
    theme_motion = THEME_MOTION.get(theme, DEFAULT_THEME_MOTION)
    prompt = (
        f"Animate with strong, clearly visible motion from the very first frame: "
        f"{action}. The {who} in the scene keep moving — walking, gesturing, "
        f"turning, reacting, with upbeat comedic energy. {theme_motion}. "
        f"Bright cheerful daylight, vivid saturated colors, lively contemporary "
        f"look — never gloomy, never sepia, never vintage. Fabric and hair move "
        f"in the wind; sunlight plays across surfaces. Slow dolly-in with clear "
        f"parallax between foreground and background. Bold, fun animation for "
        f"the entire duration — never a frozen or static frame. Vertical "
        f"composition, no text."
    )
    if len(prompt) > RUNWAY_PROMPT_MAX:
        prompt = prompt[:RUNWAY_PROMPT_MAX - 1].rsplit(" ", 1)[0].rstrip(" ,;:—-") + "."
    return prompt


class VeoError(Exception):
    """A single Veo clip failed (task creation, generation, or download).
    Callers retry the clip once, then fall back to Ken Burns for that story."""


_KEY_LOGGED = False


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


def stage_veo_clips(work_dir, stories):
    """One 8s vertical Veo clip per story, cached as clip-<timeline>.mp4.

    Per-clip resilience: a clip whose task fails (creation, upstream
    generation failure, poll timeout, or result download) is retried once
    with a brand-new task; if that also fails, that story alone falls back
    to the Ken Burns hero still and the run continues.

    Returns (paths, kb_flags): per-story source path + whether that story
    must be rendered via the Ken Burns fallback instead of a Veo clip.
    """
    paths = [work_dir / f"clip-{s['timeline_id']}.mp4" for s in stories]
    kb_flags = [False] * len(stories)
    missing = [i for i, p in enumerate(paths) if not p.exists()]
    if not missing:
        print("Stage 1: all Veo clips cached; skipping generation.")
        return paths, kb_flags
    tasks, failed = {}, []
    for i in missing:
        s = stories[i]
        prompt = runway_prompt_for(s)
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
            video_url = veo_wait_for_task(tasks[i])
            download_file(video_url, paths[i])
            print(f"  clip {i + 1} -> {paths[i]} ({paths[i].stat().st_size // 1024} KiB)")
        except (VeoError, requests.RequestException) as e:
            print(f"  WARNING: Veo clip {i + 1} failed: {e}")
            failed.append(i)
    # One retry per failed clip (fresh task); if that also fails, fall back
    # to the Ken Burns still for that story only instead of aborting the run.
    for i in failed:
        s = stories[i]
        print(f"Stage 1: retrying Veo clip {i + 1} ({s['universe_name']}) "
              f"with a new task")
        try:
            task_id = veo_start_task(s["hero_image"], runway_prompt_for(s))
            video_url = veo_wait_for_task(task_id)
            download_file(video_url, paths[i])
            print(f"  clip {i + 1} -> {paths[i]} ({paths[i].stat().st_size // 1024} KiB)")
        except (VeoError, requests.RequestException) as e:
            print(f"  WARNING: Veo retry for clip {i + 1} also failed: {e}")
            print(f"  WARNING: falling back to Ken Burns visuals for story "
                  f"{i + 1} ({s['universe_name']}, timeline {s['timeline_id']}) only.")
            paths[i] = stage_hero_images(work_dir, [s])[0]
            kb_flags[i] = True
    return paths, kb_flags


def stage_hero_images(work_dir, stories):
    """Ken Burns fallback inputs: the raw hero stills, cached per timeline."""
    paths = []
    for s in stories:
        url = s["hero_image"]
        suffix = Path(url.split("?")[0]).suffix or ".png"
        p = work_dir / f"hero-{s['timeline_id']}{suffix}"
        if not p.exists():
            print(f"Stage 1: downloading hero image for {s['universe_name']}")
            download_file(url, p)
        paths.append(p)
    return paths


# ─── STAGE 2: NARRATION ─────────────────────────────────────────────────

def narration_headline(headline):
    """Tighten a headline for speech: normalize shouting caps, drop trailing
    deck-like clauses when it runs long, end with a period."""
    h = " ".join(headline.split())
    if h.isupper():
        h = h.capitalize()
    if len(h) > 110:
        for sep in ("; ", " — ", " - ", ", ", ": "):
            idx = h.rfind(sep, 40, 110)
            if idx > 0:
                h = h[:idx]
                break
        else:
            h = h[:110].rsplit(" ", 1)[0]
    h = h.rstrip(" ,;:—-")
    return h if h.endswith((".", "!", "?")) else h + "."


def short_alteration(divergence):
    """Compact 'what changed' clause: the setup before the em-dash joke.
    E.g. 'the wheel was never invented — ...' -> 'the wheel was never invented'."""
    d = (divergence or "").split(" — ")[0].split(" - ")[0].strip().rstrip(".")
    return d if 0 < len(d) <= 70 else ""


def narration_script(date, stories):
    h = [narration_headline(s["headline"]) for s in stories]
    u = [s["universe_name"] for s in stories]
    a = [short_alteration(s.get("divergence")) for s in stories]
    line1 = (f"In an America where {a[0]}: {h[0]} " if a[0] else f"In {u[0]}: {h[0]} ")
    line2 = (f"Where {a[1]}: {h[1]} " if a[1] else f"In {u[1]}: {h[1]} ")
    line3 = f"And in {u[2]}: {h[2]} "
    return (
        f"From the Multiverse Gazette, {month_day(date)} briefing. "
        + line1 + line2 + line3 +
        f"One thing missing per timeline — full papers at thejumpuniverse dot com."
    )


def openai_tts(api_key, model, text, out_path):
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    kwargs = dict(model=model, voice="onyx", input=text, response_format="mp3")
    if model == "gpt-4o-mini-tts":
        kwargs["instructions"] = TTS_INSTRUCTIONS
    resp = client.audio.speech.create(**kwargs)
    out_path.write_bytes(resp.content)


def stage_narration(work_dir, text):
    key = hashlib.md5(text.encode("utf-8")).hexdigest()[:10]
    final = work_dir / f"narration-{key}.mp3"
    raw = work_dir / f"narration-{key}-raw.mp3"
    if final.exists():
        print(f"Stage 2: narration cached ({media_duration(final):.1f}s); skipping.")
        return final
    api_key = require_env("OPENAI_API_KEY", "OpenAI TTS narration")
    if not raw.exists():
        model = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        try:
            print(f"Stage 2: generating narration with {model} (voice: onyx)")
            openai_tts(api_key, model, text, raw)
        except Exception as e:
            if model != "tts-1-hd":
                print(f"  {model} failed ({e}); falling back to tts-1-hd")
                openai_tts(api_key, "tts-1-hd", text, raw)
            else:
                raise
    dur = media_duration(raw)
    print(f"Stage 2: raw narration is {dur:.2f}s")
    tempo = None
    if dur > NARRATION_MAX:
        tempo = min(1.25, dur / NARRATION_MAX)
    elif dur < 15.0:
        tempo = max(0.95, dur / 15.0)
    if tempo and abs(tempo - 1.0) > 0.01:
        print(f"Stage 2: retiming narration with atempo={tempo:.4f} "
              f"(-> ~{dur / tempo:.1f}s)")
        run(["ffmpeg", "-y", "-i", raw, "-filter:a", f"atempo={tempo:.4f}",
             "-c:a", "libmp3lame", "-q:a", "2", final])
    else:
        run(["ffmpeg", "-y", "-i", raw, "-c:a", "libmp3lame", "-q:a", "2", final])
    fdur = media_duration(final)
    if fdur > NARRATION_MAX + 2.0:
        print(f"  WARNING: narration still {fdur:.1f}s after retiming; "
              f"total will exceed the 20-28s target (hard cap {TOTAL_HARD_MAX}s).")
    print(f"Stage 2: final narration {fdur:.2f}s -> {final}")
    return final


# ─── STAGE 3: SEGMENTS (chyron burned in) ───────────────────────────────

def chyron_filters(work_dir, idx, story, bold_font, hide_after=None):
    """Lower-third: gold kicker line + wrapped white headline on a translucent
    black box with a gold accent bar. Returns the drawbox/drawtext chain.
    hide_after (seconds, segment-local) hides the chyron under the outro."""
    head_size, kick_size = 50, 36
    max_px = WIDTH - 64 - 56
    lines = wrap_to_pixels(story["headline"], bold_font, head_size, max_px, max_lines=3)
    kicker = f"{story['universe_name'].upper()}  ·  YEAR {story['universe_year']}"
    kick_file = write_textfile(work_dir / f"chyron{idx}-kicker.txt", kicker)
    head_file = write_textfile(work_dir / f"chyron{idx}-headline.txt", "\n".join(lines))
    line_h = head_size + 12
    box_h = 92 + len(lines) * line_h + 28
    box_y = 1560 - box_h
    en = "" if hide_after is None else f":enable='lt(t,{max(0.0, hide_after):.3f})'"
    return (
        f"drawbox=x=0:y={box_y}:w={WIDTH}:h={box_h}:color=black@0.6:t=fill{en},"
        f"drawbox=x=0:y={box_y}:w=14:h={box_h}:color={GOLD}:t=fill{en},"
        f"drawtext=fontfile={bold_font}:textfile={kick_file}:fontsize={kick_size}:"
        f"fontcolor={GOLD}:x=64:y={box_y + 30}:expansion=none{en},"
        f"drawtext=fontfile={bold_font}:textfile={head_file}:fontsize={head_size}:"
        f"fontcolor=white:line_spacing=12:x=64:y={box_y + 92}:expansion=none{en}"
    )


NORM = (f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},fps={FPS}")


def build_segment_from_clip(clip_path, seg_dur, chyron, out_path):
    """Fit the 8s Veo clip to seg_dur. Time NEVER runs backwards:
      - plain trim when the clip is long enough
      - otherwise a slow-mo stretch capped at MAX_SLOWMO (1.6x), with the
        last frame held (tpad clone) for any tiny remainder
    The old forward+reverse ping-pong is gone — it read as a distracting
    yin-yang time loop. Veo's audio track is dropped (only [v] is mapped)."""
    clip_dur = media_duration(clip_path)
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
    run(["ffmpeg", "-y", "-i", clip_path, "-filter_complex", vf,
         "-map", "[v]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-r", FPS, "-pix_fmt", "yuv420p", out_path])


def build_segment_kenburns(image_path, seg_dur, idx, chyron, out_path, seed=""):
    """Zero-cost fallback: randomized Ken Burns — a diagonal pan across the
    hero still combined with a stronger zoom (direction alternates per
    segment) plus a very subtle brightness pulse for a slow-parallax feel.
    Pan track is seeded (date+timeline) so reruns are deterministic but each
    segment/day drifts differently. Still plain zoompan math, zero cost."""
    frames = int(round(seg_dur * FPS))
    steps = max(frames - 1, 1)
    rng = random.Random(f"kenburns-{seed}-{idx}")
    # Zoom: alternate push-in / pull-back per segment. The floor stays a bit
    # above 1.0 so the diagonal pan always has crop headroom to travel in.
    z0, z1 = (1.04, 1.22) if idx % 2 == 0 else (1.22, 1.04)
    # Diagonal pan: start near a random corner (with jitter so segments don't
    # all ride the same track) and drift to the opposite corner; odd segments
    # reverse direction so back-to-back stills drift opposite ways.
    cx, cy = rng.choice([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)])
    x0 = min(1.0, max(0.0, cx + rng.uniform(-0.1, 0.1)))
    y0 = min(1.0, max(0.0, cy + rng.uniform(-0.1, 0.1)))
    x1, y1 = 1.0 - cx, 1.0 - cy
    if idx % 2 == 1:
        (x0, y0), (x1, y1) = (x1, y1), (x0, y0)
    p = f"(on/{steps})"
    zexpr = f"{z0}+{z1 - z0:.4f}*{p}"
    xexpr = f"(iw-iw/zoom)*({x0:.3f}+{x1 - x0:.3f}*{p})"
    yexpr = f"(ih-ih/zoom)*({y0:.3f}+{y1 - y0:.3f}*{p})"
    vf = (
        f"[0:v]scale={WIDTH * 2}:{HEIGHT * 2}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH * 2}:{HEIGHT * 2},"
        f"zoompan=z='{zexpr}':x='{xexpr}':y='{yexpr}'"
        f":d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},"
        f"hue=b='0.06*sin(2*PI*t/{max(seg_dur, 1.0):.2f})',"
        f"format=yuv420p,settb=AVTB,{chyron}[v]"
    )
    run(["ffmpeg", "-y", "-i", image_path, "-filter_complex", vf,
         "-map", "[v]", "-frames:v", frames,
         "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-r", FPS, "-pix_fmt", "yuv420p", out_path])


def stage_segments(work_dir, stories, sources, seg_dur, kb_flags):
    """kb_flags is per-segment: mixed runs (some Veo clips, some Ken Burns
    fallback stills) render each segment from its own source type."""
    bold = find_font(bold=True)
    out_paths = []
    for i, (story, src, kb) in enumerate(zip(stories, sources, kb_flags)):
        mode = "kb" if kb else "veo"
        out = work_dir / f"seg{i + 1}-{story['timeline_id']}-{mode}-{int(seg_dur * 1000)}.mp4"
        out_paths.append(out)
        if out.exists():
            print(f"Stage 3: segment {i + 1} cached; skipping.")
            continue
        print(f"Stage 3: rendering segment {i + 1} ({story['universe_name']}, "
              f"{seg_dur:.2f}s, {'Ken Burns' if kb else 'Veo'})")
        # The last segment carries the 3s outro overlay — drop its chyron then.
        hide_after = (seg_dur - OUTRO_SECONDS) if i == len(stories) - 1 else None
        chyron = chyron_filters(work_dir, i + 1, story, bold, hide_after)
        if kb:
            build_segment_kenburns(src, seg_dur, i, chyron, out,
                                   seed=f"{work_dir.name}-{story['timeline_id']}")
        else:
            build_segment_from_clip(src, seg_dur, chyron, out)
    return out_paths


# ─── STAGE 4: ASSEMBLY ──────────────────────────────────────────────────

def assemble(work_dir, date, segments, seg_dur, total, narration, out_path):
    bold = find_font(bold=True)
    reg = find_font(bold=False)
    banner_file = write_textfile(work_dir / "banner.txt",
                                 "MULTIVERSE GAZETTE — DAILY BRIEFING")
    date_file = write_textfile(work_dir / "banner-date.txt",
                               date.strftime("%A, %B %d, %Y").upper())
    wm_file = write_textfile(work_dir / "watermark.txt", "thejumpuniverse.com")
    outro1_file = write_textfile(work_dir / "outro1.txt", "NEW FRONT PAGES DAILY")
    outro2_file = write_textfile(work_dir / "outro2.txt", "thejumpuniverse.com")

    inputs = []
    for p in segments:
        inputs += ["-i", p]
    inputs += ["-i", narration]
    nar_idx = len(segments)
    # Brand logo badge in the banner (skipped gracefully if the asset is
    # missing so local/old checkouts still build).
    logo_idx = None
    if LOGO_BADGE.exists():
        inputs += ["-i", LOGO_BADGE]
        logo_idx = nar_idx + 1

    f = []
    # Fast 0.3s xfades between the three segments.
    o1 = seg_dur - FADE_SECONDS
    o2 = 2 * seg_dur - 2 * FADE_SECONDS
    f.append(f"[0:v][1:v]xfade=transition=fade:duration={FADE_SECONDS}:offset={o1:.3f}[x1]")
    f.append(f"[x1][2:v]xfade=transition=fade:duration={FADE_SECONDS}:offset={o2:.3f}[body]")

    outro_st = total - OUTRO_SECONDS
    fade_expr = f"'if(lt(t,{outro_st:.3f}),0,min(1,(t-{outro_st:.3f})/0.6))'"
    overlays = (
        # Persistent top banner (translucent strip + small-caps title + date).
        f"drawbox=x=0:y=96:w={WIDTH}:h=118:color=black@0.55:t=fill,"
        f"drawtext=fontfile={bold}:textfile={banner_file}:fontsize=38:"
        f"fontcolor=white:x=(w-text_w)/2:y=118:expansion=none,"
        f"drawtext=fontfile={reg}:textfile={date_file}:fontsize=26:"
        f"fontcolor={CREAM}:x=(w-text_w)/2:y=168:expansion=none,"
        # Persistent bottom watermark.
        f"drawtext=fontfile={bold}:textfile={wm_file}:fontsize=34:"
        f"fontcolor=white@0.75:x=(w-text_w)/2:y=1806:expansion=none,"
        # 3s outro over the last frames: dim + centered fade-in text.
        f"drawbox=x=0:y=0:w={WIDTH}:h={HEIGHT}:color=black@0.5:t=fill:"
        f"enable='gte(t,{outro_st:.3f})',"
        f"drawtext=fontfile={bold}:textfile={outro1_file}:fontsize=54:"
        f"fontcolor={GOLD}:x=(w-text_w)/2:y=830:alpha={fade_expr}:expansion=none,"
        f"drawtext=fontfile={bold}:textfile={outro2_file}:fontsize=76:"
        f"fontcolor=white:x=(w-text_w)/2:y=930:alpha={fade_expr}:expansion=none"
    )
    if logo_idx is not None:
        f.append(f"[body]{overlays}[vpre]")
        f.append(f"[{logo_idx}:v]scale=100:100[lg]")
        f.append(f"[vpre][lg]overlay=26:105[vout]")
    else:
        f.append(f"[body]{overlays}[vout]")

    delay_ms = int(NARRATION_START * 1000)
    f.append(
        f"[{nar_idx}:a]adelay={delay_ms}:all=1,apad,atrim=0:{total:.3f},"
        f"loudnorm=I=-16:TP=-1.5:LRA=11,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo,aresample=48000,"
        f"afade=t=out:st={max(0.0, total - 0.8):.3f}:d=0.8[aout]"
    )

    run(["ffmpeg", "-y", *inputs,
         "-filter_complex", ";".join(f),
         "-map", "[vout]", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-profile:v", "high", "-r", FPS, "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", "-t", f"{total:.3f}", out_path])
    print(f"Stage 4: assembled {out_path} ({media_duration(out_path):.2f}s, "
          f"target {total:.2f}s)")


# ─── STAGE 5: METADATA + R2 UPLOAD ──────────────────────────────────────

def display_headline(h):
    h = " ".join(h.split())
    return h.capitalize() if h.isupper() else h


def build_metadata(date, stories, r2_key, video_path):
    suffix = " #shorts"
    lead_alt = short_alteration(stories[0].get("divergence"))
    if lead_alt and len(lead_alt) <= 58:
        # Counterfactual era: the what-if IS the hook.
        title = f"What if {lead_alt}? {month_day(date)} Multiverse News{suffix}"
    else:
        prefix = f"Multiverse News, {month_day(date)}: "
        budget = 95 - len(prefix) - len(suffix)
        head = display_headline(min((s["headline"] for s in stories), key=len))
        if len(head) > budget:
            head = head[:budget - 1].rstrip(" ,;:—-") + "…"
        title = f"{prefix}{head}{suffix}"

    def story_line(s):
        alt = short_alteration(s.get("divergence"))
        head = display_headline(s["headline"])
        if alt:
            return f"What if {alt}? — {head}"
        return f"{s['universe_name']}, Year {s['universe_year']}: {head}"

    lines = [story_line(s) for s in stories]
    description = (
        "Three parallel Americas — each missing exactly one thing:\n\n"
        + "\n".join(lines) + (
        "\n\nRead the full front pages: https://thejumpuniverse.com"
        f"\n\n{HASHTAGS}"
    ))
    tags = BASE_TAGS + [s["universe_name"].lower() for s in stories]
    return {
        "date": date.strftime("%Y-%m-%d"),
        "title": title,
        "description": description,
        "tags": tags,
        "categoryId": "24",  # Entertainment
        "video": str(video_path),
        "r2_key": r2_key,
        "r2_url": f"{R2_PUBLIC_BASE}/{r2_key}",
        "stories": [{"timeline_id": s["timeline_id"],
                     "universe_name": s["universe_name"],
                     "universe_year": s["universe_year"],
                     "divergence": s.get("divergence"),
                     "headline": s["headline"]} for s in stories],
    }


def stage_upload(path, r2_key):
    if os.environ.get("SHORT_SKIP_UPLOAD"):
        print("Stage 5: SHORT_SKIP_UPLOAD set; skipping R2 upload.")
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
    print(f"Stage 5: uploading {path} -> s3://{R2_BUCKET}/{r2_key}")
    client.upload_file(
        str(path), R2_BUCKET, r2_key,
        ExtraArgs={"ContentType": "video/mp4",
                   "CacheControl": "public, max-age=3600"},
    )
    url = f"{R2_PUBLIC_BASE}/{r2_key}"
    print(f"Stage 5: uploaded. Public URL: {url}")
    return url


# ─── MAIN ───────────────────────────────────────────────────────────────

def main():
    for tool in ("ffmpeg", "ffprobe"):
        if subprocess.run(["which", tool], capture_output=True).returncode != 0:
            die(f"{tool} not found on PATH (apt-get install -y ffmpeg)")

    date = short_date()
    date_slug = date.strftime("%Y-%m-%d")
    kenburns = bool(os.environ.get("SHORT_NO_VEO") or os.environ.get("SHORT_NO_RUNWAY"))
    work_dir = BUILD_ROOT / date_slug
    work_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = BUILD_ROOT / f"{date_slug}-short.mp4"
    out_meta = BUILD_ROOT / f"{date_slug}-short.json"
    r2_key = f"shorts/{date_slug}.mp4"

    editions = load_editions(date_slug)
    if not editions:
        have = sorted({f.name[:10] for f in EDITIONS_DIR.glob("2*.json")})
        die(f"No editions found for {date_slug} in {EDITIONS_DIR} "
            f"(latest available: {have[-1] if have else 'none'}). "
            f"Has the daily generation committed yet? Set SHORT_DATE to override.")

    stories = select_stories(date, editions)
    print(f"Short pipeline for {date_slug} "
          f"({'Ken Burns fallback, $0' if kenburns else f'Veo 3.1 {VEO_MODEL} via kie.ai'}):")
    for i, s in enumerate(stories, 1):
        print(f"  {i}. [{s['timeline_id']}] {s['universe_name']} "
              f"(Year {s['universe_year']}): {s['headline']}")
    if not kenburns:
        cost = VEO_COST_PER_CLIP.get(VEO_MODEL, 0.325)
        print(f"  est. Veo cost: 3 clips x ${cost:.3f} = ~${3 * cost:.2f}/day")

    # Visual sources (stage 1) and narration (stage 2). kb_flags is
    # per-story: Veo failures degrade single stories to Ken Burns.
    if kenburns:
        sources = stage_hero_images(work_dir, stories)
        kb_flags = [True] * len(stories)
    else:
        sources, kb_flags = stage_veo_clips(work_dir, stories)
        if any(kb_flags):
            print(f"  NOTE: Ken Burns fallback in use for "
                  f"{sum(kb_flags)}/{len(kb_flags)} segment(s) after Veo failures.")
    script = narration_script(date, stories)
    print(f"Narration script ({len(script)} chars): {script}")
    narration = stage_narration(work_dir, script)
    ndur = media_duration(narration)

    # Timeline: 3 equal segments crossfaded, total = lead-in + narration + outro.
    total = NARRATION_START + ndur + OUTRO_SECONDS
    total = max(TOTAL_MIN, total)
    if total > TOTAL_HARD_MAX:
        die(f"Computed total {total:.1f}s exceeds the {TOTAL_HARD_MAX:.0f}s hard cap")
    seg_dur = (total + 2 * FADE_SECONDS) / 3
    print(f"Timeline: narration {ndur:.2f}s -> total {total:.2f}s, "
          f"3 segments of {seg_dur:.2f}s with {FADE_SECONDS}s xfades")

    segments = stage_segments(work_dir, stories, sources, seg_dur, kb_flags)

    # Homogeneous runs keep a stable cache key; mixed runs (partial fallback)
    # get their own key so finals never collide.
    if all(kb_flags):
        mode_key = "kb"
    elif not any(kb_flags):
        mode_key = "veo"
    else:
        mode_key = "-".join("kb" if f else "veo" for f in kb_flags)
    build_key = hashlib.md5(json.dumps(
        [date_slug, [s["timeline_id"] for s in stories],
         mode_key, int(ndur * 1000)]).encode()).hexdigest()[:10]
    final = work_dir / f"short-{build_key}.mp4"
    if final.exists():
        print("Stage 4: final video cached; skipping assembly.")
    else:
        assemble(work_dir, date, segments, seg_dur, total, narration, final)
    shutil.copyfile(final, out_mp4)

    info = ffprobe_json(out_mp4)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    print(f"Final video: {v['width']}x{v['height']} {v['codec_name']} "
          f"{float(info['format']['duration']):.2f}s, "
          f"{int(info['format']['size']) // 1024} KiB")

    url = stage_upload(out_mp4, r2_key)
    meta = build_metadata(date, stories, r2_key, out_mp4)
    meta["uploaded_to_r2"] = bool(url)
    out_meta.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"Metadata written -> {out_meta}")
    print(f"  title ({len(meta['title'])} chars): {meta['title']}")

    print("\nDone.")
    print(f"  Local file: {out_mp4}")
    print(f"  Metadata:   {out_meta}")
    if url:
        print(f"  Public URL: {url}")


if __name__ == "__main__":
    main()
