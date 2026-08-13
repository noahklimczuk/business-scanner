# Accessibility

Every site this tool generates is built to **WCAG 2.0 Level AA**.

That is not a quality bar we chose. Ontario's *Accessibility for Ontarians with
Disabilities Act* — specifically the Integrated Accessibility Standards
Regulation, O. Reg. 191/11 s. 14 — requires WCAG 2.0 Level AA of the public
websites of private-sector organisations with 50 or more employees, and the
province has been steadily widening who is covered. The businesses this tool
prospects are small, and the obligation may not bite for all of them today.
It is still the right floor, for two reasons that have nothing to do with the
regulation:

- a roofer's customers include people with cataracts, tremors and hearing
  aids, and they are the ones who most need a phone number they can read; and
- we are handing someone a website. If it is inaccessible, that is a liability
  we created for them, quietly, in exchange for money.

Two criteria in WCAG 2.0 are exempted by the regulation itself — 1.2.4 (live
captions) and 1.2.5 (audio description). Neither has a subject here: the pages
carry no audio or video at all.

## Where it is enforced

Accessibility is not a review step. Nobody eyeballs one of these pages before
it reaches a client, so anything that depends on someone remembering is already
broken. It is enforced in four places, in decreasing order of how much work
they do:

**`design.py` — the palette is computed, not chosen.** Each site's accent hue
comes from its `place_id`, and every other colour is derived by pushing
lightness until the contrast ratio clears 4.5:1 against the darkest surface the
colour can land on. `design.audit()` enumerates every foreground/background
pair the templates actually put together — including the pairs that only exist
inside a dark section, and the one behind the frosted nav — and
`tests/test_design.py` asserts all of them across 900 palettes. A colour that
renders on a page and is not in that list is untested, so adding a colour to
the stylesheet means adding its pair to the audit.

**`templates/partials/styles.css.j2` — a dark section re-points the colour
tokens** rather than restating colours component by component. `.on-night` sets
`--ink`, `--muted`, `--surface`, `--accent-ink` and `--focus` to their night
equivalents, so anything placed inside it resolves correctly without a new
rule. The alternative — one override per component — is right until someone
moves a component into a dark section nobody wrote an override for, and it
fails silently.

**`generate.py` — the build refuses to ship a page with a barrier in it.**
`accessibility_issues()` runs on every render, and a photo with no alt text
fails the build with an error that says what to write. It is a failure and not
a warning because an operator working through forty sites scrolls past
warnings.

**`tests/test_a11y.py` — 93 tests, each naming its success criterion.** They
parse the rendered HTML rather than grepping it, so they assert about elements
and their ancestry. A failure tells you which obligation broke.

## What has actually been run

Not a claim about the code — output measured in a browser.

- **axe-core**, `wcag2a` + `wcag2aa` rule tags only (exactly what AODA cites —
  not WCAG 2.1, not axe's best-practice rules): **56 audits, 0 violations.**
  Three templates × preview and launched × 390px and 1440px × reduced motion
  and full motion × top-of-page and fully-scrolled.
- **Lighthouse** on a launched page over HTTP: **accessibility 100**,
  performance 100, best practices 100, SEO 100.
- **200% text on a 390px phone**, which found two real failures that are now
  fixed: the display headline overflowed and was clipped by `overflow-x: clip`,
  and the fixed call bar covered the footer's address and phone number. Both
  were loss of content under 1.4.4.

`tools/audit_a11y.py` re-runs the axe pass on demand. It is a dev script, not
part of the tool — it needs `playwright` and `npm i axe-core`, neither of which
is a dependency of leadsmith. Use it when a client, or a client's lawyer, wants
a report from a tool they recognise:

```
python tools/audit_a11y.py 'sites/*/index.html' --json report.json
```

Run it *after* the page settles, which it does automatically. axe reads
composited pixels, so auditing during the first frames measures a fade in
progress and reports every animating heading as a contrast failure. That is the
scanner sampling a transition, not a finding — but it is worth knowing why
before it turns up in someone's inbox.

## Notes on specific criteria

**1.1.1 Non-text Content.** The generated artwork is decoration and is marked
`aria-hidden`. It used to carry `aria-label="Halstead Roofing artwork"`, which
is a failure dressed up as compliance — it conveys nothing a sighted visitor
gets, and announcing it just makes a screen reader read the business name a
fourth time before the copy starts. Owner photography is different and carries
real alt text, which the build enforces.

**1.4.1 Use of Color.** Links are underlined unless they carry a second signal
— a button shape, a chevron, a place in the nav bar. The open/closed pill says
"Open now" or "Closed · opens 8am" rather than relying on the dot's colour, and
today's row in the hours table gets a "(today)" that a screen reader hears.

**1.4.3 Contrast (Minimum).** The frosted bars are the interesting case. They
are translucent, so what is behind them is whatever happens to be scrolling
past — including a true-black section, which drags the bar down to about
`#e7e7e9`. The ordinary muted grey measures 4.91:1 on the page and 4.11:1 over
that, which is a fail, so text on those bars uses a darker grey and the
accent-as-text colour is darkened against that composite too. `CHROME_ALPHA`
lives in `design.py` and is templated into the CSS, so the stylesheet and the
contrast check cannot drift apart.

**1.4.4 Resize Text.** Every display-type clamp floor is itself capped against
the viewport — `min(2.75rem, 11vw)` rather than a bare `2.75rem` — because a
rem floor does not know how wide the screen is. Body copy is left in rem and
scales the whole way. The mobile call bar is `sticky`, not `fixed`, so it
reserves its own height at the end of the document and cannot cover the footer
at any text size.

**2.4.1 Bypass Blocks.** The skip link's target carries `tabindex="-1"`.
Without it WebKit scrolls but leaves focus on the link, and the next Tab goes
straight back into the nav the visitor was trying to skip — the link appears to
do nothing.

**2.4.7 Focus Visible.** The focus ring is a token, so a dark section
re-points it: an ink-dark outline on a true-black background is not a focus
indicator. A companion halo ring keeps it visible over accent-filled buttons.
There is a `:focus` fallback for Safari before 15.4.

**Motion.** Every animation lives inside `prefers-reduced-motion:
no-preference` and inside `@supports (animation-timeline: view())`. A browser
that has never heard of scroll-driven animation renders a complete static page
rather than a blank one. Print rules disable the reveals as well, because a
scroll-driven animation holds its `from` state until its range is entered and
print never scrolls — without that, the leave-behind comes out blank below the
first screen.

## What a machine cannot check

Roughly a third of WCAG cannot be automated, and two of those criteria matter
here more than anything above.

**Alt text has to be accurate, not merely present.** The build can refuse an
empty alt, reject "image" and "photo", and reject the filename. It cannot tell
whether "New cedar shakes on a century home" is what the photograph shows. Only
the person who took it knows.

**Copy has to be readable.** WCAG 2.0's reading-level criterion (3.1.5) is
Level AAA and not required, but plain writing is the single biggest
accessibility win available on a page like this, and it is what the copy prompt
is for. Short sentences, no jargon, the phone number early.

Both stay the operator's job. This document exists so that is a decision rather
than an oversight.
