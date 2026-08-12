# Leadsmith — Build Guide

A local prospecting and website production system. Finds businesses within a radius
that have no website, generates a real site for each one, deploys it, and tracks the
pipeline from cold lead to paying client.

You are the only user. That single fact drives every architectural decision below:
no auth, no multi-tenancy, no cloud database, no framework. A Python CLI, a SQLite
file, and a folder of static sites. Total infrastructure cost at 30 clients: under
$25/month.

---

## Part 0 — The business this tool serves

Build the business model first. The tool is worthless if the economics don't work,
and the economics determine what the tool must do.

### Pricing

Two offers. Lead with the second.

| | Setup | Monthly | 12-month value |
|---|---|---|---|
| Standard | $1,200 | $60 | $1,920 |
| No money down | $0 | $149 | $1,788 |

The no-money-down offer closes far more often with small trades, because the
objection is almost never "a website isn't worth $1,200" — it's "I don't have $1,200
sitting around this month." Same annual revenue, dramatically lower friction. Require
a 12-month term.

What the monthly fee actually covers, so you can say it out loud without flinching:
hosting, domain renewal, SSL, unlimited content edits, uptime monitoring, and Google
Business Profile upkeep. That last one matters more to them than the website.

### Unit economics

- Hosting per site: $0 (Cloudflare Pages free tier)
- Domain: ~$12/year
- Places API to find the lead: under $0.01
- Claude API to write the copy: $0.05–0.15
- **Marginal cost per client: roughly $1.20/month**

At 30 clients on the standard plan that's $1,800/month recurring against maybe $40 of
costs. The constraint is not money, it's your hours. Which is the tool's entire job:
drive time-per-site from eight hours down to under one.

### The sales motion this tool is built around

This is the part that makes the whole thing work, so don't skip it.

You do not cold-call and offer to build a website. **You build the site first, then
walk in with it on an iPad.** "I'm Noah, I build websites for trades in Newmarket. I
already built yours — here it is. Have a look. If you like it, it's live tomorrow."

Conversion on a finished, personalised artifact is in a different universe from
conversion on a pitch. It also disqualifies you fast: a business that shrugs at their
own finished website was never going to buy.

This is why generation has to be nearly free in both dollars and minutes. You're
building sites speculatively for people who may say no.

---

## Part 1 — Legal and terms constraints

Read this before writing code. Two of these constraints change the architecture.

### CASL (this is the one with teeth)

Canada's anti-spam law governs every commercial electronic message sent to a Canadian
address, with no B2B exemption and penalties up to $10M for a business. You need a
consent basis before you send.

The only basis realistically available to you is **conspicuous publication**: the
recipient published their address publicly, with no statement refusing commercial
mail, and your message relates to their business role. Here's the catch that shapes
your product: a business with no website usually has no publicly published email
address, so that basis frequently does not exist for your exact target segment.

**Architectural consequence: the tool has no bulk email feature. Do not build one.**
Channels are phone, walk-in, and physical mail (CASL covers electronic messages only).
If you do email someone whose address is genuinely published — say, on their Facebook
page — every message needs your legal name, mailing address, a working contact method,
and an unsubscribe honoured within 10 business days. Log the consent basis and the
date you established it, per lead. Build that field into the schema.

None of this is legal advice; if the outreach volume gets serious, spend an hour with
a lawyer.

### Google Places terms

- `place_id` may be stored indefinitely. Other Places content is subject to caching
  limits (treat 30 days as the ceiling) and must be refreshed or discarded.
  **Consequence: the DB stores a `refreshed_at` per lead and a `stale` command
  re-pulls or purges anything older than 30 days.**
- **Do not put Google Places photos on a client's website.** Photo usage is licensed
  for display in your app, not for republication on a third party's commercial site.
  Client sites use owner-supplied photos, licensed stock, or generated imagery.
- Don't resell the raw data or build a competing places dataset. You're using it to
  find prospects for a service business, which is fine.

### Preview sites

You're building sites carrying someone's business name and branding before they've
agreed to anything. Keep every unsold preview on a random subdomain, with
`<meta name="robots" content="noindex,nofollow">` and a `robots.txt` disallow, and
delete it if they say no. Never let a speculative site outrank or get confused with
their Google listing.

