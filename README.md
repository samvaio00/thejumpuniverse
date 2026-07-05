# The Multiverse Gazette

A daily satirical newspaper from alternate universes across all of time — news parody in the spirit of The Onion by way of Terry Pratchett. Every day, a new story in a different universe on a random date across eons; the comic strip, joke, classifieds, and ads all riff on the front page. Zero sign-up. Zero products. Pure traffic and ad revenue.

## Content Pipeline

- Each edition picks a random year within its theme's era (medieval 713–1499 … wasteland 2077–12077) and a satirical divergence premise
- Real-world headlines are fetched from news RSS at generation time so stories can obliquely mirror current affairs (fails soft if offline)
- An editor "brief" stage designs the day's comic premise; every section prompt receives the front-page headline so the whole paper reads as one universe reacting to one event
- A final editor pass enforces cohesion and punches up flat jokes
- The day's lead edition is prerendered into `index.html` (title, meta/OG tags, headline, article, JSON-LD) so search engines and no-JS readers see real content

## Architecture

```
GitHub Actions (Daily 00:01) → Python Script (LLM + Fallback) → Static JSON /editions/
                                                                   │
                                                                   ▼
                                                        Static HTML (Cloudflare Pages)
                                                        Fetches JSON + Client Fallback
```

## Quick Start

```bash
# 1. Generate today's editions
python generate.py --all

# 2. Serve locally
python -m http.server 8000
# Open http://localhost:8000

# 3. Deploy to Cloudflare Pages
#    Add CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID repo secrets, then push to main.
#    See DEPLOY.md for setup details.
```

## GitHub Actions Setup

1. Fork repo → Settings → Secrets → Actions
2. Add `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` for deploy
3. Add one or more LLM keys: `MOONSHOT_API_KEY`, `GROK_API_KEY`, or `OPENAI_API_KEY`
4. Images use **Grok** (comic strip) and **OpenAI** (hero photo). Text uses fixed roles: Moonshot=story, Grok=humor, OpenAI=editor
5. Workflow runs daily at 00:01 UTC automatically

## Monetization

- **Google AdSense**: Replace `.ad-slot` divs with ad code
- **Sponsored Timelines**: Brand-sponsored divergence points
- **Affiliate Links**: Embedded in classifieds
- **Native Ads**: Styled as in-universe newspaper ads

## SEO

- Dynamic title/meta/canonical per edition
- Open Graph + Twitter Cards
- Schema.org NewsArticle structured data
- Auto-generated sitemap.xml + rss.xml (properly XML-escaped)
- Canonical URLs with timeline/date params
- Semantic HTML5 + print styles

## Frontend Features

- `editions/manifest.json` — index of every published edition, regenerated daily; powers the site-wide archive and previous/next-day navigation
- Jump Universe hops between the 8 real timelines for the current date
- Keyboard shortcuts: `←`/`→` previous/next day, `J` jump universe
- Per-theme display typography via Google Fonts (Playfair Display, Oswald, Orbitron, MedievalSharp, Righteous, Monoton, Special Elite, …)
- Web Share API with clipboard fallback

## License

MIT. All timelines reserved.
