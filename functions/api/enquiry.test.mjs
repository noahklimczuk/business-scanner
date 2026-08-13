/**
 * Tests for the enquiry Function, run with plain `node --test`.
 *
 * They call `handle()` with real Request objects and a stub fetch, so the
 * validation, the honeypot, the Turnstile branch, the probe path and every
 * provider's payload are exercised for real. Only the Cloudflare binding of
 * `env` is simulated, and that is a plain object at runtime too.
 *
 *     node --test functions/api/enquiry.test.mjs
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  PROVIDERS, clean, compose, handle, looksAutomated, send, tokenMatches,
  validate, verifyTurnstile,
} from "./enquiry.js";

const ENV = {
  MAIL_PROVIDER: "resend",
  MAIL_API_KEY: "re_test",
  MAIL_FROM: "site@klimczukroofing.ca",
  MAIL_TO: "owner@klimczukroofing.ca",
  MAIL_OPERATOR: "noah@leadsmith.ca",
  MONITOR_TOKEN: "probe-token-value",
};

const GOOD = { name: "Bob Smith", email: "bob@example.ca", message: "My roof leaks." };

/** Records every outbound call and answers however the test needs. */
function stubFetch(reply = { ok: true, status: 200, json: {} }) {
  const calls = [];
  const fetcher = async (url, init) => {
    calls.push({ url, init });
    return {
      ok: reply.ok,
      status: reply.status,
      json: async () => reply.json,
      text: async () => JSON.stringify(reply.json),
    };
  };
  fetcher.calls = calls;
  return fetcher;
}

function post(body, { json: asJson = true } = {}) {
  if (asJson) {
    return new Request("https://klimczukroofing.ca/api/enquiry", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  }
  const form = new FormData();
  for (const [k, v] of Object.entries(body)) form.append(k, v);
  return new Request("https://klimczukroofing.ca/api/enquiry",
                     { method: "POST", body: form });
}

// ---------------------------------------------------------------------------
test("a complete enquiry is emailed to the owner", async () => {
  const fetcher = stubFetch();
  const res = await handle(post(GOOD), ENV, fetcher);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { ok: true });

  assert.equal(fetcher.calls.length, 1);
  const sent = JSON.parse(fetcher.calls[0].init.body);
  assert.deepEqual(sent.to, ["owner@klimczukroofing.ca"]);
  // Reply-to is the customer, so the owner can just hit reply. This is the
  // single most useful line in the whole function.
  assert.equal(sent.reply_to, "bob@example.ca");
  assert.match(sent.text, /My roof leaks\./);
  assert.match(sent.subject, /Bob Smith/);
});

test("a form post works as well as a JSON one", async () => {
  const fetcher = stubFetch();
  const res = await handle(post(GOOD, { json: false }), ENV, fetcher);
  assert.equal(res.status, 200);
  assert.equal(fetcher.calls.length, 1);
});

test("a phone number is enough — an email address is not required", async () => {
  const fetcher = stubFetch();
  const res = await handle(
    post({ name: "Bob", phone: "905 555 0143", message: "Need a quote." }),
    ENV, fetcher);
  assert.equal(res.status, 200);
  const sent = JSON.parse(fetcher.calls[0].init.body);
  assert.match(sent.text, /905 555 0143/);
  assert.equal(sent.reply_to, undefined);
});

test("missing fields come back keyed by field, with what to do about it", async () => {
  const fetcher = stubFetch();
  const res = await handle(post({ name: "", message: "" }), ENV, fetcher);
  assert.equal(res.status, 400);
  const body = await res.json();
  assert.equal(body.ok, false);
  // 3.3.1 identifies the field; 3.3.3 says what to do. Both need the error to
  // belong to a named input rather than being one sentence at the top.
  assert.ok(body.errors.name && body.errors.message && body.errors.email);
  assert.equal(fetcher.calls.length, 0, "nothing should be sent");
});

test("a mistyped email is called out rather than silently dropped", async () => {
  const res = await handle(post({ ...GOOD, email: "bob@example" }), ENV, stubFetch());
  const body = await res.json();
  assert.match(body.errors.email, /typo/);
});

test("the honeypot answers as if it worked", async () => {
  // A bot that learns it was caught comes back having removed the tell.
  const fetcher = stubFetch();
  const res = await handle(post({ ...GOOD, company: "Acme SEO" }), ENV, fetcher);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { ok: true });
  assert.equal(fetcher.calls.length, 0, "no email for a bot");
});

test("control characters cannot be smuggled into the headers", async () => {
  const fetcher = stubFetch();
  await handle(post({ ...GOOD, name: "Bob\r\nBcc: someone@else.com" }), ENV, fetcher);
  const sent = JSON.parse(fetcher.calls[0].init.body);
  assert.ok(!sent.subject.includes("\r"));
  assert.ok(!sent.subject.includes("\n"));
});

test("oversized input is truncated, not rejected", async () => {
  const fetcher = stubFetch();
  await handle(post({ ...GOOD, message: "x".repeat(99999) }), ENV, fetcher);
  const sent = JSON.parse(fetcher.calls[0].init.body);
  assert.ok(sent.text.length < 5000);
});