### Business admin (Ontario)

Register a sole proprietorship (~$60). You must register for HST once you pass $30k in
revenue over four quarters, and voluntary registration before that lets you claim
input credits. Get liability insurance before you take money for hosting something.

---

## Part 2 — Architecture

```
leadsmith/
├── cli.py                 # Typer entry point — every command lives here
├── config.json            # API keys, business identity, defaults (gitignored)
├── db.py                  # SQLite schema + accessors
├── prospect.py            # Places API scan → leads
├── enrich.py              # Facebook/socials check, phone validation, dedupe
├── generate.py            # Claude → copy JSON → rendered static site
├── deploy.py              # Cloudflare Pages upload + DNS
├── dashboard.py           # Renders a local HTML pipeline board
├── monitor.py             # Uptime + form-delivery checks for live sites
├── templates/
│   ├── trade.html         # Plumber, electrician, contractor
│   ├── food.html          # Restaurant, cafe, bakery
│   ├── salon.html         # Hair, nails, spa, barber
│   └── partials/          # Shared head, schema, contact block
├── sites/
│   └── <place_id>/        # Generated output, one folder per business
├── leads.db
└── reference/             # Starter implementations (see below)
```

### Stack decisions and why

**Python 3.12 + Typer CLI.** You know Python from OPS435/445. A CLI is the right shape
for a tool with one operator — no server to run, no port to remember, everything
scriptable and cron-able.

**SQLite, single file.** Backs up by copying. Handles a hundred thousand leads without
complaint. No connection strings.

**Jinja2 for client sites, no JS framework, no build step.** The output is one HTML
file, one CSS file, and the images. It loads in under half a second on a phone on
rural LTE, which is the actual use case — someone standing in their driveway looking
up a plumber. A React SPA would be slower, worse for SEO, and harder for you to hand
over if a client leaves.

**Cloudflare Pages for hosting.** Free tier covers unlimited sites, includes SSL and a
global CDN, and the API supports direct upload without a git repo.

**Claude API for copy only, never for layout.** Layout comes from your templates.
The model writes headlines, service descriptions, and the about paragraph — the parts
that must be specific to the business. Keeping structure out of the model's hands is
what makes output consistent enough to sell.

---

## Part 3 — Phase by phase

Build in this order. Each phase produces something usable on its own; do not start the
next until the current one earns its keep.

### Phase 1 — Prospecting (start here)

**Google Cloud setup**

1. Create a project, enable **Places API (New)** — the legacy Places API can't be
   enabled on new projects.
2. Create an API key, then immediately restrict it: application restriction to your IP,
   API restriction to Places API only.
3. Set a billing budget alert at $20 and a hard daily quota cap in the API console.
   Do this before your first call, not after your first surprise.

**The two constraints that shape the scan**

Nearby Search returns a maximum of 20 results per call and accepts a maximum 50km
radius, and there's no pagination on the New API's searchNearby. A single call can
therefore never enumerate a town. You must subdivide into overlapping cells — roughly
900m for a commercial strip, 1,500m for suburban, 2,500m for rural.

**Field-mask discipline is the whole cost story**

Billing is set by the most expensive field you request, so one stray field promotes the
entire call to a higher SKU. Freeze the mask in a module constant with a comment
explaining why, and never build it dynamically. Requesting `websiteUri` inside the
Nearby Search response is the key optimisation — it means you never make a per-business
Place Details call, which is what makes the cost per business scanned about a fifth of
a cent instead of two cents.

**Filtering and scoring**

Drop anything with a `websiteUri`, anything not `OPERATIONAL`, and anything matching
your franchise blocklist (a Tim Hortons without a website is not a lead). Then score.

Review count is the single best predictor. A business with 80 reviews, a 4.6 rating,
and no website has proven demand and no way to capture it — that is a warm lead
wearing a disguise. A business with 3 reviews is a hobby.

Also flag the near-miss category: businesses whose "website" is a Facebook page or a
`linktr.ee`. They already believe they need a web presence and are underserved by what
they have. Often an easier sell than a true zero.

**Acceptance:** `leadsmith scan --address "Newmarket, ON" --radius 10` prints a cost
estimate, asks for confirmation over $25, and populates `leads.db` with scored leads.

### Phase 2 — Enrichment

