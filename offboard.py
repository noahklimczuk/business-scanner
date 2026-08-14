"""Giving a client their site back.

The leave-behind we hand every prospect says, in print:

    "If you ever want to leave, the domain is transferred to you and you get a
     copy of every file. No exit fee, no hostage."

That is a promise made in writing to someone who has probably been burned by an
agency before, and it is most of why they said yes. It has to be one command,
because the day it gets called is the day the operator least feels like doing
it, and a promise that is technically honoured after three weeks of chasing is
not the promise that was made.

So: a zip with the whole site in it, their copy in a form they can edit, and a
plain-English sheet telling whoever they hire next exactly what they are
holding and how to put it somewhere else. No lock-in, and nothing they need us
for afterwards.
"""
from __future__ import annotations

import datetime
import io
import json
import os
import zipfile
from typing import Any, Optional

import generate

HANDOVER = """\
{title}
{rule}

This zip is everything that made up your website. It is yours. Nothing in here
needs us, and there is no account to close.

WHAT IS IN HERE

  index.html        Your website. One file. Open it in any browser to see it.
  robots.txt        Tells search engines they may index the site.
  content.json      All the words on the site, in a form that is easy to edit.
  your-words.json   The same words on their own, for whoever writes the next
                    version.
  .jpg / .png       Any photographs you supplied, at full size. They sit
                    alongside index.html because that is where the page looks
                    for them — keep them together.

HOW TO PUT IT BACK ON THE INTERNET

Any web host will take this. Upload the files as they are — there is no
database, no WordPress, no plugins and nothing to install. Hosts that will do
it free, at the time of writing: Cloudflare Pages, Netlify, GitHub Pages.

Whoever you hire next will know what to do with it in about ten minutes. If
they tell you it needs rebuilding from scratch, that is a choice they are
making, not something this requires.

YOUR DOMAIN

  {domain_line}

The domain is registered to you and transfers with you. Ask your new host or
developer for a domain transfer, and they will send you an authorisation code
request. Nothing is held on our side.

THE CONTACT FORM

{form_line}

CHANGING THE WORDS

Everything on the page also lives in content.json. Edit the text in there,
send it to whoever is looking after the site, and they can regenerate the
page — or edit index.html directly, which is a normal HTML file.

Prepared {today} by {operator}.{contact}
"""


def handover_note(lead: dict[str, Any], operator: Optional[dict[str, Any]] = None
                  ) -> str:
    operator = operator or {}
    domain = lead.get("domain")
    form = lead.get("form_enabled")

    contact = ""
    bits = [operator.get("phone"), operator.get("email")]
    if any(bits):
        contact = "\nQuestions about any of this: " + " · ".join(b for b in bits if b)

    title = f'{lead.get("name") or "Your business"} — your website'
    return HANDOVER.format(
        title=title,
        rule="=" * len(title),
        domain_line=(f"{domain} — registered in your name."
                     if domain else
                     "This site was never launched on a domain of your own."),
        form_line=(
            "The contact form posted to a small program that emailed enquiries\n"
            "to you. It is in functions/api/enquiry.js and works on Cloudflare\n"
            "Pages as-is. Your new host will need an email API key set up for\n"
            "it; any developer can do this, or you can drop the form and keep\n"
            "the phone number, which is where most of the calls come from\n"
            "anyway."
            if form else
            "This site had no contact form. The phone number on the page is\n"
            "the only way people were asked to get in touch."),
        today=datetime.date.today().strftime("%-d %B %Y"),
        operator=operator.get("legal_name") or operator.get("operator_name")
                 or "Leadsmith",
        contact=contact,
    )


def bundle(lead: dict[str, Any], *, operator: Optional[dict[str, Any]] = None,
           site_dir: Optional[str] = None) -> bytes:
    """The zip, in memory. Everything that was theirs, and nothing that was ours.

    `content.rejected.json` and `leave-behind.html` are deliberately left out:
    one is a list of claims a model tried to make about them, the other has our
    pricing and margin on it. Neither is theirs and neither would land well.
    """
    place_id = lead["place_id"]
    directory = site_dir or generate.site_dir(place_id)
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("READ-ME-FIRST.txt", handover_note(lead, operator))

        if os.path.isdir(directory):
            for name in sorted(os.listdir(directory)):
                path = os.path.join(directory, name)
                if not os.path.isfile(path) or name.startswith("."):
                    continue
                if name in ("content.rejected.json", "leave-behind.html"):
                    continue
                archive.write(path, name)

        # The words, separated out and readable, so the person who takes this
        # over does not have to dig them out of the markup.
        content = generate.load_content(place_id) if os.path.isdir(directory) else None
        if content and content.get("copy"):
            archive.writestr("your-words.json",
                             json.dumps(content["copy"], indent=2, ensure_ascii=False))
    return buffer.getvalue()


def write(lead: dict[str, Any], destination: str, **kwargs: Any) -> str:
    data = bundle(lead, **kwargs)
    if os.path.isdir(destination):
        stem = (lead.get("domain") or lead.get("name") or lead["place_id"])
        stem = "".join(c if c.isalnum() or c in "-_" else "-"
                       for c in str(stem).lower()).strip("-")
        destination = os.path.join(destination, f"{stem}-website.zip")
    with open(destination, "wb") as fh:
        fh.write(data)
    return destination
