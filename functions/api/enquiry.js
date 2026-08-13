/**
 * The contact form's back end. A Cloudflare Pages Function, deployed with the
 * site, so an enquiry goes from the client's website to the client's inbox
 * without passing through anybody else's servers.
 *
 * That is the whole reason this exists rather than a Formspree embed. A form
 * handler sees names, phone numbers and messages from the client's customers.
 * Handing that to a third party is a decision the client did not make and
 * would not be told about, and it is not ours to take on their behalf.
 *
 * Everything is configured through the Pages project's environment variables,
 * so changing email provider is a dashboard edit and never a code change:
 *
 *   MAIL_PROVIDER     resend | postmark | mailchannels
 *   MAIL_API_KEY      the provider's key (not needed for mailchannels)
 *   MAIL_FROM         a verified sender on the client's domain
 *   MAIL_TO           where enquiries land: the owner's existing email
 *   MAIL_OPERATOR     your address. Gets the monitor probes, never the client's
 *                     enquiries.
 *   TURNSTILE_SECRET  optional; when set, a valid token is required
 *   MONITOR_TOKEN     shared secret that lets `leadsmith monitor` exercise this
 *                     path end to end without emailing the client
 *
 * The helpers below are exported so they can be tested with plain node. The
 * only part that needs a real Cloudflare is the runtime binding of `env`.
 */

const MAX = { name: 120, email: 200, phone: 40, message: 4000 };

/** Providers differ only in a URL, a header and the casing of four fields. */
export const PROVIDERS = {
  resend: {
    url: "https://api.resend.com/emails",
    headers: (key) => ({ Authorization: `Bearer ${key}`, "Content-Type": "application/json" }),
    body: (m) => ({ from: m.from, to: [m.to], subject: m.subject, text: m.text, reply_to: m.replyTo }),
  },
  postmark: {
    url: "https://api.postmarkapp.com/email",
    headers: (key) => ({ "X-Postmark-Server-Token": key, "Content-Type": "application/json", Accept: "application/json" }),
    body: (m) => ({ From: m.from, To: m.to, Subject: m.subject, TextBody: m.text, ReplyTo: m.replyTo, MessageStream: "outbound" }),
  },
  mailchannels: {
    url: "https://api.mailchannels.net/tx/v1/send",
    headers: (key) => (key ? { "X-Api-Key": key, "Content-Type": "application/json" } : { "Content-Type": "application/json" }),
    body: (m) => ({
      personalizations: [{ to: [{ email: m.to }] }],
      from: { email: m.from, name: m.fromName || undefined },
      reply_to: m.replyTo ? { email: m.replyTo } : undefined,
      subject: m.subject,
      content: [{ type: "text/plain", value: m.text }],
    }),
  },
};

export function clean(value, limit) {
  // Strip control characters: a newline in a header field is header injection,
  // and there is no legitimate reason for one in a name or an email address.
  return String(value == null ? "" : value)
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .trim()
    .slice(0, limit);
}

/**
 * Validate an enquiry. Returns { ok, fields, errors } where `errors` is keyed
 * by field name so the form can put each message beside the input it belongs
 * to — 3.3.1 wants the error identified, 3.3.3 wants it to say what to do.
 */
export function validate(raw) {
  const fields = {
    name: clean(raw.name, MAX.name),
    email: clean(raw.email, MAX.email),
    phone: clean(raw.phone, MAX.phone),
    message: clean(raw.message, MAX.message),
  };
  const errors = {};

  if (!fields.name) errors.name = "Please tell us your name.";
  if (!fields.message) errors.message = "Please say what you need doing.";

  // One way to reach them, not both. Insisting on an email address from
  // someone who only uses a phone is how a real enquiry gets abandoned.
  if (!fields.email && !fields.phone) {
    errors.email = "Add an email address or a phone number so we can reply.";
  } else if (fields.email && !/^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$/.test(fields.email)) {
    errors.email = "That email address looks incomplete — check for a typo.";
  }
  return { ok: Object.keys(errors).length === 0, fields, errors };
}

/** Bots fill in every field they find. People never see this one. */
export function looksAutomated(raw) {
  return Boolean(clean(raw.company, 200));
}

export function compose(fields, site) {
  const lines = [
    `From: ${fields.name}`,
    fields.email ? `Email: ${fields.email}` : null,
    fields.phone ? `Phone: ${fields.phone}` : null,
    "",
    fields.message,
    "",
    `— sent from ${site}`,
  ].filter((line) => line !== null);
  return {
    // The business's own name in the subject, because this lands in an inbox
    // beside forty other things and has to be recognisable at a glance.
    subject: `Website enquiry — ${fields.name}`,
    text: lines.join("\n"),
  };
}