For each lead, before you spend money generating a site:

- Search for a Facebook or Instagram page — tells you they're reachable and gives you
  real photos to ask about
- Validate and normalise the phone number to E.164
- Detect franchise/chain membership by name pattern and drop them
- Record the CASL consent basis if any published email exists

**Acceptance:** `leadsmith enrich --limit 50` runs without API cost beyond a few
searches and improves the score.

### Phase 2.5 — Purchase propensity ranking

Prospecting tells you who *qualifies*. Propensity tells you who *buys*. They are not
the same list, and the difference is most of your time.

`reference/propensity.py` is a working implementation. Three things make it worth
using over a simple score.

**It ranks on economics, not vanity metrics.** The strongest predictor of willingness
to pay $1,200 is what one new customer is worth to that business. A roofer clears
$9,000 on a job and needs one extra lead a decade to justify the spend; a nail salon
clears $55 and needs forty. The model carries a job-value table per category and says
the payback period out loud.

**It uses competitive pressure, which is free.** This requires one schema change: the
scan currently discards businesses that *have* websites. Keep them in a separate
`market` table instead. Then for any lead you can compute "9 of 11 nearby roofing
contractors have a website and you don't." That is the single most persuasive sentence
available to you, it costs nothing extra to produce, and you already paid for the data.

**It explains itself, and the explanation is your script.** Every score comes with
ranked reasons and a suggested opening line built from the strongest one. The number
tells you who to call; the reasons tell you what to say when they pick up. A tool that
outputs `87` and nothing else has done maybe a third of the job.

Other signals in the model, roughly in order of weight:

- *Facebook page but no website* — the best segment there is. They already decided they
  need a web presence and are currently renting one. You're not selling the idea.
- *Reviews with no site* — proven demand landing on a listing with nowhere to go.
- *Listing maintenance* — hours, photos, phone all present means someone in there cares
  about being found. A bare listing often means a business that genuinely doesn't want
  more customers, which is real and unsellable.
- *Review recency* — no review in two years is a business winding down, not a prospect.
- *Rating* — below 3.5 is a negative signal. A website amplifies a reputation problem,
  and you don't want that as your portfolio piece.
- *Seasonality* — applied as a multiplier, not a bonus. Trades buy marketing just before
  their season: landscapers in February, accountants in November, gyms in December.
  During the season they're too busy to answer; after it they're broke. This alone will
  reorder your November call list substantially.

**Behavioural signals dominate everything above once you have them.** Put a one-pixel
beacon on preview sites logging views and distinct IPs. Someone who opened their
preview four times from two devices has shown it to their spouse and is going to buy —
that outranks any listing-based inference. In testing, an average-looking electrician
goes from 51 to 94 on that evidence alone, correctly leapfrogging a better-looking lead
who hasn't engaged.

**Then it learns.** `recalibrate()` runs a small regularised logistic regression over
every lead that reached `sold` or `dead`, and blends the fitted weights with the priors.
It refuses below 40 outcomes, and that refusal is a feature: with thirty samples and
thirteen signals you are fitting noise, and a confidently wrong call list is worse than
an unranked one. Store the signals that fired on each lead as `signals_json` at scoring
time so this is possible at all — you cannot reconstruct them later.

Six months in, this is the part that pays. You will discover your actual close rate by
segment, and it will not match your guesses. Mine are guesses too; the whole point of
the learning loop is to replace them with your numbers.

**Acceptance:** `leadsmith calls` prints today's fifteen highest-propensity leads with
their top three reasons and an opening line, excluding anyone touched in the last week.

### Phase 3 — Site generation

The heart of it. Target: under 60 seconds and under 15 cents per site.

**Step one — copy.** One Claude call per business. Send the name, category, address,
hours, rating, review count, and any services you can infer. Demand JSON only, with a
fixed shape: `hero_headline`, `hero_sub`, `about` (two short paragraphs), `services`
(3–6 items with name and one-line description), `why_us` (three points), `cta_line`,
`meta_title`, `meta_description`.

Prompt rules that matter: write for a homeowner in a hurry, not for a brochure. No
superlatives, no "nestled in the heart of," no invented history, no invented
credentials, no invented years-in-business. If a fact isn't in the input, it doesn't go
on the page. Fabricating "family owned since 1987" on someone's website is how you lose
the deal in the first thirty seconds of the pitch.