// ---------------------------------------------------------------------------
test("Turnstile is enforced when it is configured", async () => {
  const env = { ...ENV, TURNSTILE_SECRET: "secret" };
  const res = await handle(post(GOOD), env, stubFetch());
  assert.equal(res.status, 400, "no token should fail");

  const fetcher = stubFetch({ ok: true, status: 200, json: { success: true } });
  const ok = await handle(
    post({ ...GOOD, "cf-turnstile-response": "token" }), env, fetcher);
  assert.equal(ok.status, 200);
  assert.match(fetcher.calls[0].url, /siteverify/);
});

test("a rejected Turnstile token does not send", async () => {
  const fetcher = stubFetch({ ok: true, status: 200, json: { success: false } });
  const res = await handle(post({ ...GOOD, "cf-turnstile-response": "bad" }),
                           { ...ENV, TURNSTILE_SECRET: "secret" }, fetcher);
  assert.equal(res.status, 400);
  assert.equal(fetcher.calls.length, 1, "verify only, no send");
});

test("without a Turnstile secret the honeypot carries it alone", async () => {
  assert.equal(await verifyTurnstile("", "", null), true);
});

// ---------------------------------------------------------------------------
test("a monitor probe emails the operator, never the client", async () => {
  const fetcher = stubFetch();
  const res = await handle(post({ _probe: ENV.MONITOR_TOKEN }), ENV, fetcher);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { ok: true, probe: true });
  const sent = JSON.parse(fetcher.calls[0].init.body);
  assert.deepEqual(sent.to, ["noah@leadsmith.ca"]);
  assert.ok(!sent.to.includes("owner@klimczukroofing.ca"));
});

test("a probe with the wrong token sends nothing", async () => {
  const fetcher = stubFetch();
  const res = await handle(post({ _probe: "wrong-token-value" }), ENV, fetcher);
  assert.equal(res.status, 403);
  assert.equal(fetcher.calls.length, 0);
});

test("a probe reports the provider's failure so monitor can see it", async () => {
  const fetcher = stubFetch({ ok: false, status: 422, json: { message: "domain not verified" } });
  const res = await handle(post({ _probe: ENV.MONITOR_TOKEN }), ENV, fetcher);
  assert.equal(res.status, 502);
  const body = await res.json();
  assert.match(body.error, /422/);
});

test("token comparison does not leak length or content", () => {
  assert.equal(tokenMatches("abc", "abc"), true);
  assert.equal(tokenMatches("abc", "abd"), false);
  assert.equal(tokenMatches("ab", "abc"), false);
  assert.equal(tokenMatches("", ""), false);
  assert.equal(tokenMatches("x", ""), false);
});

// ---------------------------------------------------------------------------
test("a provider failure never shows the customer the provider's error", async () => {
  // That text can carry the API key's account details.
  const fetcher = stubFetch({ ok: false, status: 401, json: { message: "Invalid API key re_live_abc" } });
  const res = await handle(post(GOOD), ENV, fetcher);
  assert.equal(res.status, 502);
  const body = JSON.stringify(await res.json());
  assert.ok(!body.includes("re_live_abc"));
  assert.match(body, /phone us instead/);
});

test("every provider builds a payload with the four things that matter", async () => {
  for (const name of Object.keys(PROVIDERS)) {
    const fetcher = stubFetch();
    await send({ ...ENV, MAIL_PROVIDER: name }, {
      from: "site@x.ca", to: "owner@x.ca", replyTo: "bob@example.ca",
      subject: "Website enquiry — Bob", text: "My roof leaks.",
    }, fetcher);
    const body = fetcher.calls[0].init.body;
    assert.match(body, /owner@x\.ca/, name);
    assert.match(body, /bob@example\.ca/, name);
    assert.match(body, /My roof leaks\./, name);
    assert.match(body, /Website enquiry/, name);
    assert.equal(fetcher.calls[0].url, PROVIDERS[name].url, name);
  }
});

test("an unknown provider fails loudly rather than silently not sending", async () => {
  await assert.rejects(
    () => send({ ...ENV, MAIL_PROVIDER: "carrier-pigeon" }, {}, stubFetch()),
    /Unknown MAIL_PROVIDER/);
});

test("a missing sender address fails loudly", async () => {
  await assert.rejects(() => send({ MAIL_PROVIDER: "resend" }, {}, stubFetch()),
                       /MAIL_FROM/);
});

// ---------------------------------------------------------------------------
test("helpers behave on their own", () => {
  assert.equal(clean(null, 10), "");
  assert.equal(clean("  spaced  ", 10), "spaced");
  assert.equal(clean("abcdefghijk", 5), "abcde");
  assert.equal(looksAutomated({ company: "x" }), true);
  assert.equal(looksAutomated({}), false);
  assert.equal(validate(GOOD).ok, true);
  assert.match(compose({ name: "Bob", email: "b@x.ca", phone: "", message: "Hi" },
                       "klimczukroofing.ca").text, /sent from klimczukroofing\.ca/);
});
