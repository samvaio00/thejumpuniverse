# thejumpuniverse.com — Repositioning Strategy

**Date:** 14 August 2026
**Constraints assumed:** must run itself (near-zero weekly time) · goals are revenue + audience + showcase · concept is fully open

---

## 1. The verdict, up front

Kill the Multiverse Gazette. Keep the machine.

Repoint the domain at the **events and objects layer of astronomy** — *what is there to see, and when* — as the top-of-funnel companion to GoodSeeing, which owns *whether tonight is usable*.

Neither site competes with the other. Together they answer a question **no existing astronomy site answers well**, and that combination is the actual strategic asset here.

**But not simultaneously.** GoodSeeing launched yesterday and has no signal yet. Two zero-authority domains launched in parallel, on a near-zero time budget, is the most reliable way to end up with two dead sites. Sequence it — see §9.

---

## 2. What you actually have

The concept was never the asset. The infrastructure is.

| Asset | State | Value |
|---|---|---|
| Content pipeline (`generate.py`, 107KB) | Multi-provider LLM routing with fallback across Moonshot/Grok/OpenAI, image generation, editor passes | High — reusable as-is |
| Video pipeline | `make_short*.py`, Veo integration, automated YouTube upload, stats tracking | High — rare capability |
| Publishing | Static site gen, sitemap + video sitemap, RSS, JSON-LD, Cloudflare Pages, 13 GitHub Actions workflows on cron | High — fully automated ops |
| Domain | `thejumpuniverse.com`, ~207 editions indexed | Mixed — see §7 |
| YouTube channel | 26 videos, **377 total views, median 3 views/video** | Effectively a blank slate |

That is a genuine autonomous publishing system. Most people who want one don't have one.

## 3. Why it isn't working — and why "same machine, new topic" would also fail

**The concept problem.** Satire has near-zero search intent. Nobody googles "forkless America." Discovery for comedy runs entirely on social virality and personality — the two things an automated pipeline cannot manufacture. You built a distribution-dependent product with no distribution.

**Two platform walls now make the autonomous-fiction model structurally unviable:**

- Google's **March 2026 core update** named scaled content abuse as a primary target. Sites publishing high volumes of AI-generated pages without editorial oversight saw 50–80% traffic drops; of monitored sites deindexed after this class of update, **100% showed signs of AI generation and half had 90–100% AI-written posts**. The Gazette's profile is a textbook match.
- YouTube's **inauthentic content policy** (renamed from "repetitious content," July 2025) makes mass-produced, template-driven, minimal-human-input video ineligible for the Partner Program, enforced on a three-strike ladder. Your daily Shorts are exactly that profile. The YouTube monetization path, as currently built, is already closed.

**The important implication:** the failure mode isn't the topic. Pointing the same fiction-generating machine at a new subject walks into the same two walls. What has to change is **what the machine produces** — from *invented prose* to *computed and sourced data*.

That distinction is precisely what survived 2026's updates. Programmatic pages built on unique structured data — live pricing, verified listings, real computed values — kept their traffic. The documented survival criterion is roughly **≥3 unique data points per page, a real use case, and dense internal linking**. Astronomy satisfies this trivially, because the data is *calculated per location and per date* and cannot be synthesized from other pages.

---

## 4. The astronomy read: what GoodSeeing owns, and what it structurally doesn't

GoodSeeing is well built — forecast scoring by layer, guides, equipment/compare, dark-sky sites, location browse, email alerts, plus method and accuracy pages doing real credibility work. It answers one question precisely:

> **"Is tonight worth setting up the scope?"**

That is a *conditions* product for people who **already own a telescope**. High intent, real moat, small audience.

It launched yesterday, so none of what follows is validated by traffic — it's an argument from structure, not from data. Treat the first 90 days of GoodSeeing as the experiment that tells you whether this whole thesis holds.

It does not answer — and given its positioning, probably should not answer:

> **"What is up there tonight, and when is the next thing worth seeing?"**

