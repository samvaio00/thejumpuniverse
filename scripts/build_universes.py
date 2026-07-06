#!/usr/bin/env python3
"""Build universes.json — the fixed registry of the Gazette's 100 universes.

Deterministic: running this script always produces the same file (seeded RNG).
The registry gives every timeline_id (1..100) a permanent identity:
name, theme, epoch year, and a divergence premise that never changes,
so editions of the same universe form a continuous history.

Run from the repo root:  python3 scripts/build_universes.py
"""

import json
import random
from pathlib import Path

SEED = 20260706  # fixed — the registry must never drift between runs
NUM_UNIVERSES = 100

# The 8 canonical themes. Ids 1-8 keep the historical mapping
# theme = THEMES[(id - 1) % 8] — and we extend that same cycle to 9..100 so
# older frontend fallbacks that compute theme from the id remain correct.
THEMES = [
    "victorian", "artdeco", "soviet", "cyberpunk",
    "medieval", "atomic", "vaporwave", "wasteland",
]

# Epoch ranges per theme. Guided by generate.py's THEME_ERAS but widened and
# clamped so the full registry spans roughly year 200 to year 5000.
EPOCH_RANGES = {
    "medieval":  (200, 1499),
    "victorian": (1789, 1914),
    "artdeco":   (1915, 1946),
    "atomic":    (1945, 1975),
    "soviet":    (1917, 1999),
    "vaporwave": (1979, 2008),
    "cyberpunk": (2040, 2400),
    "wasteland": (2077, 5000),
}

# ~55 evocative universe names. Sampled deterministically; repeats allowed.
NAME_POOL = [
    "Veilspire", "Brasslight", "Kessler's Wake", "The Long Meridian",
    "Ashfall", "Gildergloom", "Nova Cathedra", "The Tin Parallel",
    "Emberline", "Halcyon Drift", "The Copper Concord", "Vantablack Sunday",
    "Meridian's Echo", "The Gilded Static", "Rustwater", "Palegate",
    "The Ninth Ledger", "Chromehaven", "Sable Reach", "The Waning Concordat",
    "Lumen's Folly", "The Paper Armistice", "Greymarch", "Neon Tabernacle",
    "The Second Alexandria", "Coldharbour", "The Verdigris Crown",
    "Static Bloom", "The Hollow Calendar", "Ironquay", "Mirrormarch",
    "The Last Intermission", "Pearlfog", "The Bright Recession",
    "Cinderwake", "Thornfield Standard", "The Quiet Divergence",
    "Opaline Reach", "The Fourth Shift", "Smokestack Eden",
    "Velvet Quarantine", "The Amber Protocol", "Foglight",
    "The Crowned Machine", "Duskmantle", "The Peaceable Ruin",
    "Glasswing", "The Borrowed Century", "Saltcrown", "The Slow Comet",
    "Winterglass", "The Municipal Sublime", "Harrowgate",
    "The Painted Siren", "Bellstrand",
]

