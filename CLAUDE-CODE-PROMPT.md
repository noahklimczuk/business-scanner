# Claude Code — Build Prompt: Leadsmith

Paste everything below the line into Claude Code in an empty directory, with
`BUILD-GUIDE.md` and `reference/` present in that directory.

Build one phase per session. Starting all six at once produces a large amount of
untested code. The prompt is written so you can say "do Phase 1" and stop.

---

## ROLE

You are building a production tool for a solo operator, not a demo and not a SaaS. The
operator is the only user. He is a Computer Systems Technician student with solid Python,
Linux, and Azure experience, and ships iOS apps in Swift — so write real code and skip
the beginner explanations. He will run this on Windows and on a Linux VM.

Read `BUILD-GUIDE.md` in full before writing anything. It contains the business model,
the legal constraints, and the architecture. Read `reference/db.py` and
`reference/prospect.py` — they encode cost and schema decisions that are expensive to
get wrong. Treat them as a starting point to extend, not gospel to preserve.

## WHAT THE TOOL DOES

Finds businesses in a geographic radius that have no website, scores them as sales
leads, generates a complete static website for each, deploys it to a preview URL so the
operator can walk in and pitch it in person, then manages the site once it sells.

## NON-NEGOTIABLE CONSTRAINTS

These come from law, vendor terms, or hard-won cost lessons. Do not design around them,
do not make them configurable, and do not "improve" them.

1. **No bulk email feature. Ever.** Canada's CASL governs commercial electronic messages
   with no B2B exemption and penalties to $10M. The target segment — businesses with no
   website — usually has no conspicuously published email address, so the implied-consent
   basis typically does not exist. Outreach is phone, walk-in, and physical mail. If you
   find yourself writing an SMTP client, stop and ask.

2. **The Places field mask is frozen.** Billing is set by the most expensive field
   requested. The mask lives in one module constant with a comment saying why. Never
   build it dynamically, never add a field without flagging the cost implication in your
   response.

3. **Never call Place Details per business.** Nearby Search returns `websiteUri` in the
   same response. A per-business Details call multiplies cost by roughly ten for zero
   additional value.

4. **Never put Google Places photos on a client website.** Photo licensing covers display
   in the app, not republication on a third party's commercial site. Client sites use
   owner-supplied images, licensed stock, or generated imagery.

5. **Google content caching is limited.** `place_id` may be stored indefinitely; other
   fields get a `refreshed_at` timestamp and a command that re-pulls or purges anything
   older than 30 days.

6. **Every preview site is `noindex,nofollow` plus a `robots.txt` disallow**, on a random
   subdomain, deleted if the prospect declines. These carry a real business's name before
   consent; they must never compete with that business's own listing.

7. **The copy model never invents facts.** No fabricated founding years, credentials,
   certifications, awards, staff counts, or history. If it isn't in the input data, it
   doesn't appear on the page. A single invented "family owned since 1987" kills the sale
   in the first thirty seconds. Put this in the system prompt and add a post-generation
   check that flags year patterns and credential words not present in the source data.

8. **Cost visibility before every paid operation.** Any command that spends money prints
   an estimate first and requires confirmation above $25. Log actual spend per run to the
   `scans` table.

## STACK

Python 3.12. Dependencies: `typer`, `requests`, `jinja2`, `rich`, `phonenumbers`,
`python-dotenv`. Standard library `sqlite3`. Nothing else without asking.

Client sites are plain HTML and CSS — no JS framework, no build step, no runtime
dependencies. Target: under 150KB total, Lighthouse 95+ on performance and
accessibility, first contentful paint under 1s on simulated 4G. The real user is
someone standing in a driveway on bad LTE looking for a plumber.

Hosting is Cloudflare Pages via direct upload API. No git integration.

## CODE STANDARDS

- Type hints on every function signature. Docstrings on modules and non-obvious
  functions only — no docstring that restates the function name.
- Comments explain *why*, never *what*. The cost, legal, and API-quirk decisions
  above are exactly what deserves a comment.
- All API calls: explicit timeout, retry with backoff on 429 and 5xx, and a clear
  exception message that names the failing service and what to do about it.
- No secrets in code. `config.json` is gitignored; ship `config.example.json`.
- Errors the operator will see are written for a human at 9pm: what broke, and the
  next action. Not a stack trace.