That is an *events and objects* product, and it serves an audience perhaps 50–100× larger: people who want to catch the aurora, the eclipse, the meteor shower, the ISS pass, or who just want to know what that bright object is. It spikes hard and predictably around celestial events, and it is the natural feeder into both GoodSeeing and telescope purchases.

### The combination nobody else offers

Every competitor covers one half:

- **timeanddate, TheSkyLive, in-the-sky.org, Sky Tonight, EarthSky** → what's visible. None tell you whether you'll actually see it from where you are.
- **Clear Outside, Astrospheric, GoodSeeing** → observing conditions. None tell you what's worth going out *for*.

You own both halves. `visibility × observability` is a data product with no strong incumbent, and it is defensible because it requires running two engines you already have. That is the real reason to keep two domains rather than one.

---

## 5. Recommended architecture

| | **thejumpuniverse.com** | **goodseeing.com** |
|---|---|---|
| Question | What's up there, and when? | Is tonight usable? |
| Layer | Events & objects | Conditions & forecast |
| Audience | Casual → enthusiast (wide) | Practitioner (narrow, high intent) |
| Traffic shape | Spiky, seasonal, event-driven | Steady, recurring |
| Role | Acquisition + audience | Retention + conversion |
| Automation | ~95% autonomous | Needs your judgment |
| Shared | One email list · reciprocal deep links · shared ephemeris code |

Cross-link at the point of intent, not in the footer: every Jump Universe event page ends with *"Will you see it from your location?"* → the matching GoodSeeing forecast. Every GoodSeeing good-window night links → *"here's what's actually up tonight."*

**Hard rule to prevent the two properties from collapsing into each other:** GoodSeeing never publishes event content; Jump Universe never publishes forecast scores. Each links to the other instead.

### What the site publishes (all computed or sourced — no invented prose)

| Page type | Unique data per page | Source |
|---|---|---|
| `/tonight/{city}` | Rise/set/altitude for every visible planet, moon phase, twilight windows, ISS passes | Skyfield + JPL ephemerides, CelesTrak TLEs |
| `/events/{year}/{event}` | Local circumstances for eclipses, showers, conjunctions, occultations | NASA eclipse canon, IMO shower calendar |
| `/aurora/{region}` | Live Kp, OVATION forecast, viewing latitude | NOAA SWPC |
| `/object/{planet\|comet\|messier}` | Position, magnitude, best viewing window per location | Minor Planet Center, COBS |
| `/launches` | Upcoming launches, visibility corridors | Launch Library 2 |

The LLM's job shrinks to writing the human-readable summary layer over verified numbers. It stops being the author and becomes the narrator. That single change is what moves you from the penalized category into the rewarded one — and it is also what makes the pages citable by AI answer engines, which preferentially cite primary data and weight recency heavily.

**Technical requirement:** explicitly allow `GPTBot`, `PerplexityBot`, `ClaudeBot`, `OAI-SearchBot` and `Applebot` in robots.txt. Blocking any one makes you invisible to that engine regardless of content quality. Your current `User-agent: * / Allow: /` is fine — just don't let anyone "harden" it later.

---

## 6. YouTube

Don't delete the channel — rename and repurpose it, but **change the format**, not just the topic. A daily template-driven auto-uploaded Short in a new subject is still inauthentic content under the current policy.

Safer and better-performing: **event-triggered, not daily.** Publish when something is actually happening — an eclipse, a strong aurora forecast, a bright comet, a close approach. That is naturally lower volume, naturally newsworthy, naturally original, and it rides real search spikes instead of fighting for attention on an empty day. Add genuine narration and original visual composition, and set the synthetic-content disclosure where AI generation is used.

Realistically: treat YouTube as a *distribution channel for event spikes*, not as a revenue line. The revenue is on-site and in the email list.

---

## 7. Housekeeping that matters

**Remove the satire archive from the index before launching.** ~207 AI-generated satirical pages sitting on the same domain is a site-wide quality liability under the scaled-content-abuse framing. Either `410` them, or `noindex` and move them to a subdomain if you're sentimental. Keep the JSON — it's a nice artifact — just don't let Google keep scoring the domain on it.