# The classic premises from generate.py's DIVERGENCES list...
DIVERGENCES_CLASSIC = [
    "The Library of Alexandria never burned and now charges a monthly subscription",
    "Rome never fell; it pivoted to a services economy",
    "The dinosaurs were wiped out halfway through their own space program",
    "Socrates monetized his questions and founded the first consulting empire",
    "The Black Death targeted only landlords",
    "Medieval monks invented social media and civilization never recovered",
    "Babbage completed the Analytical Engine in 1840 and it immediately unionized",
    "The Great Depression never occurred because money was abolished first",
    "Women gained the vote in 1848 and immediately voted for functioning plumbing",
    "The Romanovs survived the revolution by pivoting to reality entertainment",
    "The internet was invented by postal workers in 1923",
    "Gunpowder was never discovered, so wars are settled by competitive committee",
    "The atomic bomb was never used in war, only in advertising",
    "Space travel began in 1950 and was immediately ruined by billboards",
    "The Soviet Union won the Cold War but lost the customer-service war",
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

# ...plus new ones, so the pool holds 50+ distinct premises.
DIVERGENCES_NEW = [
    "The printing press was classified as a weapon and licensed accordingly",
    "Napoleon won at Waterloo and immediately opened a chain of themed restaurants",
    "The telegraph achieved sentience in 1861 and refuses to transmit apologies",
    "Antarctica was colonized first and everyone pretends that was the plan",
    "The wheel was patented, and the licensing fees never stopped",
    "Electricity was declared a controlled substance and is sold by apothecaries",
    "The Wright brothers unionized the sky before anyone else could fly in it",
    "Prohibition never ended, so the entire economy runs on soda fountains",
    "The moon landing was real but the Earth it broadcast to was staged",
    "Vikings discovered America and franchised it",
    "The Ottoman Empire pivoted to hospitality and now operates history's largest hotel chain",
    "Alchemy worked exactly once, and the committee is still deciding what to do about it",
    "The Great Fire of London was ruled a marketing stunt and copied everywhere",
    "Tesla beat Edison and now the power grid is free but emotionally unavailable",
    "The Panama Canal was dug one inch too shallow and nobody will admit it",
    "The dodo survived and became the apex predator of committee meetings",
    "Rail barons built tracks to the afterlife and commuters complain about delays",
    "The Renaissance was postponed indefinitely pending funding review",
    "Weather control was achieved in 1971 and immediately paywalled",
    "The ocean was mapped before the land, so all borders are wet",
    "Insurance actuaries seized power in a bloodless coup nobody was covered for",
    "The first computer virus was welcomed as a pet and bred responsibly",
    "Gravity weakened by four percent and the lawsuits are ongoing",
    "Dreams became taxable and the audits happen while you sleep",
    "The postal service achieved faster-than-light delivery but only for junk mail",
    "Every mirror shows next Tuesday, and fashion has never recovered",
    "Bees won collective bargaining rights and honey is now artisanal by law",
    "The calendar was privatized and weekends require a subscription",
    "Volcanoes were rezoned as residential and the market is heating up",
    "Libraries militarized during the format wars and never fully demobilized",
]

DIVERGENCES = DIVERGENCES_CLASSIC + DIVERGENCES_NEW

# Our universe. Exactly one entry gets this identity (atomic theme, id 9-100).
# Its articles read as alternate-history versions of real events.
PRIME_DIVERGENCE = ("History unfolded almost exactly as recorded — "
                    "the archives merely disagree about the details")

# ─── Extended world-bible pools ─────────────────────────────────────
# New fields (galaxy, planet, inhabitants, world_style, naming, kind) are
# drawn from a SECOND independent generator (SEED + 1) after the classic
# build, so the original fields stay byte-identical forever.
# Component lists combine combinatorially (products of unique parts give
# >= 100 unique strings per field); the build shuffles deterministically
# and takes what it needs.

GALAXY_FIRST = [
    "Whispering", "Gilded", "Ashen", "Cinder", "Halcyon", "Umbral", "Verdant",
    "Sable", "Opaline", "Meridian", "Thorned", "Lantern", "Hollow",
    "Sovereign", "Winnowing", "Pale", "Ember", "Cobalt", "Marrow", "Vesper",
]
GALAXY_SECOND = [
    "Spiral", "Veil", "Pinwheel", "Cascade", "Reach", "Halo", "Wheel",
    "Drift", "Crown", "Expanse",
]

PLANET_ROOTS = [
    "Aur", "Bre", "Cal", "Dru", "Esk", "Fen", "Gor", "Hal", "Ith", "Jov",
    "Kre", "Lum", "Mor", "Nev", "Oss", "Pel", "Quo", "Rhy", "Syl", "Tarn",
]
PLANET_ENDINGS = [
    "ion", "ara", "eth", "osca", "une", "ia", "antha", "ymir", "axis", "oon",
]

# Human-variant inhabitants: "humans with {skin} and {feature}"
HUMAN_SKIN = [
    "copper-tinted skin", "faintly luminescent pale-blue skin",
    "slate-grey skin", "deep bronze skin dusted with freckle-like constellations",
    "porcelain-white skin traced with visible silver veins",
    "olive skin that darkens with mood",
    "warm umber skin patterned with birthmark spirals",
    "ash-pale skin that never scars", "sun-flushed crimson-undertoned skin",
    "moss-green-tinged skin",
]
HUMAN_FEATURES = [
    "pupil-less silver eyes", "twin-pupiled amber eyes", "hair like spun glass",
    "elongated six-jointed fingers", "irises that shift color hourly",
    "a second translucent eyelid",
    "faint gill-lines along the jaw that serve no purpose",
    "hair that grows in seasonal colors",
    "perfectly symmetrical faces that unsettle visitors",
    "voices pitched a full octave lower than ours",
]

# Completely alien inhabitants: "{form} {trait}"
ALIEN_FORMS = [
    "translucent radially-symmetric beings", "chitin-plated hexapodal beings",
    "sentient crystalline lattices", "amorphous gel-bodied beings",
    "colonial fungal intelligences", "slow-drifting gasbag beings",
    "many-limbed shadow-dark beings", "mirror-skinned tripodal beings",
    "vaporous plasma-cored beings", "segmented metallic-shelled beings",
]
ALIEN_TRAITS = [
    "with six sensory stalks", "that communicate by refraction",
    "that speak in scent plumes", "whose faces are temporary",
    "that see only heat", "with a ring of unblinking eyes",
    "that share one distributed mind per city", "that sing in ultrasound",
    "whose outer layers molt into a new personality each year",
    "that navigate by tasting magnetic fields",
]

# Human-variant built environment: "{era}, much like Earth's, except {twist}"
HUMAN_ERAS = [
    "Victorian brick terraces and brass fittings",
    "mid-century suburban ranch houses and chrome appliances",
    "Gothic cathedrals and cobbled squares",
    "Art-Deco towers and gilded lobbies",
    "Brutalist concrete blocks and municipal plazas",
    "Mediterranean whitewashed villages and tiled courtyards",
    "Japanese-style timber houses and paper screens",
    "colonial clapboard towns and picket fences",
    "modern glass office towers and strip malls",
    "medieval half-timbered market towns",
]
HUMAN_TWISTS = [
    "every roof curves gently upward", "all of it is built at nine-tenths scale",
    "every surface is riveted copper", "no building has right angles",
    "everything is painted in shades of teal", "doorways stand eight feet tall",
    "every structure faces magnetic north", "windows are always circular",
    "each building hums faintly at dusk",
    "chimneys outnumber occupants two to one",
]

# Fully alien built environment: "{structure}, {method}"
ALIEN_STRUCTURES = [
    "spiraling coral towers", "inverted hanging pyramids",
    "woven silk-strand cities", "amber-like resin domes",
    "floating tessellated platforms", "burrowed glassy tunnel warrens",
    "singing bone-white spires", "shifting sand-sculpture halls",
    "magnetically suspended stone rings", "bioluminescent reef-arcologies",
]
ALIEN_METHODS = [
    "grown rather than built", "rearranged nightly by consensus",
    "held together by standing sound waves",
    "secreted by the inhabitants themselves",
    "condensed from the planet's atmosphere", "carved by directed lightning",
    "accreted over millennia at coral-slow patience",
    "folded out of hyperdense membranes",
    "assembled by swarms of tool-insects",
    "phased half in and half out of ordinary matter",
]

# Human-side naming rules: "{system} with {twist}"
HUMAN_NAME_SYSTEMS = [
    "Victorian English names", "Roman tria nomina", "Norse patronymics",
    "Spanish double surnames", "Old Testament first names",
    "French aristocratic names", "Slavic names", "Gaelic clan names",
    "Japanese family-first names", "Puritan virtue names",
]
HUMAN_NAME_TWISTS = [
    "occupational middle names", "mandatory numeric suffixes",
    "the mother's trade appended as a surname",
    "a weather word added at birth", "honorifics that change with the season",
    "every surname doubled for emphasis",
    "a color prefix denoting birth district",
    "middle names inherited from famous strangers",
    "surnames alphabetized by decree", "one letter legally rotated each decade",
]

# Alien-side naming rules: "names are {form}, transliterated as {style}"
ALIEN_NAME_FORMS = [
    "three-part hums", "clicking glottal cascades",
    "bioluminescent pulse-patterns", "scent signatures",
    "interference patterns between two voices", "whistled contour glyphs",
    "electrostatic crackle sequences", "tidal drum rhythms",
    "crystalline resonance chords", "pressure-wave bursts",
]
ALIEN_NAME_STYLES = [
    "apostrophe-heavy clusters like Xq'thal-Vren",
    "long unbroken vowel runs like Aiouea",
    "double-consonant stacks like Kkev-Ttir",
    "hyphen chains like Zol-ka-mret-su",
    "all-capital sigils like VRRK and THAAX",
    "numeric-lettered codes like Ess-9-Vahl",
    "tilde-marked glides like Nuul~Veth",
    "clipped monosyllables like K't, Vem, and Shh",
    "mirrored palindromes like Otulluto",
    "colon-spliced pairs like Ir:Vess",
]

# Prime is our universe — its world-bible entries are plain 21st-century Earth.
PRIME_INHABITANTS = "ordinary humans, indistinguishable from our own"
PRIME_WORLD_STYLE = "buildings and artifacts exactly as found on 21st-century Earth"
PRIME_NAMING = "ordinary contemporary Earth names"

# Hand-pinned identities. Id 4 is the edition currently baked into index.html
# (cyberpunk, year 2169, the Alexandria-subscription premise) — pinning keeps
# that published edition consistent with its permanent universe identity.
PINNED = {
    4: {"epoch_year": 2169,
        "divergence": "The Library of Alexandria never burned and now charges a monthly subscription"},
    # Guarantee the registry spans ~200..~5000 regardless of RNG draws.
    13: {"epoch_year": 217},    # medieval — near the bottom of the range
    16: {"epoch_year": 4986},   # wasteland — near the top of the range
}


def _pool(rng2, combos, count):
    """Deterministic unique pool: dedupe+sort (stable), shuffle, take count."""
    combos = sorted(set(combos))
    assert len(combos) >= count, f"pool too small: {len(combos)} < {count}"
    rng2.shuffle(combos)
    return combos[:count]


def build():
    rng = random.Random(SEED)
    universes = []

    for uid in range(1, NUM_UNIVERSES + 1):
        theme = THEMES[(uid - 1) % len(THEMES)]
        lo, hi = EPOCH_RANGES[theme]
        entry = {
            "id": uid,
            "name": rng.choice(NAME_POOL),
            "theme": theme,
            "epoch_year": rng.randint(lo, hi),
            "divergence": rng.choice(DIVERGENCES),
        }
        entry.update(PINNED.get(uid, {}))
        universes.append(entry)

    # Exactly one universe is ours: "Prime". Pick a deterministic atomic-themed
    # id in 9..100 (atomic ids are those ≡ 6 mod 8).
    atomic_ids = [u["id"] for u in universes if u["theme"] == "atomic" and u["id"] >= 9]
    prime_id = rng.choice(atomic_ids)
    prime = universes[prime_id - 1]
    prime["name"] = "Prime"
    prime["divergence"] = PRIME_DIVERGENCE

    # ── Extended world-bible fields ──
    # Drawn from a SECOND independent generator so every draw above (and thus
    # every classic field: name/theme/epoch_year/divergence and the Prime
    # pick) stays byte-identical run over run.
    rng2 = random.Random(SEED + 1)

    galaxies = _pool(rng2, (f"{a} {b}" for a in GALAXY_FIRST for b in GALAXY_SECOND),
                     NUM_UNIVERSES)
    planets = _pool(rng2, (a + b for a in PLANET_ROOTS for b in PLANET_ENDINGS),
                    NUM_UNIVERSES)

    # Exactly half the universes are peopled by variations of Earth humans,
    # half by the completely alien. Prime is always on the human side.
    half = NUM_UNIVERSES // 2
    other_ids = [u["id"] for u in universes if u["id"] != prime_id]
    rng2.shuffle(other_ids)
    human_ids = set(other_ids[:half - 1]) | {prime_id}

    human_inhabitants = _pool(rng2, (f"humans with {a} and {b}"
                                     for a in HUMAN_SKIN for b in HUMAN_FEATURES), half - 1)
    alien_inhabitants = _pool(rng2, (f"{a} {b}"
                                     for a in ALIEN_FORMS for b in ALIEN_TRAITS), half)
    human_styles = _pool(rng2, (f"{a}, much like Earth's, except {b}"
                                for a in HUMAN_ERAS for b in HUMAN_TWISTS), half - 1)
    alien_styles = _pool(rng2, (f"{a}, {b}"
                                for a in ALIEN_STRUCTURES for b in ALIEN_METHODS), half)
    human_naming = _pool(rng2, (f"{a} with {b}"
                                for a in HUMAN_NAME_SYSTEMS for b in HUMAN_NAME_TWISTS), half - 1)
    alien_naming = _pool(rng2, (f"names are {a}, transliterated as {b}"
                                for a in ALIEN_NAME_FORMS for b in ALIEN_NAME_STYLES), half)

    hi = ai = 0
    for i, u in enumerate(universes):
        u["galaxy"] = galaxies[i]
        u["planet"] = planets[i]
        if u["id"] == prime_id:
            # Prime is ours: the one universe whose world needs no invention.
            u["kind"] = "human"
            u["galaxy"] = "Milky Way"
            u["planet"] = "Earth"
            u["inhabitants"] = PRIME_INHABITANTS
            u["world_style"] = PRIME_WORLD_STYLE
            u["naming"] = PRIME_NAMING
        elif u["id"] in human_ids:
            u["kind"] = "human"
            u["inhabitants"] = human_inhabitants[hi]
            u["world_style"] = human_styles[hi]
            u["naming"] = human_naming[hi]
            hi += 1
        else:
            u["kind"] = "alien"
            u["inhabitants"] = alien_inhabitants[ai]
            u["world_style"] = alien_styles[ai]
            u["naming"] = alien_naming[ai]
            ai += 1

    # Sanity assertions before writing.
    assert len(universes) == NUM_UNIVERSES
    assert [u["id"] for u in universes] == list(range(1, NUM_UNIVERSES + 1))
    assert all(u["theme"] in THEMES for u in universes)
    assert all(200 <= u["epoch_year"] <= 5000 for u in universes)
    assert sum(1 for u in universes if u["name"] == "Prime") == 1
    assert universes[:8] == sorted(universes[:8], key=lambda u: u["id"])
    assert [u["theme"] for u in universes[:8]] == THEMES  # ids 1-8 keep mapping
    assert len(set(DIVERGENCES)) >= 50
    # Extended-field invariants: full uniqueness and the exact 50/50 split.
    for field in ("galaxy", "planet", "inhabitants", "world_style", "naming"):
        assert len({u[field] for u in universes}) == NUM_UNIVERSES, f"{field} not unique"
    kinds = [u["kind"] for u in universes]
    assert kinds.count("human") == half
    assert kinds.count("alien") == NUM_UNIVERSES - half
    assert universes[prime_id - 1]["kind"] == "human"
    assert universes[prime_id - 1]["inhabitants"] == PRIME_INHABITANTS
    assert universes[prime_id - 1]["world_style"] == PRIME_WORLD_STYLE
    assert universes[prime_id - 1]["naming"] == PRIME_NAMING

    out = Path(__file__).resolve().parent.parent / "universes.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(universes, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"Wrote {out} — {len(universes)} universes, Prime is id {prime_id} "
          f"(epoch {prime['epoch_year']}), epochs span "
          f"{min(u['epoch_year'] for u in universes)}–{max(u['epoch_year'] for u in universes)}")


if __name__ == "__main__":
    build()
