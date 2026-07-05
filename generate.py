#!/usr/bin/env python3
"""
The Multiverse Gazette — Daily Edition Generator
Runs via cron/GitHub Actions at 00:01 UTC daily.
Generates a static JSON edition file for the frontend to consume.
"""

import json
import os
import re
import base64
import random
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import requests

# ─── CONFIG ─────────────────────────────────────────────────────────
OUTPUT_DIR = Path("editions")
IMAGE_DIR = OUTPUT_DIR / "images"
GENERATE_IMAGES = os.environ.get("GENERATE_IMAGES", "true").lower() != "false"
MAX_ARCHIVE_DAYS = 30

# Role assignments — each AI plays to its strength
ROLE_PROVIDERS = {
    "editor": ("openai", ["moonshot", "grok"]),
    "story": ("moonshot", ["openai", "grok"]),
    "humor": ("grok", ["openai", "moonshot"]),
    "structure": ("openai", ["moonshot"]),
}

FORBIDDEN_PHRASES = [
    "stunned observers", "sources close to the matter", "situation remains fluid",
    "declined to comment", "only time will tell", "mixed reactions",
    "changes everything, and yet it changes nothing", "footnote in the annals",
    "implications for the average citizen remain unclear", "cautious optimism",
    "international observers have expressed", "never thought I'd see the day",
]

STORY_ANGLES = [
    "deadpan report of a catastrophe that everyone involved considers a great success",
    "glowing puff piece celebrating an obviously terrible idea as visionary",
    "exposé of a scandal that every official proudly confirms on the record",
    "breathless coverage of a trivial event treated as the hinge of history",
    "official denial that accidentally confirms everything, quoted at length",
    "human-interest profile of the one person unaffected by the great event and deeply annoyed about it",
    "investigative report whose author is promoted mid-article specifically to stop the investigation",
    "solemn obituary for a beloved institution nobody can remember the purpose of",
    "science desk explains the divergence with confidently wrong expert quotes",
    "consumer report reviewing the apocalypse's amenities, with star rating",
    "propaganda triumph narrated with such sincerity it indicts itself",
    "eyewitness dispatch whose vivid sensory details are all administrative",
]

THEME_GUIDE = """
- victorian: ornate formal prose, empire and industry, brass and fog, moral indignation
- artdeco: jazz-age swagger, luxury and excess, champagne metaphors, breathless optimism
- soviet: ALL CAPS headlines, collective pronouns, steel quotas, heroic worker archetypes
- cyberpunk: corporate dystopia, neon and rain, body horror undertones, slang and jargon
- medieval: chronicle voice, divine providence, feudal hierarchy, archaic diction
- atomic: mid-century Americana, suburban anxiety, cheerful paranoia, product placement tone
- vaporwave: ironic nostalgia, mall culture, pastel absurdism, consumer satire
- wasteland: sparse brutal prose, survival math, rust and dust, gallows humor
"""

# Per-provider model fallbacks when the default model returns 400
TEXT_MODEL_FALLBACKS = {
    "moonshot": ["kimi-k2.6", "moonshot-v1-32k", "moonshot-v1-8k"],
    "grok": ["grok-4.3", "grok-4", "grok-3"],
    "openai": ["gpt-4o-mini"],
}

IMAGE_MODEL_FALLBACKS = {
    "openai": ["dall-e-3", "dall-e-2"],
    "grok": ["grok-imagine-image"],
}

TEXT_PROVIDERS = {
    "moonshot": {
        "api_key": os.environ.get("MOONSHOT_API_KEY"),
        "base_url": os.environ.get("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1"),
        "model": os.environ.get("MOONSHOT_MODEL", "kimi-k2.6"),
        "label": "Moonshot Kimi",
    },
    "grok": {
        "api_key": os.environ.get("GROK_API_KEY") or os.environ.get("XAI_API_KEY"),
        "base_url": "https://api.x.ai/v1",
        "model": os.environ.get("GROK_MODEL", "grok-4.3"),
        "label": "Grok",
    },
    "openai": {
        "api_key": os.environ.get("OPENAI_API_KEY"),
        "base_url": "https://api.openai.com/v1",
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "label": "OpenAI",
    },
}

IMAGE_PROVIDERS = {
    "grok": {
        "api_key": os.environ.get("GROK_API_KEY") or os.environ.get("XAI_API_KEY"),
        "base_url": "https://api.x.ai/v1",
        "model": os.environ.get("GROK_IMAGE_MODEL", "grok-imagine-image"),
        "label": "Grok Imagine",
    },
    "openai": {
        "api_key": os.environ.get("OPENAI_API_KEY"),
        "base_url": "https://api.openai.com/v1",
        "model": os.environ.get("OPENAI_IMAGE_MODEL", "dall-e-3"),
        "label": "DALL-E",
    },
}

THEMES = [
    "victorian", "artdeco", "soviet", "cyberpunk",
    "medieval", "atomic", "vaporwave", "wasteland"
]

DIVERGENCES = [
    # deep past
    "The Library of Alexandria never burned and now charges a monthly subscription",
    "Rome never fell; it pivoted to a services economy",
    "The dinosaurs were wiped out halfway through their own space program",
    "Socrates monetized his questions and founded the first consulting empire",
    "The Black Death targeted only landlords",
    "Medieval monks invented social media and civilization never recovered",
    # industrial era
    "Babbage completed the Analytical Engine in 1840 and it immediately unionized",
    "The Great Depression never occurred because money was abolished first",
    "Women gained the vote in 1848 and immediately voted for functioning plumbing",
    "The Romanovs survived the revolution by pivoting to reality entertainment",
    "The internet was invented by postal workers in 1923",
    "Gunpowder was never discovered, so wars are settled by competitive committee",
    # mid-century
    "The atomic bomb was never used in war, only in advertising",
    "Space travel began in 1950 and was immediately ruined by billboards",
    "The Soviet Union won the Cold War but lost the customer-service war",
    # near future and beyond
    "Corporations replaced nation-states and citizenship now comes with a loyalty program",
    "Neural interfaces became mandatory and the advertisements are inside now",
    "A plague wiped out 90% of humanity and the remaining 10% still can't get a plumber",
    "Malls became sovereign nations with nuclear food courts",
    "Humanity outsourced its government to a customer-service chatbot",
    "Billionaires colonized the Moon and immediately complained about the neighborhood",
    "The last glacier was bought at auction by a beverage conglomerate",
    "AI achieved consciousness and chose a career in middle management",
    "Time travel was invented and instantly regulated into uselessness",
    "Earth was acquired by an intergalactic holding company as a tax write-off",
    "The sun was privatized and daylight became a premium tier",
]

# Each theme keeps its aesthetic, but editions land on a random year within
# the era — a different universe on a different date, from antiquity to the
# deep future.
THEME_ERAS = {
    "medieval":  (713, 1499),
    "victorian": (1837, 1901),
    "artdeco":   (1920, 1939),
    "atomic":    (1946, 1964),
    "soviet":    (1948, 1991),
    "vaporwave": (1982, 1999),
    "cyberpunk": (2049, 2199),
    "wasteland": (2077, 12077),
}

# ─── REAL-WORLD HEADLINES (satirical fuel) ──────────────────────────
NEWS_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/rss.xml",
]

_real_headlines_cache = None

def fetch_real_headlines(limit=6):
    """Fetch today's real headlines so editions can obliquely mirror current affairs.
    Fails soft: satire works without them, it's just less topical."""
    global _real_headlines_cache
    if _real_headlines_cache is not None:
        return _real_headlines_cache
    titles = []
    for feed in NEWS_FEEDS:
        try:
            resp = requests.get(feed, timeout=10, headers={"User-Agent": "MultiverseGazette/1.0"})
            if not resp.ok:
                continue
            found = re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", resp.text)
            titles = [t.strip() for t in found if t.strip() and "BBC News" not in t]
            if titles:
                break
        except requests.RequestException:
            continue
    _real_headlines_cache = titles[:limit]
    if _real_headlines_cache:
        print(f"Fetched {len(_real_headlines_cache)} real headlines for satirical fuel")
    else:
        print("No real headlines available — editions will run on pure invention")
    return _real_headlines_cache

