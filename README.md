# thejumpuniverse.com

Sky events and visibility — *what is happening overhead, and when you can see it from where you are.*

Companion to [GoodSeeing](https://goodseeing.com), which covers observing **conditions**
(cloud by layer, transparency, seeing, moonlight, darkness). This site covers **events and
objects**. Strict division of labour: GoodSeeing never publishes event content, this site
never publishes forecast scores. Each links to the other.

**Status:** holding page. See [`STRATEGY-PIVOT.md`](./STRATEGY-PIVOT.md) for the full plan.

---

## History

This domain previously ran *The Multiverse Gazette*, a daily algorithmically generated
satirical newspaper. It was retired in August 2026. All generated editions, feeds, sitemaps
and brand assets were removed, and every content-producing workflow was deleted.

Retired because:

- Satire has effectively no search intent, so an automated pipeline had no discovery path.
- Google's March 2026 core update targets scaled content abuse; a site of ~200 fully
  AI-generated pages matches the penalised profile.
- YouTube's inauthentic content policy makes mass-produced, template-driven daily uploads
  ineligible for the Partner Program.

The **pipeline** was kept. The **output** was not.

## What remains

| Path | Purpose |
|---|---|
| `generate.py` | Multi-provider LLM pipeline (Moonshot / Grok / OpenAI) with fallback routing, image generation and editor passes. Retained for reuse — no longer invoked by any workflow. |
| `scripts/` | Video generation (Veo), YouTube upload, stats, deploy helpers. Retained, dormant. |
| `index.html` | Holding page. |
| `robots.txt` | Allows search crawlers and AI answer-engine crawlers explicitly. |
| `.github/workflows/deploy.yml` | Cloudflare Pages deploy on push. The only remaining workflow. |

## Automation status

**All content generation is off.** Deleted: `daily.yml`, `daily-short.yml`, `publish-short.yml`,
`publish-ad.yml`, `promo-video.yml`, `backfill-images.yml`, `migrate-images.yml`,
`yt-stats.yml`, `yt-auth-test.yml`, and the three `test-*` workflows.

Nothing publishes to the web or to YouTube on a schedule any more.

## When rebuilding

The design principle that matters: **the LLM narrates, it does not author.** Every page must
carry computed or sourced values — ephemerides, geomagnetic indices, eclipse circumstances —
with prose as a thin readable layer over verified numbers. That is the difference between the
category Google penalised in March 2026 and the category that kept its traffic.

Intended sources: Skyfield + JPL ephemerides, CelesTrak TLEs, NOAA SWPC, the IMO shower
calendar, the Minor Planet Center, and Launch Library 2.

## Deploy

Hosted on Cloudflare Pages. Push to `main` deploys. See [`DEPLOY.md`](./DEPLOY.md).
