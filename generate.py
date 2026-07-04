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
    "investigative exposé uncovering a hidden scandal",
    "intimate human-interest profile of an ordinary person caught in events",
    "triumphant breakthrough celebrated by the establishment",
    "somber obituary for a lost era or institution",
    "absurdist satire treating catastrophe with deadpan bureaucracy",
    "propaganda triumph narrated with unsettling sincerity",
    "technical deep-dive explaining the mechanics of the divergence",
    "eyewitness dispatch from the scene with vivid sensory detail",
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
    "Babbage completed the Analytical Engine in 1840",
    "The Great Depression never occurred",
    "The Romanovs survived the revolution",
    "Neural interfaces became mandatory in 2045",
    "Rome never fell",
    "The atomic bomb was never used in war",
    "The Soviet Union won the Cold War",
    "A plague wiped out 90% of humanity in 2020",
    "The internet was invented by postal workers in 1923",
    "Women gained the vote in 1848",
    "Gunpowder was never discovered",
    "Space travel began in 1950",
    "Corporations replaced nation-states in 2020",
    "The Black Death targeted only the wealthy",
    "Malls became sovereign nations"
]

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
BRIEF_PROMPT = """You are the assigning editor of an alternate-history newspaper.

Divergence: {divergence}
Theme: {theme} | Year: {year} | Date: {date}
Story angle for today: {angle}

{theme_guide}

Create a story brief that ensures THIS edition feels distinct from generic news.
Return ONLY JSON:
{{"topic": "specific subject", "tone": "one-sentence tone directive", "voice": "narrative voice to use", "key_details": ["detail1","detail2","detail3"], "avoid": ["cliché phrase to ban", "..."]}}"""

STORY_PROMPT = """You are the lead reporter for an alternate-history newspaper. Write the main story.

Divergence: {divergence}
Theme: {theme} | Year: {year}
Editor brief: {brief}

CRITICAL RULES:
- Match the {theme} voice exactly — see theme guide below
- Use the assigned story angle: do NOT write generic "officials declined to comment" filler
- NEVER use these phrases: {forbidden}
- Include specific names, places, numbers, and quotes
- Article: 4-5 paragraphs, 320-420 words, varied sentence rhythm

{theme_guide}

Return ONLY JSON: {{"headline": "...", "deck": "...", "article": "..."}}
Article body: separate paragraphs with \\n\\n (not HTML)."""

OPED_PROMPT = """Write a sharp, provocative op-ed reacting to today's main story.

Main headline: {headline}
Divergence: {divergence} | Theme: {theme} | Year: {year}
Take an unexpected angle — contrarian, satirical, or emotionally raw.
150 words max. Return ONLY JSON: {{"title": "...", "author": "...", "body": "..."}}"""

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

CLASSIFIED_PROMPT = """Write 4 in-universe classified ads for theme: {theme}, year: {year}.
Divergence: {divergence}. Each ad should feel native to this timeline.
Return ONLY JSON array: [{{"cat": "...", "text": "..."}}, ...]"""

SPONSOR_ADS_PROMPT = """Write 3 short native advertisements that belong in a {theme}-era newspaper.
Year: {year}. Divergence: {divergence}. In-universe products/services, 1-2 sentences each.
Return ONLY JSON array: [{{"headline": "...", "body": "..."}}, ...]"""

WEATHER_PROMPT = """Weather forecast for capital city in {theme} timeline, year {year}.
Divergence: {divergence}. Invent a creatively themed condition name.
Return ONLY JSON: {{"city": "...", "condition": "...", "temp": 72, "high": 78, "low": 65}}"""