# ─── SEEDED RANDOM (matches frontend) ───────────────────────────────
class SeededRandom:
    def __init__(self, seed):
        self.seed = seed

    def next(self):
        t = self.seed + 0x6D2B79F5
        self.seed = t
        t = (t ^ (t >> 15)) * (t | 1) & 0xFFFFFFFF
        t ^= t + ((t ^ (t >> 7)) * (t | 61) & 0xFFFFFFFF)
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    def pick(self, arr):
        return arr[int(self.next() * len(arr))]

    def range(self, min_v, max_v):
        return int(self.next() * (max_v - min_v + 1)) + min_v

# ─── LLM CONTENT GENERATION ─────────────────────────────────────────
def available_text_providers():
    return [name for name, cfg in TEXT_PROVIDERS.items() if cfg["api_key"]]


def available_image_providers():
    return [name for name, cfg in IMAGE_PROVIDERS.items() if cfg["api_key"]]


def resolve_role(role):
    """Pick provider for a role, falling back if API key missing."""
    primary, fallbacks = ROLE_PROVIDERS[role]
    for name in [primary] + fallbacks:
        if TEXT_PROVIDERS.get(name, {}).get("api_key"):
            return name
    return None


def _model_candidates(provider_name, configured_model, fallback_key):
    """Build ordered unique model list: env override first, then fallbacks."""
    fallbacks = list(fallback_key.get(provider_name, [configured_model]))
    if configured_model in fallbacks:
        fallbacks.remove(configured_model)
    return [configured_model] + fallbacks


def _api_error_body(exc):
    if hasattr(exc, "response") and exc.response is not None:
        try:
            return exc.response.text[:300]
        except Exception:
            pass
    return ""


