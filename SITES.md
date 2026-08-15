# The site generator

What `leadsmith build` and `leadsmith demo` produce, and why it is built this
way. This is the game plan the rebuild follows; if the code and this file ever
disagree, the code is wrong.

## The problem with the old generator

Three templates — `trade`, `food`, `salon` — sharing one `base.html`. They
differed in how the services list was laid out and in the corner radius, and in
nothing else. A dental clinic, a tattoo studio, a driving school and a
landscaper all got the roofing page with a different accent hue.

That was the right thing to build first: it proved the pipeline. It is the
wrong thing to walk into a shop with, for two reasons.

1. **The owner is comparing the page to their competitor's, not to nothing.**
   A page that reads as "generic small-business template" loses to the
   nephew-built Wix site that at least looks like a bakery.
2. **The page had nothing on it.** Hero, services, about, three reasons,
   contact. A real site for a real business answers questions: what happens
   when you call, where do you work, what does the process look like, are you
   open Sunday. The old page had five sections and three of them were the same
   paragraph in different fonts.

And there was no demo. The operator's only artifact was the prospect's own
half-empty preview, which is a hard thing to sell from: it shows the shape of
the offer, not the ceiling of it.

## What the rebuild produces

Two things out of one engine.

**A production site.** Everything on it is true. Copy comes from the model and
goes through `review()`, which refuses invented years, credentials, ratings and
superlatives. Photographs are the owner's, licensed stock, or generated. The
page is a single self-contained HTML file, no external requests, under the
weight budget, WCAG 2.0 AA.

**A demo site.** The same engine with the dial turned up: every optional
section filled, stock photography throughout, the full page a business would
have after a year of us maintaining it. Two flavours:

- `leadsmith demo` builds twelve **showcase** sites from fictional businesses
  that ship with the tool. No API key, no spend, no real business's name on
  anything. This is the portfolio — the thing you open on the iPad before you
  say a number.
- `leadsmith build <place_id> --demo` builds the *prospect's own* site in demo
  form. Their name, their hours, their trade, richer than the production build,
  with stock photography standing in for photographs nobody has taken yet.

A demo always says it is one. There is a slim ribbon at the top of the page and
a credits line at the bottom naming every stock photograph. That is not
squeamishness — it is the thing that stops the pitch dying on "wait, that's not
my shop." You get to say "those are stock, day one is me taking photos of
yours," which is a better sentence than any headline the model can write.

## Twelve templates, one per kind of business

Not twelve colour schemes. Twelve page *structures*, each with its own hero,
its own way of laying out services, one signature section that only it has, its
own motion character and its own type personality.

| Template | Businesses | The idea | Signature |
|---|---|---|---|
| `trade` | roofing, plumbing, HVAC, electrical, concrete | The van door. Heavy condensed type, blueprint rules, black slabs. | The call plate |
| `food` | restaurants, cafés, bakeries, delis, caterers | The menu. Warm paper, serif display, ruled rows with leaders. | Menu with dot leaders |
| `salon` | hair, nails, spa, barber, lashes, brows | The studio. Airy, letterspaced small caps, big soft radii. | The service stack |
| `auto` | mechanics, body shops, tyres, detailing | The garage. Dark, monospace data, chrome edges, arc gauges. | The spec strip |
| `wellness` | gyms, yoga, PT, chiropractic, massage | The floor. Kinetic gradients, oversized numbers, timetable. | The week timetable |
| `clinic` | dental, medical, optometry, physio, vets | The waiting room. Cool, calm, enormous whitespace, soft cards. | The reassurance grid |
| `professional` | law, accounting, insurance, consulting, realty | The practice. Serif, hairlines, a numbered index of what they do. | The numbered index |
| `retail` | boutiques, florists, gift shops, hardware | The shopfront. Colour blocks, sticker badges, snapping carousel. | The scroll-snap rail |
| `home` | landscaping, lawn, cleaning, pest, pool, snow | The property. Organic masks, seasonal band, big landscape imagery. | The season band |
| `pet` | groomers, kennels, daycare, pet shops | The yard. Very large radii, tilt, warm and unserious. | The tilted card wall |
| `creative` | photographers, studios, print, events, DJs | The gallery. Cinematic dark, thin type, full-bleed frames. | The full-bleed strip |
| `education` | daycare, tutoring, music, driving schools | The classroom. Blocky shapes, chunky rounded type, bright. | The programme blocks |

Category → template is keyword matching on Google's own category string, whole
words only. `template_for()` keeps its existing contract and its existing
answers: a roofer still gets `trade`, a barber still gets `salon` and not
`food`, because "barber" contains "bar" and that bug is only fixed as long as
the word-boundary match stays.

No webfonts — a font file is a second round trip on rural LTE and the whole
promise of these pages is that they arrive. Personality comes from the system
stack instead: `ui-serif` for `food`, `professional` and `creative`,
`ui-rounded` for `pet`, `education` and `wellness`, `ui-monospace` for `auto`'s
data lines, and weight, tracking, case and scale everywhere else. Twelve pages
that do not look related, out of fonts every device already has.

## Colour: still computed, now with a category accent

`design.py` keeps its OKLCH machinery unchanged, because the argument for it
was always right: a hue from a hash plus a *computed* lightness is the only way
to ship two hundred palettes nobody reviews and have all of them legible.
Ontario's AODA makes WCAG 2.0 AA a legal obligation on the client's site, and
1.4.3 is the criterion a generated palette breaks first.