EDITOR_PROMPT = """You are the executive editor. Polish this entire newspaper edition.

Remove repetitive phrases, clichés, and samey tone across sections.
Each section must sound distinct. Preserve facts and JSON structure exactly.
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


def fallback_sponsor_ads(rng, theme):
    ads = {
        "victorian": [{"headline": "Pneumatic Post Express", "body": "Your telegram in four minutes or we eat the stamp."}, {"headline": "Brass & Sons Automata", "body": "Servants that never sleep. Ethics sold separately."}, {"headline": "Dr. Morley's Elixir", "body": "Cures melancholy, gout, and skepticism."}],
        "cyberpunk": [{"headline": "NeuroLink™ Basic", "body": "First month free. Memories are not included."}, {"headline": "CorpSec Insurance", "body": "When your identity gets hacked, we hack back."}, {"headline": "SynthMeal Blocks", "body": "Tastes like chicken because it legally has to."}],
    }
    return ads.get(theme, [{"headline": "Multiverse Gazette Subscriptions", "body": "One timeline. Daily delivery. Zero refunds across dimensions."}, {"headline": "Local Merchants Union", "body": "Shop where history went differently."}, {"headline": "Public Notice Board", "body": "Your ad could be here. This one is free."}])


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
    year = {"victorian": 1890, "artdeco": 1927, "soviet": 1962, "cyberpunk": 2087,
            "medieval": 1347, "atomic": 1954, "vaporwave": 1986, "wasteland": 2147}[theme] + (date.year - 2026)
    divergence = rng.pick(DIVERGENCES)
    angle = rng.pick(STORY_ANGLES)
    date_slug = date.strftime("%Y-%m-%d")
    forbidden = ", ".join(FORBIDDEN_PHRASES)

    editor_p = resolve_role("editor")
    story_p = resolve_role("story")
    humor_p = resolve_role("humor")
    structure_p = resolve_role("structure")
    used_providers = {}

    print(f"Timeline {timeline_id} ({theme}): story={story_p}, humor={humor_p}, editor={editor_p}")

    ctx = {"divergence": divergence, "theme": theme, "year": year,
           "date": date.strftime("%B %d, %Y"), "angle": angle, "theme_guide": THEME_GUIDE,
           "forbidden": forbidden}

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
    if not classifieds_data:
        classifieds_data = fallback_classifieds(rng, theme)

    sponsor_ads, used = llm_json_with_fallback(SPONSOR_ADS_PROMPT.format(**ctx), "structure")
    if used:
        used_providers["sponsor_ads"] = used
    if not sponsor_ads:
        sponsor_ads = fallback_sponsor_ads(rng, theme)

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
        "author": rng.pick(["Staff Correspondent", "Special Reporter", "Foreign Bureau", "Local Editor"]),
        "city": content["weather"].get("city", weather_data["city"]),
        "weather": content["weather"],
        "oped": content["oped"],
        "classifieds": content["classifieds"],
        "comic_strip": content["comic_strip"],
        "joke": content["joke"],
        "sponsor_ads": content["sponsor_ads"],
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

def cleanup_old_editions():
    """Keep only the last MAX_ARCHIVE_DAYS editions."""
    files = sorted(OUTPUT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[MAX_ARCHIVE_DAYS:]:
        stem = old.stem
        old.unlink()
        print(f"Removed old: {old.name}")
        for img in IMAGE_DIR.glob(f"{stem}-*.png"):
            img.unlink()
            print(f"Removed old image: {img.name}")

def generate_sitemap():
    """Generate sitemap.xml for SEO."""
    files = sorted(OUTPUT_DIR.glob("*.json"))
    urls = []
    for f in files:
        # Parse filename: YYYY-MM-DD-T.json
        parts = f.stem.split("-")
        if len(parts) >= 4:
            date_str = "-".join(parts[:3])
            timeline = parts[3]
            urls.append(f"https://multiversegazette.com/?timeline={timeline}&date={date_str}")

    # Add base URL
    urls.insert(0, "https://multiversegazette.com/")

    sitemap = ['<?xml version="1.0" encoding="UTF-8"?>']
    sitemap.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for url in urls:
        sitemap.append(f"  <url><loc>{url}</loc><changefreq>daily</changefreq><priority>0.8</priority></url>")
    sitemap.append("</urlset>")

    with open("sitemap.xml", "w", encoding="utf-8") as f:
        f.write("\n".join(sitemap))
    print("Generated sitemap.xml")

def generate_rss():
    """Generate RSS feed for aggregators."""
    files = sorted(OUTPUT_DIR.glob("*.json"), reverse=True)[:10]
    items = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            ed = json.load(fh)
        url = f"https://multiversegazette.com/?timeline={ed['timeline_id']}&date={ed['date']}"
        items.append(f"""<item>
      <title>{ed['headline']}</title>
      <link>{url}</link>
      <description>{ed['deck']}</description>
      <pubDate>{ed['date']}T00:00:00Z</pubDate>
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
    generate_sitemap()
    generate_rss()
    print("\nDone.")
