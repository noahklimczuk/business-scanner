# Offboarding policy

**If a client leaves, they get their domain and every file, at no cost, without
having to ask twice.**

That is the policy. It is written down here, before there is a client to apply
it to, because a policy written while someone is walking out is a negotiation
rather than a policy.

## Why it exists

Say it out loud during the pitch. It removes the "am I trapped" objection,
which is real and common — a lot of small businesses have been through an
agency that held the domain hostage, or that "built" a site the owner turned
out not to own, and they are waiting for the catch.

It costs nothing. Nothing here is worth keeping from someone who wants to go,
and a client who leaves cleanly refers people. A client who leaves angry tells
the same number of people, in the other direction, in a town where everybody
knows everybody.

It is printed on the leave-behind we hand every prospect, in these words:

> If you ever want to leave, the domain is transferred to you and you get a
> copy of every file. No exit fee, no hostage.

So it is a written commitment made to a stranger before they paid us anything.
That is exactly the kind of promise that has to be honoured on the worst day
rather than the convenient one.

## What they get

    leadsmith export <place_id> --out ~/Desktop

One zip, containing:

| | |
|---|---|
| `index.html` | The website. One file, opens in any browser. |
| `robots.txt` | |
| `content.json` | Every word on the site, in an editable form. |
| `your-words.json` | The copy on its own, for whoever writes the next version. |
| photographs | Anything they supplied, at full size. |
| `READ-ME-FIRST.txt` | Plain English: what this is, how to host it, how the domain transfers, what to do about the contact form. |

**What is deliberately not in it:** `leave-behind.html`, which has our pricing
and margin on it, and `content.rejected.json`, which is a list of the claims a
language model tried to make about their business before the check stopped it.
Neither is theirs and neither would land well.

## The domain

The domain is registered in the client's name from the start. That is the
decision that makes the rest of this easy — a domain registered to us and
"transferred on request" is the same hostage situation with better manners.

When they leave, they ask their new registrar for a transfer and we approve it.
There is nothing to unpick.

## The rest of it

1. Run the export and send it.
2. Approve the domain transfer when it comes through.
3. Cancel the Stripe subscription. `leadsmith billing --sync` exists partly for
   this: the failure it is most likely to catch is **us still charging someone
   who thinks they have left**, which is the single worst way to end a
   relationship with a local business.
4. `leadsmith unpreview` if a preview is still up anywhere.
5. Take the site down from our Cloudflare account only after their new host is
   answering. An hour of downtime during a handover is remembered as "the
   website broke when I left".

## What we do not do

- No exit fee, no "release fee", no charge for the export.
- No holding the domain, the DNS or the files pending a final invoice.
- No deleting anything until they confirm they have it.
- No asking why. If they want to go, they go.