What changes is where the hue comes from. A full-wheel hash gives a dental
clinic a mustard accent as happily as a teal one. So each template declares a
**hue arc**, and the place_id picks a position inside it:

```
clinic       178–262   cool, clinical
trade         12–58    safety orange through amber
food          18–74    warm, appetising
creative     255–330   violet through magenta
…
```

Same determinism — one business, one colour, forever — with a floor under how
wrong it can look. `hue_for(place_id)` with no template still spans the whole
wheel, so nothing that depended on the old behaviour changes.

Every new token is added to `design.audit()`, and `test_design.py` sweeps every
hue of every template through it. A colour that renders on a page and is not in
that audit is untested, and that is the contract.

## Content: schema v2

v1 required `hero_headline`, `hero_sub`, `about`, `services`, `why_us`,
`cta_line`, `meta_title`, `meta_description`. All of it stays required and
means the same thing, so every `content.json` already on disk renders.

Added, all optional, all fact-free by construction:

- `process` — three or four steps of what happens after the call. Derived from
  the trade, not from the business's history.
- `faq` — three to five questions, answered from hours, address, town and
  services. The model is told, again, that it does not know anything else.
- `service_areas` — towns, from the address and the operator's home city.
- `gallery` — captions for image slots, which double as alt text.
- `hero_eyebrow`, `services_intro`, `closing_note` — short connective copy.

The fabrication check now walks the content **recursively**. v1 enumerated the
five known string fields by hand, which meant every field added later had to
remember to add itself to the check, and one that forgot would ship unchecked.
Now every string anywhere in the content object is reviewed, and a new block is
guarded the moment it exists.

Demo content for the showcase businesses ships as JSON in `demo/fixtures/`. It
is written by hand, not generated, so `leadsmith demo` costs nothing and works
with no API key at all. Those businesses are fictional, which is the only
honest way to put a testimonial or a price on a page.

## Pictures

Three tiers, best first:

1. **The owner's photographs.** Always better than anything else, and the first
   thing to collect on the day of the sale. `content.json` → `photos`.
2. **Licensed stock**, through `stock.py`. Pexels by default (the licence
   allows downloading and self-hosting, which is what keeps the page working in
   a shop with no signal), Unsplash if a key for it is configured. Curated
   search terms per template, fetched once, cached on disk, credited in the
   footer. Never Google Places photos — the licence covers display inside our
   tool, not republication on a third party's commercial site, and that is
   constraint 4 in the guide.
3. **Generated artwork**, from `visuals.py`. Deterministic per business,
   category-specific, a few kilobytes of inline SVG, and it works on a plane.

Tier 3 is not a placeholder for the other two — it is the default, and it is
built to be good enough to ship. Layered gradient meshes in the business's own
hue, grain from an SVG turbulence filter, and geometry that comes from the
trade: rooflines and pitch angles, plates and steam, ribbon curves, gauge arcs,
contour lines, gallery frames.

Stock photography is fetched at build time and never committed. If the network
is not there, or no key is configured, the build does not fail — it falls back
to tier 3 and says so.

## Motion

Everything the old generator got right here is kept, because it was the good
part: CSS scroll-driven animation, no JavaScript, no observers, nothing on the
main thread, every animation inside both `@supports (animation-timeline: view())`
and `prefers-reduced-motion: no-preference`, and every element fully visible in
its final state by default so a browser that has never heard of any of it shows
a complete page rather than a blank one.

What is added is **motion character per template**. One reveal for every page is
the animation equivalent of one layout for every page:

- `trade` — hard, fast, no overshoot. Things arrive like they were dropped.
- `salon`, `clinic` — long slow fades, slight blur, nothing snaps.
- `auto` — horizontal slide and a marquee ticker.
- `pet`, `education` — spring overshoot and a degree or two of rotation.
- `creative` — mask wipes; the image is revealed rather than faded.
- `home` — parallax drift on the landscape imagery.

Plus a scroll-progress hairline under the nav, a per-template hero entrance,
and `@media print` still killing all of it so the leave-behind PDF is not blank
below the fold.

## What must not break

These are checked by tests and they are not negotiable:

- One `<h1>`, no skipped heading levels, every section named, every list a real
  list with `role="list"`, the hours a real `<table>`, the address an
  `<address>`, unique ids everywhere.
- Every generated SVG `aria-hidden`, `role="presentation"`, `focusable="false"`.
  Every `<img>` carries alt text or an explicit `"decorative": true`.
- Skip link first, `<main tabindex="-1">`, visible focus rings, no
  `outline: none` anywhere but `main`.
- No form, no iframe, no video, no external request of any kind. The only
  off-origin link on the page is the Google Maps directions URL.
- Preview builds are `noindex,nofollow` plus a `robots.txt` disallow; launch
  strips both and adds a canonical.
- No Google rating, review count or Places photo reaches a client page, in the
  copy, the schema, or the markup.
- Production page under the weight budget with room to spare. Demo pages carry
  photographs and get their own, larger budget.

## Build order

1. `design.py` — hue arcs, template design languages, extended audit.
2. `visuals.py` — the generative art system.
3. `templates/partials/styles.css.j2` + `templates/sections/` — the core
   stylesheet and the section macro library.
4. Twelve templates and twelve theme modules.
5. `generate.py` — schema v2, recursive review, demo mode in `render()`.
6. `stock.py` — catalogue, fetch, cache, credits.
7. `demo.py` + fixtures + `leadsmith demo` + the gallery index.
8. Tests across all twelve templates, then screenshots of all twelve, then
   another pass on whatever looks wrong.