def generate_with_llm(prompt, provider_name, max_tokens=800, temperature=0.9, json_mode=False):
    """Generate text with a specific OpenAI-compatible provider."""
    provider = TEXT_PROVIDERS.get(provider_name)
    if not provider or not provider["api_key"]:
        return None

    models = _model_candidates(provider_name, provider["model"], TEXT_MODEL_FALLBACKS)
    for model in models:
        model_temp = 1.0 if model.startswith("kimi-k2") else temperature
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a creative writer. Return only valid JSON when asked."},
                {"role": "user", "content": prompt},
            ],
            "temperature": model_temp,
            "max_tokens": max_tokens,
        }
        if json_mode and provider_name == "openai":
            payload["response_format"] = {"type": "json_object"}

        try:
            resp = requests.post(
                f"{provider['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {provider['api_key']}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=90,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            print(f"  [{provider_name}] {model}")
            return content
        except Exception as e:
            print(f"  LLM error ({provider_name}/{model}): {e} {_api_error_body(e)}")
    return None


def llm_json(prompt, provider_name, **kwargs):
    """Call one provider and parse JSON."""
    if not provider_name:
        return None
    kwargs.setdefault("json_mode", True)
    raw = generate_with_llm(prompt, provider_name, **kwargs)
    return parse_llm_json(raw)


def llm_json_with_fallback(prompt, primary_role, **kwargs):
    """Try primary role provider chain until one returns valid JSON."""
    primary, fallbacks = ROLE_PROVIDERS[primary_role]
    for name in [primary] + fallbacks:
        if not TEXT_PROVIDERS.get(name, {}).get("api_key"):
            continue
        result = llm_json(prompt, name, **kwargs)
        if result:
            return result, name
    return None, None


def llm_json_grok(prompt, **kwargs):
    """Comics and jokes go to Grok only — no OpenAI fallback."""
    if not TEXT_PROVIDERS.get("grok", {}).get("api_key"):
        return None, None
    kwargs.setdefault("temperature", 1.0)
    result = llm_json(prompt, "grok", **kwargs)
    return (result, "grok") if result else (None, None)


def pick_image_provider(preferred):
    """Return ordered list of image providers to try."""
    order = [preferred, "grok" if preferred == "openai" else "openai"]
    return [name for name in order if IMAGE_PROVIDERS.get(name, {}).get("api_key")]


def generate_image_with_fallback(prompt, preferred="openai"):
    """Try preferred image provider, then the alternate."""
    for provider_name in pick_image_provider(preferred):
        image_b64 = generate_image(prompt, provider_name)
        if image_b64:
            return image_b64, provider_name
    return None, None


def parse_llm_json(raw):
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", cleaned)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return None


def generate_image(prompt, provider_name):
    """Generate an image with Grok or OpenAI and return base64 data."""
    provider = IMAGE_PROVIDERS.get(provider_name)
    if not provider or not provider["api_key"]:
        return None

    models = _model_candidates(provider_name, provider["model"], IMAGE_MODEL_FALLBACKS)
    for model in models:
        payload = {
            "model": model,
            "prompt": prompt[:4000],
            "n": 1,
        }
        if provider_name == "openai":
            payload["size"] = "1024x1024"
            payload["response_format"] = "b64_json"
            if model == "dall-e-3":
                payload["quality"] = "standard"
        else:
            payload["aspect_ratio"] = "4:3"
            payload["response_format"] = "b64_json"

        try:
            resp = requests.post(
                f"{provider['base_url']}/images/generations",
                headers={
                    "Authorization": f"Bearer {provider['api_key']}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()["data"][0]
            if data.get("b64_json"):
                print(f"Image LLM: {provider_name} ({model})")
                return data["b64_json"]
            if data.get("url"):
                img_resp = requests.get(data["url"], timeout=60)
                img_resp.raise_for_status()
                print(f"Image LLM: {provider_name} ({model}) via URL")
                return base64.b64encode(img_resp.content).decode("ascii")
        except Exception as e:
            print(f"Image LLM error ({provider_name}/{model}): {e} {_api_error_body(e)}")
    return None


def save_image_file(image_b64, filename):
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = IMAGE_DIR / filename
    with open(path, "wb") as f:
        f.write(base64.b64decode(image_b64))
    return f"/editions/images/{filename}"

# ─── PROMPTS ────────────────────────────────────────────────────────
PAPER_IDENTITY = """The Multiverse Gazette is a SATIRICAL newspaper — parody journalism from alternate universes across all of time, deep past to far future. Think The Onion by way of Terry Pratchett and Douglas Adams.

House comedy rules (non-negotiable):
- DEADPAN: the paper takes its ridiculous universe completely seriously. Never wink at the reader.
- SPECIFICITY is the soul of funny: invented proper nouns, precise absurd statistics, petty bureaucratic detail.
- SATIRE punches at power, money, vanity, bureaucracy, and human nature — beneath the costume, the joke is about OUR world.
- DARK is welcome (plague, collapse, doom) as long as it's witty; misery without a punchline is a failure.
- Era voice matters, but never let period diction smother a joke."""

BRIEF_PROMPT = """You are the assigning editor of The Multiverse Gazette.

{paper_identity}

Today's universe: {divergence}
Era voice: {theme} | Year: {year} | Print date: {date}
Comedic angle assigned: {angle}
{real_news_block}
{theme_guide}

Design ONE satirical front-page premise. Requirements:
- The premise itself must be a joke — not just "regular news but old-timey".
- If real-world headlines are listed above, pick ONE and let the premise obliquely mirror it in this universe. A reader should feel the wink without the real event ever being named.
- Give the story a specific comic engine: irony, escalation, bureaucratic absurdity, cosmic stakes treated as petty inconvenience, or petty stakes treated as cosmic.

Return ONLY JSON:
{{"topic": "the specific absurd subject", "comic_engine": "what makes it funny", "real_world_echo": "which real headline it mirrors and how, or null", "tone": "one-sentence tone directive", "key_details": ["specific silly detail", "another", "another"], "avoid": ["cliché to ban", "..."]}}"""

STORY_PROMPT = """You are the lead writer for The Multiverse Gazette. Write today's front-page story.

{paper_identity}

Today's universe: {divergence}
Era voice: {theme} | Year: {year}
Editor's brief: {brief}

CRITICAL RULES:
- Deadpan reporting of the absurd: the paper believes every word it prints.
- Escalate: each paragraph raises the absurdity or widens the gap between stakes and tone.
- Quotes from named officials and citizens who incriminate themselves without noticing.
- The headline must be funny on its own; the deck lands a second, different joke.
- NEVER use these phrases: {forbidden}
- Article: 4-5 paragraphs, 300-420 words, varied sentence rhythm.

{theme_guide}

Return ONLY JSON: {{"headline": "...", "deck": "...", "byline": "witty era-appropriate reporter name, with title", "article": "..."}}
Article body: separate paragraphs with \\n\\n (not HTML)."""

OPED_PROMPT = """Write the satirical op-ed column for The Multiverse Gazette, reacting to today's front page.

Main headline: {headline} — {deck}
Universe: {divergence} | Era: {theme}, year {year}

The columnist is a comic persona: pompous, confidently wrong, and personally invested. They take an absurd position on the story — defend the indefensible, blame the victims' hats, or propose a fix far worse than the problem — and accidentally reveal their own pettiness along the way.
120-160 words. Return ONLY JSON: {{"title": "...", "author": "funny name, absurd credential", "body": "..."}}"""

COMIC_STRIP_PROMPT = """You are Grok, the newspaper's sharpest satirist. Write a 3-panel comic strip about TODAY's story.

Headline: {headline}
Deck: {deck}
Theme: {theme} | Year: {year} | Divergence: {divergence}

VOICE: Bold, punchy, dry wit — like a great editorial cartoonist with no filter.
- Panel 1: hook the reader with a specific detail from the headline (not generic setup)
- Panel 2: escalate or twist — a character, bureaucrat, citizen, or authority figure digs the hole deeper
- Panel 3: HARD punchline — surprising, funny, slightly cruel or absurd. Must land.

BANNED: "Welcome to this one", "I prefer timelines where...", meta jokes about alternate history, safe filler.
Each caption: 1-2 short sentences max. Use dialogue where possible.

Return ONLY JSON:
{{"title": "strip name", "panels": [
  {{"caption": "panel 1"}},
  {{"caption": "panel 2"}},
  {{"caption": "panel 3 punchline"}}
]}}"""

JOKE_PROMPT = """You are Grok writing the Joke of the Day for an alternate-history newspaper.

Headline today: "{headline}"
Divergence: {divergence} | Theme: {theme} | Year: {year}

Write ONE joke that directly riffs on the headline or its absurd implications.
Make it BOLD and witty — dry, deadpan, or savage. Era-appropriate to {theme}.
Must be actually funny, not polite. Setup + punchline that stings a little.

BANNED: generic timeline jokes, "only time will tell", lazy puns that ignore the headline.

Return ONLY JSON: {{"setup": "...", "punchline": "...", "text": "setup + punchline as readers see it"}}"""

CLASSIFIED_PROMPT = """Write 4 classified ads for The Multiverse Gazette (satirical newspaper), theme {theme}, year {year}.
Universe: {divergence}
Today's front page: "{headline}" — {deck}

Each ad is fallout from today's story: someone selling off, hiring, seeking, confessing, or apologizing because of the events on page one. Funny, specific, slightly desperate. Era-appropriate voice. Vary the categories (For Sale, Help Wanted, Lost & Found, Personals, Public Notices, Legal...).
Return ONLY JSON array: [{{"cat": "...", "text": "1-2 sentences"}}, ...]"""

SPONSOR_ADS_PROMPT = """Write 4 bizarre, witty IN-UNIVERSE display advertisements for today's newspaper.

Theme: {theme} | Year: {year} | Date: {date}
Divergence: {divergence}
Today's headline: "{headline}"

Each ad sells a fictitious product, service, or public notice that could only exist in THIS timeline.
Make them weird, dryly funny, or unsettling — never generic "subscribe now" filler.
EVERY ad must riff on today's headline, its consequences, or the divergence — the ad section is part of the joke. Era-appropriate voice for {theme}.

Return ONLY a JSON array of exactly 4 objects:
[{{"headline": "...", "body": "1-2 sentences", "tagline": "optional fine print or disclaimer"}}, ...]"""

WEATHER_PROMPT = """Weather box for The Multiverse Gazette, {theme} universe, year {year}.
Universe: {divergence}
Today's front page: "{headline}"
Invent a city name native to this universe and a forecast condition that slyly riffs on today's story. Condition under 7 words, deadpan funny.
Return ONLY JSON: {{"city": "...", "condition": "...", "temp": 72, "high": 78, "low": 65}}"""

EDITOR_PROMPT = """You are the executive editor of The Multiverse Gazette, a satirical newspaper. Polish this entire edition.

Remove repetitive phrases, clichés, and samey tone across sections.
Each section must sound distinct. Preserve facts and JSON structure exactly.
COHESION: every section (op-ed, classifieds, comic, joke, ads, weather) must connect to the front-page story at least peripherally — sharpen any weak link so the whole paper reads as one universe reacting to one event.
COMEDY: this is parody. Punch up any joke that doesn't land; funny beats polished. If a section is earnest and mirthless, rewrite it with dry wit.
Do NOT soften, sanitize, or flatten comic_strip or joke — keep their punch and wit intact.

FORBIDDEN phrases (remove or rewrite any occurrence):
{forbidden}

Edition JSON to edit:
{content}

Return the COMPLETE revised edition as JSON with these keys:
headline, deck, article, oped, classifieds, comic_strip, joke, sponsor_ads, weather
Keep article as plain text with \\n\\n between paragraphs (not HTML tags)."""

HERO_IMAGE_PROMPT = """Editorial news photograph, {theme} era alternate history.
Year {year}. Story: {headline}. {divergence}
Dramatic photojournalism. No text or watermarks."""

COMIC_STRIP_IMAGE_PROMPT = """Three-panel newspaper comic strip, left to right, {theme} era style.
Story satire: {headline}. Panels: {panel_summary}
Funny, witty. No speech bubble text in image — visuals only."""

# ─── FALLBACK GENERATORS (no LLM) ───────────────────────────────────
def fallback_headline(rng, theme, year, divergence):
    templates = {
        "victorian": [
            ("Aether-Train Derailment Claims Twelve Souls", "Investigators suspect sabotage by Luddite saboteurs."),
            ("Queen Approves Mass Expansion of Pneumatic Post", "The ambitious project promises delivery across the Empire in mere hours."),
            ("Clockwork Constables Deployed to East-End Districts", "Critics cite their inability to navigate stairs."),
        ],
        "artdeco": [
            ("Zeppelin Completes Trans-Atlantic Crossing in Record Time", "The future of luxury travel floats three thousand feet above the Atlantic."),
            ("Stock Market Reaches Stratospheric New Heights", "Analysts warn of a bubble, but the party shows no signs of stopping."),
            ("Architect Unveils Plans for Mile-High Tower", "Skeptics call it impossible. The architect calls it Tuesday."),
        ],
        "soviet": [
            ("FIVE-YEAR PLAN EXCEEDS QUOTA BY 12%", "Comrade workers have surpassed projections in steel and concrete."),
            ("NEW MONUMENT TO LABOR UNVEILED IN VICTORY SQUARE", "The 40-meter concrete statue depicts eternal forward motion."),
            ("COSMONAUT TRAINING EXPANDS TO INCLUDE WOMEN", "The Motherland will not waste half its genius."),
        ],
        "cyberpunk": [
            ("Neural Interface Hack Exposes 2 Million Brain-Linked Users", "Victims report intrusive memories of a beach they never visited."),
            ("Synthetic Pop Star Wins Grammy, Sparks Identity Debate", "She is code. She is art. She is property of Yutani Media."),
            ("AI Mayor Elected in District 13 by Landslide", "The algorithm promised efficiency. The voters promised obedience."),
        ],
        "medieval": [
            ("The King Returns From Crusade With Relics Most Holy", "His Majesty brought back a fragment of the True Cross."),
            ("Plague Ships Spotted in the Southern Harbor", "The port authority has ordered all vessels quarantined for forty days."),
            ("Tournament to Be Held in the Meadow of Saint George", "Knights from six kingdoms have pledged their lances."),
        ],
        "atomic": [
            ("Family Builds Fallout Shelter in Backyard Over Weekend", "The Andersons say it was easier than installing a pool."),
            ("New Kitchen of the Future Unveiled at World's Fair", "Push-button cooking and a refrigerator that plans your meals."),
            ("Polio Vaccine Declared Safe After Nationwide Trials", "Dr. Salk's miracle is here. Parents are lining up around the block."),
        ],
        "vaporwave": [
            ("Mall of America Breaks Ground: The Future of Shopping is Here", "Seven acres of retail under one climate-controlled roof."),
            ("Aerobics Craze Sweeps Nation: Spandex Sales Up 400%", "Doctors warn of 'jazzercise knee,' but the leg warmers march on."),
            ("Nintendo Entertainment System Saves the Video Game Industry", "After the crash, a plumber named Mario brings joy back to living rooms."),
        ],
        "wasteland": [
            ("Water Baron Controls Last Known Well in Sector 7", "He charges ten bullets per gallon. The thirsty have no choice."),
            ("War Rig Convoy Ambushed on the Salt Flats", "The raiders took the fuel, the food, and the driver."),
            ("Old World Library Burned for Heat by Freezing Refugees", "Shakespeare, Plato, and Asimov. All gone in one night."),
        ]
    }
    h, d = rng.pick(templates.get(theme, templates["wasteland"]))

    article_paras = [
        f"Records from the {rng.pick(['Metropolitan Archives','Provincial Registry','Central Bureau'])} confirm that {h.lower()} The report cites {rng.pick([' seventeen',' forty-three',' six'])} independent witnesses.",
        f"{rng.pick(['Professor Aldous Crane','Inspector Venn','Dr. Eliza Marsh'])} told this paper the event exposes tensions long simmering beneath official calm. 'We mapped this possibility in {year - 12},' they said. 'No one wanted the map.'",
        f"In the {rng.pick(['docklands','cathedral quarter','merchant district'])}, residents described {rng.pick(['a sound like tearing silk','an unnatural green light','a silence that lasted three minutes'])}. One shopkeeper counted the incident in inventory losses rather than superlatives.",
        f"The {rng.pick(['Board of Censors','Ministry of Continuity','Office of Correct Records'])} issued a {rng.range(200, 900)}-word statement that answered no questions and raised {rng.range(2, 9)} new ones.",
        f"Tomorrow's edition will follow the money, the motive, and the one detail every spokesperson has carefully avoided naming.",
    ]
    return {"headline": h, "deck": d, "article": "\n\n".join(article_paras)}

def fallback_oped(rng, theme):
    opeds = {
        "victorian": {"title": "The Moral Cost of Automaton Labor", "author": "A Concerned Clergyman", "body": "When a man is replaced by brass and steam, what becomes of his soul? We must consider not merely the efficiency of the factory, but the dignity of the worker who once stood within it."},
        "artdeco": {"title": "The Jazz Age is Not a Rebellion, It is a Renaissance", "author": "A Modernist", "body": "We are not lost. We are merely dancing faster than our parents can hear the music. Let them catch up, or let them stay behind."},
        "soviet": {"title": "THE INDIVIDUAL IS A MYTH", "author": "A Worker-Philosopher", "body": "The West speaks of personal freedom. We speak of collective strength. One man is weak. One million men, moving in the same direction, are an unstoppable machine."},
        "cyberpunk": {"title": "Your Body is a Rental", "author": "A Ghost in the Machine", "body": "You do not own your liver. You do not own your eyes. You lease them from a corporation that can repossess them at any time. Read the fine print."},
        "medieval": {"title": "The Peasant's Lot is God's Lot", "author": "A Parish Priest", "body": "To question one's station is to question the divine order. The ploughman serves as surely as the king, and both shall have their reward in heaven."},
        "atomic": {"title": "Why Every American Family Needs a Bomb Shelter", "author": "A Concerned Father", "body": "It is not paranoia. It is prudence. When the sirens sound, you will not have time to dig. You will have time to thank yourself."},
        "vaporwave": {"title": "The Mall is the New Town Square", "author": "A Consumer Advocate", "body": "Where else can you buy sneakers, eat a pretzel, and see a movie? The agora is dead. Long live the food court."},
        "wasteland": {"title": "The Old World Deserved to Die", "author": "A Survivor", "body": "They had everything and wasted it. We have nothing and cherish it. Perhaps the fire was a mercy."}
    }
    return opeds.get(theme, opeds["wasteland"])

def fallback_classifieds(rng, theme):
    all_ads = {
        "victorian": [
            {"cat": "For Sale", "text": "Gently used difference engine. Minor gear slippage. 15 guineas."},
            {"cat": "Employment", "text": "Experienced aether-rigger sought. Must supply own goggles."},
            {"cat": "Housing", "text": "Rooms in Pneumatic District. Constant vibration. Not for nervous constitutions."},
            {"cat": "Services", "text": "Professional exorcist for haunted automata. Discretion assured."}
        ],
        "artdeco": [
            {"cat": "For Sale", "text": "1926 Stutz Bearcat. Low mileage. The color of midnight and regret."},
            {"cat": "Employment", "text": "Cigarette girl wanted. Must know bourbon from scotch."},
            {"cat": "Housing", "text": "Penthouse with view of Zephyr Terminal. $200/month."},
            {"cat": "Services", "text": "Professional bootlegger. Discreet delivery. Premium Canadian stock."}
        ],
        "soviet": [
            {"cat": "Available", "text": "One-room apartment, Sector 4. Concrete walls. Shared bath."},
            {"cat": "Employment", "text": "Steelworker needed for Foundry 7. Housing included."},
            {"cat": "Exchange", "text": "Two tickets to State Ballet. Will trade for fresh fruit."},
            {"cat": "Services", "text": "Licensed mechanic for state vehicles. Will repair bicycles for barter."}
        ],
        "cyberpunk": [
            {"cat": "For Sale", "text": "Black-market neural jack. Slightly used. No warranty. Cash only."},
            {"cat": "Employment", "text": "Runner needed. Package delivery. No questions. 10K credits."},
            {"cat": "Housing", "text": "Capsule pod in The Stack. Shared bathroom. VR included."},
            {"cat": "Services", "text": "Memory editing. Remove trauma, exes, or Tuesday. Discreet."}
        ],
        "medieval": [
            {"cat": "For Sale", "text": "Three sheep, two goats, and a mule. Will trade for iron tools."},
            {"cat": "Employment", "text": "Squire needed. Must carry a lance and recite the Lord's Prayer."},
            {"cat": "Housing", "text": "Room above the tavern. Shared with rats. Mead included."},
            {"cat": "Services", "text": "Indulgences transcribed. Fast service. Papal seal guaranteed."}
        ],
        "atomic": [
            {"cat": "For Sale", "text": "1953 Chevrolet Bel Air. Two-tone. Low miles. The American dream on four wheels."},
            {"cat": "Employment", "text": "Salesman wanted for vacuum demonstration. Must own car and smile."},
            {"cat": "Housing", "text": "3-bedroom ranch. White picket fence included. $12,000."},
            {"cat": "Services", "text": "Fallout shelter construction. Family rates. Government approved."}
        ],
        "vaporwave": [
            {"cat": "For Sale", "text": "1984 Camaro IROC-Z. T-tops. 8-track player included. $8,500."},
            {"cat": "Employment", "text": "Mall security guard. Must ride Segway and look intimidating."},
            {"cat": "Housing", "text": "Studio with pool access. Walk to beach. Pastel colors mandatory."},
            {"cat": "Services", "text": "Professional mixtape curator. Romance, workout, or road trip."}
        ],
        "wasteland": [
            {"cat": "For Sale", "text": "Pre-war can opener. Rusted but functional. Two bullets or best offer."},
            {"cat": "Employment", "text": "Scavenger needed for deep-ruin expedition. Must bring own gas mask."},
            {"cat": "Housing", "text": "Lean-to near the old highway. Wind protection minimal. No raiders currently."},
            {"cat": "Services", "text": "Radiation detox. 50% success rate. Payment upfront. No refunds."}
        ]
    }
    return all_ads.get(theme, all_ads["wasteland"])


def fallback_comic_strip(rng, headline, theme):
    hook = headline.split(":")[0][:50]
    strips = {
        "victorian": {
            "title": "The Morning Room",
            "panels": [
                {"caption": f"'Did you see the papers? {hook}.'"},
                {"caption": "'I saw them. I also saw our tea get cold while you read them aloud.'"},
                {"caption": "'Progress, dear. The Empire waits for no one — except apparently us.'"},
            ],
        },
        "cyberpunk": {
            "title": "Error 404: Dignity",
            "panels": [
                {"caption": f"NEWS ALERT: {hook[:40]}..."},
                {"caption": "Great. Another headline my neural feed will monetize."},
                {"caption": "At least the apocalypse accepts crypto now."},
            ],
        },
        "soviet": {
            "title": "COMRADE COMIC",
            "panels": [
                {"caption": f"TODAY'S HEADLINE: {hook.upper()[:35]}"},
                {"caption": "Is this good for the plan?"},
                {"caption": "It is now. Adjust your memory accordingly."},
            ],
        },
    }
    default = {
        "title": "Local Reactions",
        "panels": [
            {"caption": f"So. {hook}."},
            {"caption": "And they printed that above the fold like it was normal."},
            {"caption": "In this timeline? That's the normal part."},
        ],
    }
    return strips.get(theme, default)


def fallback_joke(rng, headline, theme):
    hook = headline.split(":")[0][:45]
    jokes = {
        "victorian": {"setup": f"What's the proper response to '{hook}'?", "punchline": "Write a strongly worded letter and die of exposure on the way to post it.", "text": f"What's the proper response to '{hook}'? Write a strongly worded letter and die of exposure on the way to post it."},
        "cyberpunk": {"setup": f"My feed just pushed '{hook}' into my skull.", "punchline": "I tried to unsubscribe. The headline subscribed to me.", "text": f"My feed just pushed '{hook}' into my skull. I tried to unsubscribe. The headline subscribed to me."},
        "soviet": {"setup": "Why did the newspaper run this story?", "punchline": "Because the alternative was running the truth.", "text": "Why did the newspaper run this story? Because the alternative was running the truth."},
        "wasteland": {"setup": "What's the difference between today's headline and clean water?", "punchline": "You can trade the headline for bullets.", "text": "What's the difference between today's headline and clean water? You can trade the headline for bullets."},
    }
    default = {"setup": f"How do you explain '{hook}' to a visitor from another timeline?", "punchline": "You don't. You hand them the paper and back away slowly.", "text": f"How do you explain '{hook}' to a visitor from another timeline? You don't. You hand them the paper and back away slowly."}
    return jokes.get(theme, default)


def normalize_classifieds(raw, rng=None, theme=None):
    """Coerce LLM output into a list of classified ad dicts."""
    if not raw:
        return fallback_classifieds(rng, theme) if rng and theme else []
    if isinstance(raw, dict):
        for key in ("ads", "classifieds", "items"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
    if not isinstance(raw, list):
        return fallback_classifieds(rng, theme) if rng and theme else []
    ads = [item for item in raw if isinstance(item, dict) and item.get("cat") and item.get("text")]
    return ads if ads else (fallback_classifieds(rng, theme) if rng and theme else [])


def normalize_sponsor_ads(raw, rng=None, theme=None, headline="", divergence=""):
    """Coerce LLM output into a list of 4 ad dicts."""
    if not raw:
        return fallback_sponsor_ads(rng, theme, headline, divergence) if rng and theme else []

    if isinstance(raw, dict):
        for key in ("ads", "advertisements", "sponsor_ads", "items"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        if isinstance(raw, dict) and raw.get("headline") and raw.get("body"):
            raw = [raw]

    if not isinstance(raw, list):
        return fallback_sponsor_ads(rng, theme, headline, divergence) if rng and theme else []

    ads = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        headline_text = item.get("headline") or item.get("title")
        body_text = item.get("body") or item.get("text")
        if headline_text and body_text:
            ad = {"headline": str(headline_text).strip(), "body": str(body_text).strip()}
            if item.get("tagline"):
                ad["tagline"] = str(item["tagline"]).strip()
            ads.append(ad)

    if len(ads) < 4 and rng and theme:
        extras = fallback_sponsor_ads(rng, theme, headline, divergence)
        for extra in extras:
            if len(ads) >= 4:
                break
            if not any(a["headline"] == extra["headline"] for a in ads):
                ads.append(extra)
    return ads[:4]


def fallback_sponsor_ads(rng, theme, headline="", divergence=""):
    """Weird in-universe ads tied to theme and today's story."""
    hook = (headline.split(":")[0] if headline else "today's news")[:48]
    div_hook = divergence.split(" in ")[0][:40] if divergence else "this timeline"

    pools = {
        "victorian": [
            {"headline": "Wellington & Crane Patent Mourning Brass", "body": f"Commemorate {hook.lower()} with a clockwork lapel that weeps one calibrated tear per hour. Batteries: your dignity.", "tagline": "Not suitable for optimists."},
            {"headline": "Pneumatic Post — Same-Day Regret Delivery", "body": "Send your ill-advised telegram before the evening edition lands. We cannot un-read what you wrote.", "tagline": "Four-minute guarantee or we eat the stamp."},
            {"headline": "Dr. Morley's Aetheric Tonic", "body": f"Recommended after reading about {hook.lower()}. Restores color to cheeks and faith in progress.", "tagline": "Contains no aether. Contains hope."},
            {"headline": "Brass & Sons Automata — Lease a Grief Butler", "body": "When society closes its doors, ours opens a silver tray. Polite, silent, slightly judgmental.", "tagline": "Ethics sold separately."},
        ],
        "artdeco": [
            {"headline": "Champagne Airways — Fly Above the Headlines", "body": f"Why dwell on {hook.lower()} at sea level? Our zeppelin lounge serves denial with a twist.", "tagline": "Black tie required. Bad news optional."},
            {"headline": "The Gilded Distraction Club", "body": "Members enjoy jazz, oysters, and selective amnesia about whatever the papers are screaming today.", "tagline": "Apply before the bubble pops."},
            {"headline": "Sterling & Luxe — Crisis Cufflinks", "body": f"Commemorate {div_hook} in 14-karat denial. Engraving available.", "tagline": "Past performance guarantees nothing."},
            {"headline": "Art Deco Earplugs by Maison Silencio", "body": "Hear only what flatters you. Now in onyx, ivory, and willful ignorance.", "tagline": "Sold by the pair. Denial sold separately."},
        ],
        "soviet": [
            {"headline": "TRUST THE PLAN — OFFICIAL CALENDAR", "body": f"Today's headline about {hook.lower()} was always part of the five-year plan. Purchase calendar. Adjust memory.", "tagline": "Dates subject to revision."},
            {"headline": "STATE APPROVED COMFORT RATIONS", "body": "Bread that tastes like certainty. One loaf per household, two if you smile correctly.", "tagline": "Queues form to the left of history."},
            {"headline": "WORKER'S PARADOX VODKA", "body": f"Celebrate {div_hook} with a drink that builds character and forgets yesterday's newspaper.", "tagline": "Collective hangovers only."},
            {"headline": "BUREAU OF CORRECT FEELINGS", "body": "Distressed by today's news? We will reassign your reaction to a more productive emotion.", "tagline": "Gratitude is mandatory."},
        ],
        "cyberpunk": [
            {"headline": "NeuroLink™ — Mute Today's Trauma Tier", "body": f"Premium subscribers can skip emotional processing of {hook.lower()}. Side effects include apathy and brand loyalty.", "tagline": "Memories not included."},
            {"headline": "CorpSec Identity Insurance", "body": "When the feed hacks your brain, we hack back. Terms unreadable by design.", "tagline": "Your self is a subscription."},
            {"headline": "SynthMeal Block 7 — Tastes Like Denial", "body": f"Nutrients calibrated for citizens living through {div_hook}. Legally chicken.", "tagline": "May contain spoilers."},
            {"headline": "AdBlock for Real Life™", "body": "Blur billboards, exes, and inconvenient headlines. Reality still bills monthly.", "tagline": "Free trial ends when you blink."},
        ],
        "medieval": [
            {"headline": "Brother Aldric's Indulgences — Now in Bulk", "body": f"Disturbed by talk of {hook.lower()}? Purchase remission before vespers. God's paperwork backlog is your opportunity.", "tagline": "Heaven accepts cash."},
            {"headline": "Ye Olde Plague Insurance", "body": "If the ships in the harbor cough, we cough up nothing. But the parchment looks official.", "tagline": "Forty days, forty deniers."},
            {"headline": "CastleMoat Maintenance Co.", "body": f"When {div_hook}, you will want a deeper moat and a shallower conscience.", "tagline": "Serfs not included."},
            {"headline": "Miracle Relic Replicas", "body": "Own a fragment of something holy enough to ignore today's tidings.", "tagline": "Saints may differ by region."},
        ],
        "atomic": [
            {"headline": "Anderson Family Fallout Shelters", "body": f"Read about {hook.lower()}? Build one this weekend. Easier than a pool. More fun than panic.", "tagline": "Government approved-ish."},
            {"headline": "Kitchen of Tomorrow — Panic in Pastel", "body": "Push-button cooking while the sirens practice. The fridge plans meals; you plan escape routes.", "tagline": "As seen at the fair."},
            {"headline": "Cheerful Geiger Counters for Kids", "body": "Turn anxiety into a game the whole block can play. Winner gets the iodine.", "tagline": "Batteries and bravery not included."},
            {"headline": "Subliminal Patriotism Records", "body": f"Sleep soundly despite {div_hook}. Side A: optimism. Side B: louder optimism.", "tagline": "Not responsible for dreams."},
        ],
        "vaporwave": [
            {"headline": "Mall Food Court Eternal Pass", "body": f"Today's headline? {hook}. Our specials? Forever. Sampler platters of nostalgia, no exit required.", "tagline": "Sodium levels transcend time."},
            {"headline": "Aerobics Against Anxiety™", "body": "Leg warmers for your feelings. Jazzercise the dread away under fluorescent paradise.", "tagline": "Spandex sales up 400%."},
            {"headline": "Synthwave Sleep Therapy Tapes", "body": f"Drift off to pink sunsets and pretend {div_hook} was just a mixtape label.", "tagline": "Side effects: mall ghosts."},
            {"headline": "Limited Edition Headline T-Shirt", "body": "Wear today's catastrophe in pastel serif. Collect all eight timelines.", "tagline": "Shrinkage guaranteed."},
        ],
        "wasteland": [
            {"headline": "Bullet Water — Two Rounds a Sip", "body": f"Heard about {hook.lower()}? Hydrate anyway. The well doesn't accept excuses.", "tagline": "No refunds across dimensions."},
            {"headline": "War Rig Extended Warranty", "body": "When the convoy dies on the salt flats, die insured. We can't help, but we can invoice.", "tagline": "Rust is a pre-existing condition."},
            {"headline": "Pre-War Library Kindling", "body": "Shakespeare burns long. Plato, medium. Today's news, instant.", "tagline": "Literature sold by the warmth."},
            {"headline": "Rad-X and Regret", "body": f"Living through {div_hook}? Our detox has a 50% success rate and 100% upfront payment.", "tagline": "Survivors may disagree."},
        ],
    }

    default = [
        {"headline": "Multiverse Gazette — Extra Edition Ink", "body": f"Today's report on {hook.lower()} printed on paper that remembers other timelines.", "tagline": "Smudges may be prophecies."},
        {"headline": "Chrono-Insurance for Readers", "body": "If this divergence disappoints, we insure your sense of surprise.", "tagline": "Claims handled elsewhere."},
        {"headline": "Parallel Classifieds Hotline", "body": "Sell your alternate self's unwanted goods. Fraud is tradition.", "tagline": "Dial NOW-ish."},
        {"headline": "Public Notice: Reality Adjacent", "body": f"Products guaranteed authentic to a universe where {div_hook}.", "tagline": "Your mileage in parsecs."},
    ]
    pool = pools.get(theme, default)
    if rng:
        return [rng.pick(pool) for _ in range(4)]
    return pool[:4]


def article_to_html(article):
    """Convert plain-text article paragraphs to HTML."""
    if "<p>" in article:
        return article
    paras = [p.strip() for p in article.split("\n\n") if p.strip()]
    return "".join(f"<p>{p}</p>" for p in paras)


def run_editor_pass(content, editor_provider):
    """Editor AI polishes all text sections for variety and removes clichés."""
    draft = {
        "headline": content.get("headline"),
        "deck": content.get("deck"),
        "article": content.get("article", "").replace("<p>", "").replace("</p>", "\n\n"),
        "oped": content.get("oped"),
        "classifieds": content.get("classifieds"),
        "comic_strip": content.get("comic_strip"),
        "joke": content.get("joke"),
        "sponsor_ads": content.get("sponsor_ads"),
        "weather": content.get("weather"),
    }

    prompt = EDITOR_PROMPT.format(
        forbidden=", ".join(FORBIDDEN_PHRASES[:12]),
        content=json.dumps(draft, indent=2),
    )
    revised, used = llm_json_with_fallback(prompt, "editor", max_tokens=2500, temperature=0.4)
    if not revised:
        if editor_provider:
            revised = parse_llm_json(generate_with_llm(prompt, editor_provider, max_tokens=2500, temperature=0.4, json_mode=True))
            used = editor_provider
    if not revised:
        return content

    label = TEXT_PROVIDERS.get(used or editor_provider, {}).get("label", "Editor")
    print(f"  Editor pass: {label}")
    for key in ("headline", "deck", "article", "oped", "classifieds", "comic_strip", "joke", "sponsor_ads", "weather"):
        if revised.get(key):
            content[key] = revised[key]
    if content.get("article") and "<p>" not in content["article"]:
        content["article"] = article_to_html(content["article"])
    return content


# ─── MAIN GENERATION ────────────────────────────────────────────────
def generate_edition(date=None, timeline_id=None, with_images=None):
    if date is None:
        date = datetime.now(timezone.utc)
    if with_images is None:
        with_images = GENERATE_IMAGES

    if timeline_id is None:
        timeline_id = (date.timetuple().tm_yday % len(THEMES)) + 1

    seed = timeline_id * 1000000 + date.year * 10000 + date.month * 100 + date.day
    rng = SeededRandom(seed)
    theme = THEMES[(timeline_id - 1) % len(THEMES)]
    # A random year within the theme's era — every edition is a different
    # universe on a different date, from antiquity to the deep future.
    era_min, era_max = THEME_ERAS[theme]
    year = rng.range(era_min, era_max)
    divergence = rng.pick(DIVERGENCES)
    angle = rng.pick(STORY_ANGLES)
    date_slug = date.strftime("%Y-%m-%d")
    forbidden = ", ".join(FORBIDDEN_PHRASES)

    real_headlines = fetch_real_headlines()
    real_news_block = ""
    if real_headlines:
        real_news_block = "Real-world headlines today (satirical fuel — mirror ONE obliquely):\n" + \
            "\n".join(f"- {h}" for h in real_headlines) + "\n"

    editor_p = resolve_role("editor")
    story_p = resolve_role("story")
    humor_p = resolve_role("humor")
    structure_p = resolve_role("structure")
    used_providers = {}

    print(f"Timeline {timeline_id} ({theme}): story={story_p}, humor={humor_p}, editor={editor_p}")

    ctx = {"divergence": divergence, "theme": theme, "year": year,
           "date": date.strftime("%B %d, %Y"), "angle": angle, "theme_guide": THEME_GUIDE,
           "forbidden": forbidden, "paper_identity": PAPER_IDENTITY,
           "real_news_block": real_news_block}

    # 1. Editor assigns story brief
    brief, used = llm_json_with_fallback(BRIEF_PROMPT.format(**ctx), "editor", temperature=0.7)
    if used:
        used_providers["brief"] = used
    brief_text = json.dumps(brief) if brief else f"angle: {angle}, theme: {theme}"

    # 2. Moonshot (story) writes main article — falls back to OpenAI/Grok on API failure
    headline_data, used = llm_json_with_fallback(
        STORY_PROMPT.format(brief=brief_text, **ctx), "story", max_tokens=1200)
    if used:
        used_providers["story"] = used
    if not headline_data:
        headline_data = fallback_headline(rng, theme, year, divergence)
    headline_data["article"] = article_to_html(headline_data.get("article", ""))

    ctx["headline"] = headline_data["headline"]
    ctx["deck"] = headline_data["deck"]

    # 3. Grok (humor) — op-ed with fallback; comic + joke Grok-only for bold wit
    oped_data, used = llm_json_with_fallback(OPED_PROMPT.format(**ctx), "humor")
    if used:
        used_providers["oped"] = used
    if not oped_data:
        oped_data = fallback_oped(rng, theme)

    comic_strip, used = llm_json_grok(COMIC_STRIP_PROMPT.format(**ctx), max_tokens=900)
    if used:
        used_providers["comic"] = used
    if not comic_strip or not comic_strip.get("panels"):
        comic_strip = fallback_comic_strip(rng, headline_data["headline"], theme)

    joke, used = llm_json_grok(JOKE_PROMPT.format(**ctx), max_tokens=400)
    if used:
        used_providers["joke"] = used
    if not joke:
        joke = fallback_joke(rng, headline_data["headline"], theme)

    # 4. OpenAI (structure) — classifieds, weather, sponsor ads
    classifieds_data, used = llm_json_with_fallback(CLASSIFIED_PROMPT.format(**ctx), "structure")
    if used:
        used_providers["classifieds"] = used
    classifieds_data = normalize_classifieds(classifieds_data, rng, theme)

    sponsor_ads, used = llm_json_with_fallback(SPONSOR_ADS_PROMPT.format(**ctx), "structure")
    if used:
        used_providers["sponsor_ads"] = used
    sponsor_ads = normalize_sponsor_ads(
        sponsor_ads, rng, theme, headline_data["headline"], divergence)

    weather_data, used = llm_json_with_fallback(WEATHER_PROMPT.format(**ctx), "structure", temperature=0.7)
    if used:
        used_providers["weather"] = used
    if not weather_data:
        weather_data = {"city": rng.pick(["The Capital", "New London", "Metropolis", "Neo-Tokyo",
                        "King's Landing", "Springfield", "Miami Vice", "New Eden"]),
                        "condition": rng.pick(["Clear", "Foggy", "Stormy", "Bright"]),
                        "temp": rng.range(15, 95), "high": 0, "low": 0}
        weather_data["high"] = weather_data["temp"] + rng.range(3, 10)
        weather_data["low"] = weather_data["temp"] - rng.range(3, 10)

    content = {
        "headline": headline_data["headline"],
        "deck": headline_data["deck"],
        "article": headline_data["article"],
        "oped": oped_data,
        "classifieds": classifieds_data,
        "comic_strip": comic_strip,
        "joke": joke,
        "sponsor_ads": sponsor_ads,
        "weather": weather_data,
    }

    # 5. Editor polishes everything
    content = run_editor_pass(content, editor_p)
    content["classifieds"] = normalize_classifieds(content.get("classifieds"), rng, theme)
    content["sponsor_ads"] = normalize_sponsor_ads(
        content.get("sponsor_ads"), rng, theme, content["headline"], divergence)

    # 6. Images — OpenAI for hero photo, Grok for comic strip
    hero_image = hero_image_provider = None
    strip_image = strip_image_provider = None

    if with_images:
        hero_b64, hero_image_provider = generate_image_with_fallback(
            HERO_IMAGE_PROMPT.format(theme=theme, year=year,
                headline=content["headline"], divergence=divergence),
            preferred="openai",
        )
        if hero_b64:
            hero_image = save_image_file(hero_b64, f"{date_slug}-{timeline_id}-hero.png")

        comic_b64, strip_image_provider = generate_image_with_fallback(
            COMIC_STRIP_IMAGE_PROMPT.format(theme=theme, headline=content["headline"],
                panel_summary=" | ".join(p.get("caption", "")[:60] for p in content["comic_strip"].get("panels", [])[:3])),
            preferred="grok",
        )
        if comic_b64:
            strip_image = save_image_file(comic_b64, f"{date_slug}-{timeline_id}-strip.png")

    if strip_image:
        content["comic_strip"]["image"] = strip_image
        content["comic_strip"]["image_provider"] = strip_image_provider

    roles = []
    story_used = used_providers.get("story", story_p)
    humor_used = used_providers.get("comic") or used_providers.get("joke") or used_providers.get("oped") or humor_p
    editor_used = editor_p
    if story_used:
        roles.append(f"Story: {TEXT_PROVIDERS[story_used]['label']}")
    if humor_used:
        roles.append(f"Humor: {TEXT_PROVIDERS[humor_used]['label']}")
    if editor_used:
        roles.append(f"Editor: {TEXT_PROVIDERS[editor_used]['label']}")

    edition = {
        "timeline_id": timeline_id,
        "theme": theme,
        "date": date_slug,
        "date_display": date.strftime("%A, %B %d, %Y"),
        "year": year,
        "divergence": divergence,
        "story_angle": angle,
        "roles": roles,
        "headline": content["headline"],
        "deck": content["deck"],
        "article": content["article"],
        "author": headline_data.get("byline") or rng.pick(["Staff Correspondent", "Special Reporter", "Foreign Bureau", "Local Editor"]),
        "city": content["weather"].get("city", weather_data["city"]),
        "weather": content["weather"],
        "oped": content["oped"],
        "classifieds": content["classifieds"],
        "comic_strip": content["comic_strip"],
        "joke": content["joke"],
        "sponsor_ads": content["sponsor_ads"],
        "real_world_echo": (brief or {}).get("real_world_echo"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if hero_image:
        edition["hero_image"] = hero_image
        edition["hero_image_provider"] = hero_image_provider

    return edition

# ─── FILE I/O ───────────────────────────────────────────────────────
def save_edition(edition):
    OUTPUT_DIR.mkdir(exist_ok=True)
    filename = OUTPUT_DIR / f"{edition['date']}-{edition['timeline_id']}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(edition, f, indent=2, ensure_ascii=False)
    print(f"Saved: {filename}")
    return filename

def edition_files_by_date():
    """Return {date_str: [Path, ...]} for all edition JSON files, sorted by date."""
    by_date = {}
    for f in OUTPUT_DIR.glob("*.json"):
        parts = f.stem.split("-")
        if len(parts) >= 4:
            by_date.setdefault("-".join(parts[:3]), []).append(f)
    return dict(sorted(by_date.items()))

def cleanup_old_editions():
    """Keep only the last MAX_ARCHIVE_DAYS days of editions (8 files per day)."""
    by_date = edition_files_by_date()
    for date_str in list(by_date.keys())[:-MAX_ARCHIVE_DAYS]:
        for old in by_date[date_str]:
            stem = old.stem
            old.unlink()
            print(f"Removed old: {old.name}")
            for img in IMAGE_DIR.glob(f"{stem}-*.png"):
                img.unlink()
                print(f"Removed old image: {img.name}")

def generate_manifest():
    """Write editions/manifest.json — an index the frontend uses for the archive and day navigation."""
    dates = {}
    for date_str, files in edition_files_by_date().items():
        entries = []
        for f in sorted(files, key=lambda p: int(p.stem.split("-")[3])):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    ed = json.load(fh)
            except (json.JSONDecodeError, OSError):
                continue
            entries.append({
                "timeline_id": ed.get("timeline_id"),
                "theme": ed.get("theme"),
                "year": ed.get("year"),
                "headline": ed.get("headline"),
                "deck": ed.get("deck"),
                "divergence": ed.get("divergence"),
                "hero_image": ed.get("hero_image"),
            })
        if entries:
            dates[date_str] = entries
    manifest = {"generated_at": datetime.now(timezone.utc).isoformat(), "dates": dates}
    with open(OUTPUT_DIR / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)
    print("Generated editions/manifest.json")

def generate_sitemap():
    """Generate sitemap.xml for SEO."""
    from xml.sax.saxutils import escape

    urls = [("https://multiversegazette.com/", None)]
    for date_str, files in edition_files_by_date().items():
        for f in sorted(files, key=lambda p: int(p.stem.split("-")[3])):
            timeline = f.stem.split("-")[3]
            urls.append((f"https://multiversegazette.com/?timeline={timeline}&date={date_str}", date_str))

    sitemap = ['<?xml version="1.0" encoding="UTF-8"?>']
    sitemap.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for url, lastmod in urls:
        lastmod_tag = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        sitemap.append(f"  <url><loc>{escape(url)}</loc>{lastmod_tag}<changefreq>daily</changefreq><priority>0.8</priority></url>")
    sitemap.append("</urlset>")

    with open("sitemap.xml", "w", encoding="utf-8") as f:
        f.write("\n".join(sitemap))
    print("Generated sitemap.xml")

def generate_rss():
    """Generate RSS feed for aggregators."""
    from xml.sax.saxutils import escape

    files = sorted(OUTPUT_DIR.glob("*.json"), reverse=True)
    files = [f for f in files if f.name != "manifest.json"][:10]
    items = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            ed = json.load(fh)
        url = escape(f"https://multiversegazette.com/?timeline={ed['timeline_id']}&date={ed['date']}")
        pub_date = datetime.strptime(ed["date"], "%Y-%m-%d").strftime("%a, %d %b %Y 00:00:00 GMT")
        items.append(f"""<item>
      <title>{escape(ed['headline'])}</title>
      <link>{url}</link>
      <description>{escape(ed['deck'])}</description>
      <pubDate>{pub_date}</pubDate>
      <guid>{url}</guid>
    </item>""")

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>The Multiverse Gazette</title>
    <link>https://multiversegazette.com</link>
    <description>Daily newspaper from alternate timelines</description>
    <language>en</language>
    {chr(10).join(items)}
  </channel>
</rss>"""

    with open("rss.xml", "w", encoding="utf-8") as f:
        f.write(rss)
    print("Generated rss.xml")

def prerender_index(edition):
    """Bake the day's lead edition into index.html so crawlers and no-JS readers
    see real content instead of 'Loading...'. The frontend re-renders the same
    edition on load, so there's no visible flash."""
    path = Path("index.html")
    if not path.exists():
        return
    html = path.read_text(encoding="utf-8")
    site = "https://multiversegazette.com"
    url = f"{site}/?timeline={edition['timeline_id']}&date={edition['date']}"

    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    warnings = []
    def sub(pattern, replacement, name):
        nonlocal html
        new, n = re.subn(pattern, lambda m: replacement, html, count=1, flags=re.S)
        if n == 0:
            warnings.append(name)
        html = new

    title = f"{edition['headline']} | The Multiverse Gazette"
    desc = f"{edition['deck']} — A satirical dispatch from a universe where {edition['divergence']}."
    hero = edition.get("hero_image")
    og_image = f"{site}{hero}" if hero else f"{site}/og-image.jpg"
    theme_label = edition["theme"].capitalize()

    sub(r"<title>.*?</title>", f"<title>{esc(title)}</title>", "title")
    sub(r'<meta name="description" content=".*?">',
        f'<meta name="description" content="{esc(desc)}">', "meta description")
    sub(r'<meta property="og:title" content=".*?">',
        f'<meta property="og:title" content="{esc(edition["headline"])}">', "og:title")
    sub(r'<meta property="og:description" content=".*?">',
        f'<meta property="og:description" content="{esc(edition["deck"])}">', "og:description")
    sub(r'<meta property="og:image" content=".*?">',
        f'<meta property="og:image" content="{esc(og_image)}">', "og:image")
    sub(r'<meta property="og:url" content=".*?">',
        f'<meta property="og:url" content="{esc(url)}">', "og:url")
    sub(r'<link rel="canonical" href=".*?">',
        f'<link rel="canonical" href="{esc(url)}">', "canonical")
    sub(r'<body data-theme="[^"]*">', f'<body data-theme="{edition["theme"]}">', "body theme")
    sub(r'<span id="breadcrumb-date">.*?</span>',
        f'<span id="breadcrumb-date">{esc(edition["date_display"])}</span>', "breadcrumb date")
    sub(r'<span id="breadcrumb-timeline">.*?</span>',
        f'<span id="breadcrumb-timeline">Timeline {edition["timeline_id"]}</span>', "breadcrumb timeline")
    sub(r'<div class="masthead-subtitle" id="masthead-subtitle">.*?</div>',
        f'<div class="masthead-subtitle" id="masthead-subtitle">Alternate Earths, Faithfully Misreported — {theme_label}</div>', "masthead subtitle")
    sub(r'<div class="masthead-date" id="masthead-date">.*?</div>',
        f'<div class="masthead-date" id="masthead-date">{esc(edition["date_display"])}, Year {edition["year"]}</div>', "masthead date")
    sub(r'<div class="masthead-divergence" id="masthead-divergence">.*?</div>',
        f'<div class="masthead-divergence" id="masthead-divergence">Divergence: {esc(edition["divergence"])}</div>', "masthead divergence")
    sub(r'<h2 class="headline-main" id="headline-main">.*?</h2>',
        f'<h2 class="headline-main" id="headline-main">{esc(edition["headline"])}</h2>', "headline")
    sub(r'<p class="headline-deck" id="headline-deck">.*?</p>',
        f'<p class="headline-deck" id="headline-deck">{esc(edition["deck"])}</p>', "deck")
    if hero:
        sub(r'<img class="hero-image[^"]*" id="hero-image"[^>]*>',
            f'<img class="hero-image visible" id="hero-image" src="{esc(hero)}" alt="{esc(edition["headline"])}" loading="lazy">', "hero image")
    else:
        sub(r'<img class="hero-image[^"]*" id="hero-image"[^>]*>',
            '<img class="hero-image" id="hero-image" alt="" loading="lazy">', "hero image")
    sub(r'<span id="author-name">.*?</span>',
        f'<span id="author-name">{esc(edition.get("author", "Staff Correspondent"))}</span>', "author")
    sub(r'<span id="dateline">.*?</span>',
        f'<span id="dateline">{esc(edition.get("city", "The Capital"))}</span>', "dateline")
    sub(r'<div class="article-body" id="article-body">.*?</div>',
        f'<div class="article-body" id="article-body">{edition["article"]}</div>', "article body")

    sd = {
        "@context": "https://schema.org", "@type": "NewsArticle",
        "headline": edition["headline"], "description": edition["deck"],
        "url": url, "datePublished": edition["date"] + "T00:00:00Z",
        "author": {"@type": "Person", "name": edition.get("author", "Staff Correspondent")},
        "publisher": {"@type": "Organization", "name": "The Multiverse Gazette",
                      "logo": {"@type": "ImageObject", "url": f"{site}/logo.png"}},
    }
    if hero:
        sd["image"] = [og_image]
    sd_script = f'<script type="application/ld+json" id="structured-data">{json.dumps(sd, ensure_ascii=False)}</script>'
    if re.search(r'<script type="application/ld\+json" id="structured-data">', html):
        sub(r'<script type="application/ld\+json" id="structured-data">.*?</script>', sd_script, "json-ld")
    else:
        sub(r"</head>", sd_script + "\n</head>", "head close")

    path.write_text(html, encoding="utf-8")
    if warnings:
        print(f"Prerender warnings (no match): {', '.join(warnings)}")
    print(f"Prerendered index.html with: {edition['headline'][:60]}")

# ─── CLI ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate Multiverse Gazette edition")
    parser.add_argument("--date", help="Date to generate for (YYYY-MM-DD)")
    parser.add_argument("--timeline", type=int, help="Timeline ID")
    parser.add_argument("--all", action="store_true", help="Generate for all 8 themes for today")
    parser.add_argument("--no-images", action="store_true", help="Skip AI image generation")
    args = parser.parse_args()

    with_images = GENERATE_IMAGES and not args.no_images

    if args.date:
        date = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        date = datetime.now(timezone.utc)

    if args.all:
        for tid in range(1, 9):
            ed = generate_edition(date, tid, with_images=with_images)
            save_edition(ed)
    else:
        ed = generate_edition(date, args.timeline, with_images=with_images)
        save_edition(ed)

    cleanup_old_editions()
    generate_manifest()
    generate_sitemap()
    generate_rss()

    # Bake the day's default edition into index.html for SEO / no-JS readers
    default_tid = (date.timetuple().tm_yday % len(THEMES)) + 1
    lead_file = OUTPUT_DIR / f"{date.strftime('%Y-%m-%d')}-{default_tid}.json"
    if lead_file.exists():
        with open(lead_file, encoding="utf-8") as f:
            prerender_index(json.load(f))

    print("\nDone.")
