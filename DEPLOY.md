# Deploy The Multiverse Gazette to Cloudflare Pages

## Prerequisites
- GitHub account
- Cloudflare account (free)
- Domain: thejumpuniverse.com (already in Cloudflare)
- OpenAI API key (optional, falls back to templates)

## Step 1: Create GitHub Repository

1. Go to https://github.com/new
2. Name it `thejumpuniverse` (or any name)
3. Make it **Public** (required for Cloudflare Pages free tier)
4. Do NOT initialize with README (we have our own)
5. Create repository

## Step 2: Push Code to GitHub

```bash
# In your local project folder
git init
git add .
git commit -m "Initial commit: The Multiverse Gazette"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/thejumpuniverse.git
git push -u origin main
```

## Step 3: Configure Secrets

Add credentials in **one** of these places:

### Option 1: Cursor Cloud Agent Secrets (best for autonomous setup)

1. Open https://cursor.com/dashboard/cloud-agents
2. Go to **Secrets**
3. Add:
   - `CLOUDFLARE_API_TOKEN` — API token with **Cloudflare Pages → Edit**
   - `CLOUDFLARE_ACCOUNT_ID` — from the Cloudflare dashboard sidebar
4. Ask the cloud agent to deploy, or run:

```bash
./scripts/deploy-cloudflare.sh
```

The agent can deploy immediately without GitHub Actions secrets.

### Option 2: GitHub Actions Secrets

1. Go to your repo → **Settings** → **Secrets and variables** → **Actions**
2. Add these secrets:

| Secret | Required for | Notes |
|--------|--------------|-------|
| `CLOUDFLARE_API_TOKEN` | Deploy | Account → Cloudflare Pages → Edit |
| `CLOUDFLARE_ACCOUNT_ID` | Deploy | From Cloudflare dashboard sidebar |
| `MOONSHOT_API_KEY` | Daily AI | **Story** writing (rich alternate-history narrative) |
| `GROK_API_KEY` | Daily AI + images | **Humor** — op-ed, comic strip, joke of the day; comic strip images |
| `OPENAI_API_KEY` | Daily AI + images | **Editor** + structure — polishes tone, classifieds, weather, sponsor ads; hero photos |

Each edition uses all three AIs in fixed roles (not random). OpenAI acts as executive editor to remove repetitive phrasing.

> **Note:** If no LLM key is set, the generator uses built-in templates. The site works either way.

## Step 4: Connect Cloudflare Pages

Choose **one** of these options. Option A is recommended because it deploys from GitHub Actions and does not require linking the repo inside the Cloudflare dashboard.

### Option A: Deploy via GitHub Actions (recommended)

1. Log in to https://dash.cloudflare.com
2. Copy your **Account ID** from the right sidebar on the Workers & Pages overview
3. Create an API token:
   - Go to **My Profile** → **API Tokens** → **Create Token**
   - Use the **Edit Cloudflare Workers** template, or create a custom token with:
     - **Account** → **Cloudflare Pages** → **Edit**
   - Copy the token (shown only once)
4. Add GitHub repository secrets:
   - Go to your repo → **Settings** → **Secrets and variables** → **Actions**
   - Add `CLOUDFLARE_API_TOKEN` with the token value
   - Add `CLOUDFLARE_ACCOUNT_ID` with your account ID
5. Push to `main` (or merge a PR). The **Deploy to Cloudflare Pages** workflow will:
   - Create the `thejumpuniverse` Pages project on first run if it does not exist
   - Upload the static site from the repo root
   - Deploy on every push to `main` and after each daily edition commit

Your site will be live at `https://thejumpuniverse.pages.dev` until you add a custom domain.

### Option B: Connect Git in the Cloudflare dashboard

1. Log in to https://dash.cloudflare.com
2. Go to **Workers & Pages** → **Create application** → **Pages** → **Connect to Git**
3. Select your GitHub account → authorize Cloudflare
4. Select the `thejumpuniverse` repository
5. Click **Begin setup**

### Build Settings (Option B only)
- **Project name:** `thejumpuniverse`
- **Production branch:** `main`
- **Build command:** *(leave empty — this is a static site)*
- **Build output directory:** `/` *(root)*
- **Root directory:** *(leave empty)*

Click **Save and Deploy**

> **Note:** If you use Option A (GitHub Actions), do not also connect the same repo in the Cloudflare dashboard. That would create duplicate deployments.

## Step 5: Connect Custom Domain