- `pytest` tests for the pure logic — grid generation, scoring, phone normalisation,
  content validation, template rendering against a fixture. Do not write tests that
  hit paid APIs; mock the responses.
- Commit at the end of each phase with a real message.

## BUILD ORDER

Do one phase per session. At the end of each, tell me what to run to verify it and stop.

**Phase 1 — Prospect.** `db.py`, `prospect.py`, `cli.py` with `scan` and `list`.
Geocode a text address to coordinates. Subdivide the radius into overlapping cells
(Nearby Search caps at 20 results and 50km, with no pagination). Batch the included
types. Filter out anything with a website, anything not `OPERATIONAL`, and a franchise
blocklist. Score by review count, rating, phone presence, hours presence, and category
value. Flag the "Facebook page only" near-miss segment separately — they already believe
they need a web presence and often convert more easily.
*Verify:* `leadsmith scan --address "Newmarket, ON" --radius 10 --dry-run` prints a cost
estimate and call count without spending anything.

**Phase 2 — Enrich.** Social presence check, phone normalisation to E.164, chain
detection, CASL consent-basis field populated only where a published address genuinely
exists.

**Phase 2.5 — Purchase propensity.** Port and extend `reference/propensity.py`.
Requires two schema changes: a `market` table storing businesses that *do* have websites
(the scan currently throws them away — you need them to compute competitive density),
and `propensity`, `propensity_band`, `signals_json`, `opener` columns on `leads`.
Persist `signals_json` at scoring time; the learning loop cannot reconstruct it later.
Add a preview beacon — a one-pixel request from preview sites logging view count and
distinct IPs into a `preview_views` table — and feed it in as behavioural input.
Behavioural signals must be able to override strong static ones; verify that a mediocre
listing with heavy preview engagement outranks a great listing with none.
Implement `recalibrate()` with the 40-outcome floor intact. Do not remove that floor
and do not add sklearn — the pure-Python logistic regression is deliberate, since this
runs on a laptop and needs no environment to reproduce.
*Verify:* `leadsmith calls` prints fifteen leads with score, band, top three reasons,
and an opening line, excluding anyone touched within seven days.

**Phase 3 — Generate.** This is the phase that matters most; spend the effort here.
One Claude API call per business returning JSON only, with the fixed content shape in
the guide. Three Jinja templates (trade, food, salon). Deterministic per-business accent
colour derived from `place_id`. Every site needs: `tel:` link in the header and a fixed
mobile call bar, formatted hours with a client-side "open now" state, map link,
`LocalBusiness` JSON-LD, Open Graph tags, and the noindex block.
Design the templates like a designer would, not like a developer picking defaults.
Read `/mnt/skills/public/frontend-design/SKILL.md` if it exists in your environment.
These have to look better than what a $2,000 agency in Newmarket ships, because that is
the actual competition and the operator is walking in with it on an iPad.
*Verify:* `leadsmith build <place_id>` produces a site that passes Lighthouse targets.

**Phase 4 — Preview.** Deploy to a random `pages.dev` subdomain, print URL and a
terminal QR code, generate a one-page leave-behind PDF with a screenshot, price, and
contact details. Touch logging with outcomes; auto-move to `dead` after three no-answers.

**Phase 5 — Launch and manage.** Named Pages project, custom domain with DNS
instructions, noindex stripped. Content edits happen by editing `content.json` and
re-running build and deploy — **do not build a CMS**, and push back if I ask for one.
Contact form via Pages Functions delivering to the owner's existing email, with a
delivery test.

**Phase 6 — Operations.** `leadsmith board` renders a local HTML dashboard: pipeline by
stage, today's call list sorted by score, live sites with uptime status, MRR. Daily
`monitor` command checking uptime and form delivery across live sites.

## HOW TO WORK WITH ME

- Ask before adding a dependency, changing the schema, or touching the field mask.
- If a constraint above blocks something I've asked for, say so and explain the
  tradeoff rather than quietly working around it.
- When a decision has real consequences — cost, legal exposure, or a thing that's
  painful to reverse later — surface it instead of picking for me.
- Prefer boring, obvious code. This has one user and needs to be repairable at 11pm
  six months from now when I've forgotten how it works.
- Don't write a README until Phase 3 works.

Start with Phase 1. Read the guide and the reference files first, tell me anything in
them you think is wrong, then build.