**Name risk.** "The Jump Universe" reads sci-fi/fiction more than science. It works for a wonder-and-events brand; it works against you if you want to be cited as a reference source. Mitigate with a descriptive tagline locked into every title tag and the OG metadata — something that says *sky events and what's visible tonight* in plain words — so the brand carries fiction-free intent everywhere it appears.

---

## 8. Honest expectations

- **Revenue:** modest and slow. Telescope/binocular affiliate is a real high-ticket market and should live on GoodSeeing (it already does). Jump Universe monetizes through display on event spikes and by feeding the list. Realistic year-one outcome is hundreds per month, not thousands. Anyone promising more from an autonomous site in 2026 is selling something.
- **Audience:** this is the goal most likely to be met. Event traffic is genuinely large and genuinely recurring, and an email list built off aurora and eclipse alerts is a durable asset that no algorithm change can take away.
- **Showcase:** this is quietly the highest-value outcome. "I run two integrated astronomy data products on a fully autonomous pipeline, with an accuracy page that proves the forecasts" is a credential worth more in consulting or hiring than the ad revenue will ever be. Optimize the build so it's legible to a technical reader — the method and accuracy pages on GoodSeeing are already doing this correctly, and Jump Universe should mirror that.

---

## 9. Sequencing — the part that actually decides this

Both domains are at zero. Time budget is near-zero. Launching two properties at once from that position splits the only two resources that matter — your attention and your link equity — at the exact moment concentration matters most.

So don't run §5 as a parallel launch. Run it in three phases:

**Phase 0 — this month (a few hours, one time).**
Clean up thejumpuniverse.com and then leave it alone. 410 or noindex the 207 satire editions, kill the daily satire cron and the daily Shorts upload (both are currently accruing risk, not value), and put a one-page holding page on the domain that says what's coming and captures email. Cost: near zero. Benefit: stops the domain acquiring a scaled-content-abuse profile, and stops the YouTube channel walking further into the inauthentic-content ladder.

**Phase 1 — next 90 days. GoodSeeing only.**
One product, all attention. What you're looking for is a signal that the thesis is real: does anything rank, does anyone return, does the alert list convert, do the accuracy pages get cited. Do not build the second site during this window. If GoodSeeing shows nothing in 90 days, the events layer would not have saved it, and you've saved yourself a second failed property.

**Phase 2 — only if Phase 1 shows signal.**
Bring thejumpuniverse.com up as the events layer per §5, reusing the pipeline. By then you'll know which queries actually pull, which locations matter, and what the audience asks for — so the second build is informed rather than speculative. The ephemeris code will already exist on the GoodSeeing side.

**The consolidation alternative.** If, at Phase 2, you'd rather run one thing: fold the events layer into GoodSeeing as a subsection and 301 thejumpuniverse.com into it. One domain accumulates authority faster than two, and split attention is the most common way solo projects die.

The argument against consolidating is positioning: GoodSeeing is deliberately narrow and practitioner-facing — "know before you set up the scope." Bolting "supermoon tonight!" onto it dilutes exactly the credibility that makes it good. Separate brands let each stay sharp. But that argument only earns its keep once there's an audience to segment. At zero, it's theoretical.

---

## 10. Decisions needed

1. **Confirm the sequence** (§9) — GoodSeeing alone for 90 days, Jump Universe parked but cleaned. This is the only decision that needs making now.
2. **Archive disposal** — 410 or noindex the 207 satire editions, and switch off the daily crons. Do this week.
3. **YouTube** — mothball the channel now rather than keep auto-uploading. Rename and restart it event-triggered at Phase 2.
4. **Deferred to Phase 2:** first data vertical. When you get there, start with **aurora → meteor showers → eclipses**: highest spike volume, cleanest free data, weakest incumbents.

---

*Note on evidence: the platform-policy claims in §3 are sourced from 2026 reporting on Google's March core update and YouTube's inauthentic content policy. The competitive and audience-size claims in §4 are structural arguments, not measured — both of your domains are new, and nothing here has been validated against your own traffic yet. Phase 1 exists to fix that.*
