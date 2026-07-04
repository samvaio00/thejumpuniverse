#!/usr/bin/env python3
"""
The Multiverse Gazette — Daily Edition Generator
Runs via cron/GitHub Actions at 00:01 UTC daily.
Generates a static JSON edition file for the frontend to consume.
"""

import json
import os
import random
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import requests

# ─── CONFIG ─────────────────────────────────────────────────────────
OUTPUT_DIR = Path("editions")
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "auto").lower()

LLM_PROVIDERS = {
    "moonshot": {
        "api_key": os.environ.get("MOONSHOT_API_KEY"),
        "base_url": "https://api.moonshot.ai/v1",
        "model": os.environ.get("MOONSHOT_MODEL", "kimi-k2.6"),
    },
    "grok": {
        "api_key": os.environ.get("GROK_API_KEY") or os.environ.get("XAI_API_KEY"),
        "base_url": "https://api.x.ai/v1",
        "model": os.environ.get("GROK_MODEL", "grok-2-1212"),
    },
    "openai": {
        "api_key": os.environ.get("OPENAI_API_KEY"),
        "base_url": "https://api.openai.com/v1",
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
    },
}
MAX_ARCHIVE_DAYS = 30  # Keep last 30 days

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
def llm_provider_order():
    if LLM_PROVIDER != "auto":
        return [LLM_PROVIDER]
    return [name for name in ("moonshot", "grok", "openai") if LLM_PROVIDERS[name]["api_key"]]


def llm_available():
    return bool(llm_provider_order())


def generate_with_llm(prompt):
    """Generate content using the configured OpenAI-compatible LLM provider."""
    for provider_name in llm_provider_order():
        provider = LLM_PROVIDERS.get(provider_name)
        if not provider or not provider["api_key"]:
            continue

        try:
            resp = requests.post(
                f"{provider['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {provider['api_key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": provider["model"],
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.9,
                    "max_tokens": 800,
                },
                timeout=60,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            print(f"LLM provider: {provider_name} ({provider['model']})")
            return content
        except Exception as e:
            print(f"LLM error ({provider_name}): {e}")

    return None

# ─── PROMPT ENGINEERING ─────────────────────────────────────────────
HEADLINE_PROMPT = """You are the editor-in-chief of a newspaper from an alternate timeline.

Divergence point: {divergence}
Theme: {theme}
Year: {year}
Date: {date}

Write a newspaper headline and deck (subtitle) for the main story. The tone should match the theme:
- victorian: ornate, formal, 19th-century journalistic style
- artdeco: jazz-age exuberance, 1920s-30s sophistication
- soviet: propaganda-heavy, collective-focused, 1960s communist
- cyberpunk: dystopian tech, corporate-speak, 2080s
- medieval: chronicle-style, religious undertones, 1300s
- atomic: optimistic mid-century, 1950s Americana, slightly paranoid
- vaporwave: ironic 80s/90s nostalgia, consumer-culture satire
- wasteland: gritty survival, post-apocalyptic, sparse prose

Return ONLY a JSON object with this exact structure:
{{"headline": "...", "deck": "...", "article": "..."}}

The article should be 4-5 paragraphs of newspaper prose, 300-400 words total."""

OPED_PROMPT = """You are an op-ed columnist for a newspaper from an alternate timeline where {divergence}.
Theme: {theme}. Year: {year}.

Write a short op-ed (150 words) with a provocative title and author name appropriate to the era/theme.
Return ONLY JSON: {{"title": "...", "author": "...", "body": "..."}}"""

CLASSIFIED_PROMPT = """You are writing classified ads for a newspaper from a timeline where {divergence}.
Theme: {theme}. Year: {year}.

Write 4 short classified ads (1-2 sentences each) in categories: For Sale, Employment, Housing, Services, Lost, Wanted.
Return ONLY a JSON array of objects: [{{"cat": "...", "text": "..."}}, ...]"""

WEATHER_PROMPT = """For a newspaper in a timeline where {divergence}, theme: {theme}, year: {year}, 
write a weather forecast for the capital city. Return ONLY JSON:
{{"city": "...", "condition": "...", "temp": 72, "high": 78, "low": 65}}
The condition should be a creatively named weather state matching the theme (e.g., "Phlogiston Clear" for victorian)."""

