# Invoicing

**The tool raises the invoice, prints it, and remembers what was paid. The
money arrives however you and the client already agreed.**

There is no service behind this. Nothing is emailed for you, nothing takes a
percentage, and no account has to exist anywhere before you can bill somebody.
Stripe stays optional and read-only, exactly as it always was — `leadsmith
billing --sync` reconciles the subscriptions you set up by hand, and knows
nothing about any of this.

## Why it works this way

Most of these clients pay by e-transfer or hand over a cheque at the counter.
An operator with six clients is not going to open a merchant account to send
one $149 invoice, and if the only way to bill someone is through a service that
wants a business number and a percentage, the invoice does not get sent at all.

So the numbers live in `leads.db` with everything else, and the document is an
HTML page you print from the browser — the same call the leave-behind makes,
for the same reasons. Every machine can print to PDF. A PDF library would be a
heavy dependency, a font problem, and a new way for the total to come out in
the wrong place.

## Set it up once

In `config.json`:

```json
"invoicing": {
  "prefix": "",
  "terms_days": 14,
  "tax_label": "HST",
  "tax_rate": 13,
  "tax_number": "",
  "e_transfer_email": "you@example.ca",
  "cheque_payable_to": "Your Name o/a Leadsmith",
  "footer": ""
}
```

Two of these matter more than the rest.

**`tax_number` is what turns tax on.** You may not collect GST/HST unless you
are registered for it, and most people under $30k a year are not. Leave it
empty and no tax is charged and none is printed, whatever `tax_rate` says. Fill
it in the day you register, and every invoice from then on charges the rate and
prints the number — which is what the CRA requires of anyone who does.

**Fill in at least one way to pay.** With neither an e-transfer address nor a
payee for cheques, the invoice prints with no way to pay it on the page. The
CLI and the app both say so at the time, but it is worth setting once.

The rest of the letterhead comes from the `business` block you already filled
in for the leave-behind.

## Billing a month

    leadsmith invoice run

Raises and issues this month's invoice for every active client, and writes each
page into `invoices/`. **It is safe to run twice.** A client who already has an
invoice for the month is skipped, and the database refuses a second one even if
the check is bypassed — one live invoice per client per period is a constraint
in the schema, not a habit.

Use `--dry-run` to see who would be billed, and `--draft` to look them over
before they are issued.

In the app: **Invoices → Invoice everyone for this month**, which asks first and
tells you who it skipped.

## Billing one thing

    leadsmith invoice new <place_id> --line "Extra page:250" --line "Photography:120:2.5"

Repeat `--line` for each. The form is `what it is for:amount`, with an optional
quantity after the price. A negative amount is a discount:

    --line "Late last month, sorry:-50"

`--from-plan` starts from what the client already pays. Invoices are issued
immediately unless you pass `--draft`, because an invoice nobody sent is an
invoice nobody pays.

## Getting paid

    leadsmith invoice pay 2026-004 149 --method e-transfer --ref CA9931

Part payments are ordinary — record each one as it lands. The invoice settles
itself when the balance reaches zero, and not before: nothing here asks you to
remember whether it was paid in full.

    leadsmith invoice list --outstanding

is what is still owed, oldest overdue first. The same figure sits on the Money
page and on `leadsmith board`, next to MRR, because they answer different
questions — MRR is what clients are worth, outstanding is what they owe, and
only one of the two goes quiet when somebody stops paying.

## Getting one wrong

    leadsmith invoice void 2026-004 --reason "billed the wrong month"

Nothing is ever deleted. A void keeps its number, prints as void with the
reason on it, and frees its month so the run can raise it again. A gap in a
numbered run is the first thing anybody reading a year of invoices asks about.

An invoice with a payment against it cannot be voided at all — that would
quietly erase money that actually arrived.

Once an invoice has been issued its lines cannot change, because the client is
holding a copy of them. Void it and raise a new one.

## What the tool is careful about

- **Cents, as integers, everywhere.** A subtotal a cent under the sum of its
  own lines is an argument with somebody holding the paper.
- **Tax once, on the taxable subtotal** — not per line and then added up, which
  drifts a cent or two from the figure a client gets checking it themselves.
- **The client is copied onto the invoice, not joined to it.** Google's copy of
  a business expires after 30 days and `purge_stale` blanks it; an invoice has
  to say the same thing in a year as it did the day it was sent.
- **Overdue is arithmetic, not a column.** It is worked out against today every
  time it is asked, so nothing can be stale by a day.

## For the books

    leadsmith invoice export --year 2026 --out ~/Desktop/2026-invoices.csv

Every invoice with its subtotal, tax, total, paid and balance, in dollars,
which is the one output somebody who did not write this tool has to read.

## Where things are

| | |
|---|---|
| `invoices/` | The printed pages, beside the database. Gitignored. |
| `leads.db` | The record: invoices, their lines, and every payment. |

Back the two up together. And note that the page in `invoices/` is a snapshot
of the day it was written — `leadsmith invoice show <number> --open`, or **Open**
in the app, reprints it as it stands now with the payments on it.
