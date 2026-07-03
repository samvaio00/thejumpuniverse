# The Multiverse Gazette

A daily, procedurally generated newspaper from alternate timelines. Zero sign-up. Zero products. Pure traffic and ad revenue.

## Architecture

```
GitHub Actions (Daily 00:01) → Python Script (LLM + Fallback) → Static JSON /editions/
                                                                   │
                                                                   ▼
                                                        Static HTML (Vercel/Netlify)
                                                        Fetches JSON + Client Fallback
```

## Quick Start

```bash
# 1. Generate today's editions
python generate.py --all

# 2. Serve locally
python -m http.server 8000
# Open http://localhost:8000

# 3. Deploy to Vercel
vercel --prod
```

## GitHub Actions Setup

1. Fork repo → Settings → Secrets → Actions
2. Add `OPENAI_API_KEY` (optional, falls back to templates)
3. Workflow runs daily at 00:01 UTC automatically

## Monetization

- **Google AdSense**: Replace `.ad-slot` divs with ad code
- **Sponsored Timelines**: Brand-sponsored divergence points
- **Affiliate Links**: Embedded in classifieds
- **Native Ads**: Styled as in-universe newspaper ads

## SEO

- Dynamic title/meta per edition
- Open Graph + Twitter Cards
- Schema.org NewsArticle structured data
- Auto-generated sitemap.xml + rss.xml
- Canonical URLs with timeline/date params
- Semantic HTML5 + print styles

## License

MIT. All timelines reserved.