COMIC_PROMPT = """Write a one-panel comic caption for a newspaper from a timeline where {divergence}.
Theme: {theme}. The caption should be a single line of dialogue or observation, dryly humorous.
Return ONLY JSON: {{"caption": "...", "scene": "..."}}
Scene should be one of: clockwork, airship, laboratory, street, skyscraper, club, car, park, factory, monument, apartment, train, alley, terminal, rooftop, market, castle, village, forest, tavern, suburb, diner, shelter, fair, mall, beach, arcade, desert, ruin, camp, vehicle"""

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
        f"In a development that has stunned observers across the nation, {h.lower()}. Sources close to the matter have confirmed that the situation remains fluid.",
        f"Officials in {rng.pick(['the capital','the provinces','the central district'])} have declined to comment, but witnesses describe a scene of both confusion and cautious optimism. The implications for the average citizen remain unclear.",
        f"Historical records suggest a similar event occurred nearly a century ago in this timeline, though on a much smaller scale. International observers have expressed both admiration and concern.",
        f"Residents of the capital expressed mixed reactions. 'I never thought I'd see the day,' said one local merchant. 'It changes everything, and yet it changes nothing.'",
        f"Only time will tell whether this marks the beginning of a new era or merely a footnote in the annals of this parallel history."
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

# ─── MAIN GENERATION ────────────────────────────────────────────────
def generate_edition(date=None, timeline_id=None):
    if date is None:
        date = datetime.now(timezone.utc)

    date_seed = date.year * 10000 + (date.month) * 100 + date.day

    if timeline_id is None:
        day_of_year = date.timetuple().tm_yday
        timeline_id = (day_of_year % len(THEMES)) + 1

    seed = timeline_id * 1000000 + date_seed
    rng = SeededRandom(seed)

    theme = THEMES[(timeline_id - 1) % len(THEMES)]
    year = {"victorian": 1890, "artdeco": 1927, "soviet": 1962, "cyberpunk": 2087,
            "medieval": 1347, "atomic": 1954, "vaporwave": 1986, "wasteland": 2147}[theme] + (date.year - 2026)

    divergence = rng.pick(DIVERGENCES)

    # Try LLM first, fallback to templates
    headline_data = None
    if llm_available():
        prompt = HEADLINE_PROMPT.format(divergence=divergence, theme=theme, year=year, date=date.strftime("%B %d, %Y"))
        raw = generate_with_llm(prompt)
        if raw:
            try:
                headline_data = json.loads(raw)
            except:
                pass

    if not headline_data:
        headline_data = fallback_headline(rng, theme, year, divergence)

    # Op-Ed
    oped_data = None
    if llm_available():
        raw = generate_with_llm(OPED_PROMPT.format(divergence=divergence, theme=theme, year=year))
        if raw:
            try:
                oped_data = json.loads(raw)
            except:
                pass
    if not oped_data:
        oped_data = fallback_oped(rng, theme)

    # Classifieds
    classifieds_data = None
    if llm_available():
        raw = generate_with_llm(CLASSIFIED_PROMPT.format(divergence=divergence, theme=theme, year=year))
        if raw:
            try:
                classifieds_data = json.loads(raw)
            except:
                pass
    if not classifieds_data:
        classifieds_data = fallback_classifieds(rng, theme)

    # Weather
    weather_data = {"city": rng.pick(["The Capital", "New London", "Metropolis", "Neo-Tokyo", "King's Landing", "Springfield", "Miami Vice", "New Eden"]),
                    "condition": rng.pick(["Clear", "Foggy", "Stormy", "Bright", "Hazy", "Windy", "Drizzle", "Overcast"]),
                    "temp": rng.range(15, 95), "high": 0, "low": 0}
    weather_data["high"] = weather_data["temp"] + rng.range(3, 10)
    weather_data["low"] = weather_data["temp"] - rng.range(3, 10)

    # Comic
    comic_data = {"caption": rng.pick(["'Life goes on. Somehow.'", "'The future is here, and it is slightly greasy.'", "'I came for the view. I stayed because the door locked.'"]),
                  "scene": rng.pick(["street", "laboratory", "castle", "alley", "suburb", "ruin"])}

    edition = {
        "timeline_id": timeline_id,
        "theme": theme,
        "date": date.strftime("%Y-%m-%d"),
        "date_display": date.strftime("%A, %B %d, %Y"),
        "year": year,
        "divergence": divergence,
        "headline": headline_data["headline"],
        "deck": headline_data["deck"],
        "article": headline_data["article"],
        "author": rng.pick(["Staff Correspondent", "Special Reporter", "Foreign Bureau", "Local Editor"]),
        "city": weather_data["city"],
        "weather": weather_data,
        "oped": oped_data,
        "classifieds": classifieds_data,
        "comic": comic_data,
        "generated_at": datetime.now(timezone.utc).isoformat()
    }

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
        old.unlink()
        print(f"Removed old: {old.name}")

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
    args = parser.parse_args()

    if args.date:
        date = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        date = datetime.now(timezone.utc)

    if args.all:
        for tid in range(1, 9):
            ed = generate_edition(date, tid)
            save_edition(ed)
    else:
        ed = generate_edition(date, args.timeline)
        save_edition(ed)

    cleanup_old_editions()
    generate_sitemap()
    generate_rss()
    print("\nDone.")