export async function verifyTurnstile(secret, token, ip, fetcher = fetch) {
  if (!secret) return true;              // not configured; honeypot only
  if (!token) return false;
  const body = new FormData();
  body.append("secret", secret);
  body.append("response", token);
  if (ip) body.append("remoteip", ip);
  const res = await fetcher(
    "https://challenges.cloudflare.com/turnstile/v0/siteverify",
    { method: "POST", body });
  const data = await res.json().catch(() => ({ success: false }));
  return Boolean(data.success);
}

export async function send(env, message, fetcher = fetch) {
  const name = (env.MAIL_PROVIDER || "resend").toLowerCase();
  const provider = PROVIDERS[name];
  if (!provider) throw new Error(`Unknown MAIL_PROVIDER: ${name}`);
  if (!env.MAIL_FROM) throw new Error("MAIL_FROM is not set");

  const res = await fetcher(provider.url, {
    method: "POST",
    headers: provider.headers(env.MAIL_API_KEY),
    body: JSON.stringify(provider.body(message)),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${name} returned ${res.status}: ${detail.slice(0, 300)}`);
  }
  return true;
}

/** Constant-time compare, so a probe token cannot be guessed a byte at a time. */
export function tokenMatches(given, expected) {
  if (!given || !expected || given.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < given.length; i++) diff |= given.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

async function readBody(request) {
  const type = request.headers.get("content-type") || "";
  if (type.includes("application/json")) return await request.json();
  const form = await request.formData();
  return Object.fromEntries(form.entries());
}

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json; charset=utf-8",
               "cache-control": "no-store" },
  });
}

export async function handle(request, env, fetcher = fetch) {
  let raw;
  try {
    raw = await readBody(request);
  } catch {
    return json({ ok: false, errors: { message: "We could not read that." } }, 400);
  }

  // A monitor probe exercises this whole path — validation, provider, key,
  // sender domain — and lands in the operator's inbox instead of the client's.
  // Checking a form that does not send the email would test nothing worth
  // testing, and emailing the client daily would lose the client.
  const probe = clean(raw._probe, 200);
  if (probe) {
    if (!tokenMatches(probe, env.MONITOR_TOKEN || "")) {
      return json({ ok: false, error: "bad probe token" }, 403);
    }
    const site = new URL(request.url).host;
    try {
      await send(env, {
        from: env.MAIL_FROM,
        to: env.MAIL_OPERATOR || env.MAIL_TO,
        subject: `Form check — ${site}`,
        text: `The contact form on ${site} accepted a probe and sent this.\n`,
      }, fetcher);
    } catch (err) {
      return json({ ok: false, probe: true, error: String(err.message || err) }, 502);
    }
    return json({ ok: true, probe: true });
  }

  if (looksAutomated(raw)) {
    // Answer exactly as if it had worked. A bot that learns it was caught
    // comes back having removed the tell.
    return json({ ok: true });
  }

  const passed = await verifyTurnstile(
    env.TURNSTILE_SECRET, clean(raw["cf-turnstile-response"], 4000),
    request.headers.get("cf-connecting-ip"), fetcher);
  if (!passed) {
    return json({ ok: false,
                  errors: { form: "The spam check did not pass. Please try again." } }, 400);
  }

  const { ok, fields, errors } = validate(raw);
  if (!ok) return json({ ok: false, errors }, 400);

  const site = new URL(request.url).host;
  const { subject, text } = compose(fields, site);
  try {
    await send(env, {
      from: env.MAIL_FROM,
      fromName: site,
      to: env.MAIL_TO,
      // So the owner can just hit reply. This is the single most useful line
      // in the whole function.
      replyTo: fields.email || undefined,
      subject,
      text,
    }, fetcher);
  } catch (err) {
    // Never show the provider's error to a customer — it can carry the key's
    // account details — but do fail loudly enough that `monitor` sees it.
    console.error("enquiry send failed:", err);
    return json({ ok: false,
                  errors: { form: "Something went wrong sending that. "
                                  + "Please phone us instead." } }, 502);
  }
  return json({ ok: true });
}

export const onRequestPost = ({ request, env }) => handle(request, env);

// Without this, a GET falls through to the static asset handler and answers
// 200 with the site's index page — which would let `monitor` report a healthy
// form on a site where the Function failed to deploy at all. An explicit 405
// is how the endpoint proves it exists.
export const onRequestGet = () =>
  new Response("This endpoint accepts POST from the contact form.",
               { status: 405, headers: { allow: "POST", "cache-control": "no-store" } });