1. In your Cloudflare Pages project, go to **Custom domains**
2. Click **Set up a custom domain**
3. Enter: `thejumpuniverse.com`
4. Cloudflare will detect the domain is already in your account
5. Click **Activate domain**
6. SSL certificate is provisioned automatically (takes ~2 minutes)

## Step 6: Verify Daily Generation

The GitHub Actions workflow runs automatically every day at 00:01 UTC. To verify:

1. Go to your repo → **Actions** tab
2. You should see the workflow "Daily Gazette Generation"
3. Wait until 00:01 UTC (or trigger manually: click **Run workflow**)
4. After it runs, check that new files appear in the `editions/` folder
5. Cloudflare Pages will auto-deploy the new commit within 1-2 minutes

## Step 7: Test the Live Site

1. Visit `https://thejumpuniverse.com`
2. You should see today's newspaper
3. Click **🌌 Jump Universe** — it should generate a new timeline
4. Check that the URL updates to `?timeline=XXXXX&date=YYYY-MM-DD`
5. Share that URL — it should load the exact same edition for anyone

## File Structure (What Gets Deployed)

```
thejumpuniverse.com/
├── index.html              ← Main frontend (fetches JSON or generates client-side)
├── editions/               ← Generated daily by GitHub Actions
│   ├── 2026-07-03-1.json   ← Victorian theme
│   ├── 2026-07-03-2.json   ← Art Deco theme
│   ├── ...
│   └── 2026-07-03-8.json   ← Wasteland theme
├── sitemap.xml             ← Auto-generated for SEO
├── rss.xml                 ← Auto-generated RSS feed
├── robots.txt              ← SEO crawler instructions
├── _headers                ← Cloudflare cache rules
├── _redirects              ← URL rewriting rules
├── generate.py             ← Daily AI agent script
└── .github/workflows/      ← Automation
    └── daily.yml
```

## How It Works After Setup

```
Every day at 00:01 UTC
        │
        ▼
┌─────────────────┐
│ GitHub Actions  │─── Runs on GitHub's servers (free)
│   (Cron Job)    │    Calls OpenAI API (or uses templates)
└─────────────────┘    Writes 8 JSON files to editions/
        │
        ▼
┌─────────────────┐
│  Git Commit +   │─── Commits JSON files back to repo
│     Push        │
└─────────────────┘
        │
        ▼
┌─────────────────┐
│ Cloudflare Pages│─── Detects new commit, auto-deploys
│   (CDN Edge)    │    Builds from repo, pushes to 300+ cities
└─────────────────┘
        │
        ▼
┌─────────────────┐
│  thejumpuniverse│─── Visitor loads page from nearest edge
│     .com        │    Fetches JSON edition for today
└─────────────────┘    Falls back to client-side generator if missing
```

## Cost Breakdown

| Service | Cost | Why |
|---------|------|-----|
| Cloudflare Pages | **$0** | Free for static sites |
| Cloudflare DNS | **$0** | Included with domain |
| GitHub Actions | **$0** | 2,000 minutes/month free (uses ~15 min/month) |
| OpenAI API | **~$0.50/month** | 8 editions/day × 30 days × ~$0.002 = ~$0.48 |
| **Total** | **~$0.50/month** | Plus domain renewal (~$10/year) |

## Troubleshooting

### "No editions folder after workflow runs"
- Check the Actions tab for error logs
- If OpenAI key is missing, the script falls back to templates but still generates files
- Make sure `permissions: contents: write` is in the workflow

### "Site not updating after workflow"
- If using GitHub Actions deploy: check **Actions** → **Deploy to Cloudflare Pages**
- Ensure `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` secrets are set
- If using Cloudflare Git integration: Pages deploys automatically on every commit to main
- Check Pages dashboard → Deployments for status

### "Custom domain shows 404"
- Ensure DNS record in Cloudflare is proxied (orange cloud)
- Wait 2-5 minutes for SSL certificate provisioning
- Try clearing browser cache or using incognito

### "Want to use Hetzner instead"
If you later want a VPS for other reasons:
1. Keep Cloudflare as CDN (point DNS to Hetzner IP)
2. Run `generate.py` via `cron` on Hetzner
3. Serve files with nginx
4. You lose auto-deploy but gain full server control
5. Cost: €4.51/month vs $0/month

## Monitoring

- **Uptime:** Cloudflare Pages dashboard shows analytics
- **Traffic:** Cloudflare Analytics (no Google Analytics needed)
- **Errors:** GitHub Actions tab shows generation logs
- **SEO:** Submit sitemap to Google Search Console:
  `https://thejumpuniverse.com/sitemap.xml`