**Step two — render.** Pick a template by category. Inject a per-business accent colour
derived deterministically from the place_id so no two look identical. Every site gets:

- A phone number in the header that is a `tel:` link, and again as a fixed bottom bar
  on mobile. For a trade, the phone call *is* the conversion. Everything else is
  decoration.
- Hours, formatted, with an "open now" state computed client-side.
- Address with a map link.
- `LocalBusiness` JSON-LD schema with name, address, phone, hours, geo — this is what
  gets them into the local pack, and it's a genuine selling point you can name.
- Open Graph tags so the link looks right when shared on Facebook.
- `noindex` while it's a preview, stripped on launch.

**Step three — images.** Not from Google Places (see Part 1). For previews use a
tasteful category-appropriate stock set or generated imagery; on sale, the first task
is collecting the owner's real photos, which are always better anyway.

**Acceptance:** `leadsmith build <place_id>` produces `sites/<place_id>/index.html`
scoring 95+ on Lighthouse performance and accessibility, under 150KB total.

### Phase 4 — Preview and pitch

`leadsmith preview <place_id>` deploys to `<random>.pages.dev` and prints the URL and a
QR code for the terminal. You walk in with it open.

Also generate a one-page leave-behind PDF: a screenshot of their site, the URL, the
price, your name and number. Half of small business owners need to talk to a spouse or
partner before spending money, and the leave-behind is what survives that conversation.

Track every attempt as a touch with an outcome. Three touches with no answer moves the
lead to `dead` automatically — your time is the scarce resource, not leads.

### Phase 5 — Launch and management

- `leadsmith launch <place_id> --domain example.ca` — strips noindex, uploads to a
  named Pages project, walks you through the DNS records.
- Content edits: the client's content lives in `sites/<id>/content.json`. You edit that
  and re-run build + deploy. Do not build a CMS. At thirty clients, edits are a few
  minutes a week, and a CMS is a month of work plus a permanent security surface.
- Contact forms: Cloudflare Pages Functions or Formspree, delivering to the owner's
  existing email. Test delivery on launch day and monthly after — a silently broken
  form is the fastest way to lose a client.
- `leadsmith monitor` — daily uptime and form check across all live sites, alerting
  you before the client notices.

### Phase 6 — Operations

`leadsmith board` renders a local HTML dashboard: pipeline by stage, leads sorted by
score, today's call list, live sites with their status, and monthly recurring revenue.

Billing through Stripe subscriptions. Do not chase e-transfers manually past client
number five.

Write your offboarding policy now, while it's abstract: if a client leaves, they get
their domain and a zip of their site files. Say this during the pitch. It removes the
"am I trapped" objection, which is a real one for people who've been burned by an
agency before, and it costs you nothing.

---

## Part 4 — Reference implementations

`reference/db.py` and `reference/prospect.py` are working starting points that already
encode the decisions above: the frozen field mask, the grid subdivision, the scoring
weights, and the pipeline schema. Read them before writing your own version — the cost
model in particular is easy to get wrong in a way that only shows up on the bill.

They are a starting point, not finished code. Missing: the 30-day refresh logic, the
franchise blocklist, the consent-basis field, and tests.

---

## Part 5 — Sequencing this against everything else

Realistic effort: Phase 1 is a weekend. Phases 1–3 are two or three weekends. The
full system is roughly a month of evenings.

The temptation will be to build all six phases before talking to anyone. Resist it.
The correct milestone order is:

1. Build Phase 1. Scan Newmarket. Look at the list.
2. Hand-build **one** site for the best lead on that list. By hand, badly, in an
   evening. Walk in and try to sell it.
3. Only then decide whether to automate.

That third step is the one that matters. If you can't sell one hand-built site, a tool
that generates two hundred of them is a very elaborate way to produce nothing. If you
can sell one, you'll know exactly which parts of the tool to build first, because
you'll have felt where the time actually went.

One more piece of sequencing, said plainly: this is a real business but it is not fast
money. First revenue is realistically 6–10 weeks out, and it depends on walking into
places and talking to owners. The job search still outranks it on dollars per hour.
Build this on evenings, not instead of the resume.
