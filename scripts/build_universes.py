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

    # Sanity assertions before writing.
    assert len(universes) == NUM_UNIVERSES
    assert [u["id"] for u in universes] == list(range(1, NUM_UNIVERSES + 1))
    assert all(u["theme"] in THEMES for u in universes)
    assert all(200 <= u["epoch_year"] <= 5000 for u in universes)
    assert sum(1 for u in universes if u["name"] == "Prime") == 1
    assert universes[:8] == sorted(universes[:8], key=lambda u: u["id"])
    assert [u["theme"] for u in universes[:8]] == THEMES  # ids 1-8 keep mapping
    assert len(set(DIVERGENCES)) >= 50

    out = Path(__file__).resolve().parent.parent / "universes.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(universes, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"Wrote {out} — {len(universes)} universes, Prime is id {prime_id} "
          f"(epoch {prime['epoch_year']}), epochs span "
          f"{min(u['epoch_year'] for u in universes)}–{max(u['epoch_year'] for u in universes)}")


if __name__ == "__main__":
    build()
