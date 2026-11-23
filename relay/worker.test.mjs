// cd relay && node --test   (hermetic: fetch is mocked, the key is generated here, nothing leaves the machine)
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import worker, { norm } from "./worker.mjs";

const pair = await crypto.subtle.generateKey(
  { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
const pkcs8 = Buffer.from(await crypto.subtle.exportKey("pkcs8", pair.privateKey)).toString("base64");
const PEM = `-----BEGIN PRIVATE KEY-----\n${pkcs8.match(/.{1,64}/g).join("\n")}\n-----END PRIVATE KEY-----\n`;
const ID = "3f2b8c1e-5a4d-4e6f-9a8b-7c6d5e4f3a2b";

let calls, issues, kv, env;
beforeEach(() => {
  calls = [];
  issues = [{ number: 7, title: "Fetch: page text garbled on pages without a charset", html_url: "https://example.invalid/i/7" }];
  kv = new Map();
  env = { APP_ID: "12345", INSTALLATION_ID: "678", TARGET_REPO: "example-maintainer/triage", APP_PRIVATE_KEY: PEM,
    RATE: { get: async (k) => kv.get(k) ?? null, put: async (k, v) => void kv.set(k, v) } };
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, init });
    const path = new URL(url).pathname;
    const json = (o, status = 200) => new Response(JSON.stringify(o), { status });
    if (path === "/app/installations/678/access_tokens") return json({ token: "inst-token" }, 201);
    if (path === "/repos/example-maintainer/triage/issues" && init.method !== "POST") return json(issues);
    if (path === "/repos/example-maintainer/triage/issues") return json({ html_url: "https://example.invalid/i/8" }, 201);
    if (path === "/repos/example-maintainer/triage/issues/7/comments") return json({ html_url: "https://example.invalid/i/7#c" }, 201);
    return json({ message: "nope" }, 404);
  };
});

const post = (obj, path = "/v1/feedback", method = "POST", ip = "192.0.2.1") =>
  worker.fetch(new Request("https://relay.example.invalid" + path, { method, headers: { "cf-connecting-ip": ip },
    body: method === "POST" ? (typeof obj === "string" ? obj : JSON.stringify(obj)) : undefined }), env);
let n = 0;
const fresh = () => good({ body: `Steps on examples/instance, case ${++n}` });
const good = (o = {}) => ({ instance: ID, engine: "v0.3.0", title: "Sync: a new problem in the example", body: "Steps on examples/instance ...", ...o });

test("routes and methods", async () => {
  assert.equal((await post(good(), "/v1/other")).status, 404);
  assert.equal((await post(null, "/v1/feedback", "GET")).status, 405);
  assert.equal(calls.length, 0);
});

test("rejects invalid input without calling GitHub", async () => {
  for (const bad of ["not json", "[]", good({ instance: "abc" }), good({ instance: ID.toUpperCase() }), good({ title: "x".repeat(121) }),
    good({ title: "two\nlines" }), good({ title: " " }), good({ body: "x".repeat(4001) }), good({ engine: "v1 && rm" }), good({ engine: "anything" }),
    good({ extra: 1 }), good({ body: 5 }), "x".repeat(6001)]) {
    const r = await post(bad);
    assert.ok([400, 413].includes(r.status), JSON.stringify(bad).slice(0, 60));
  }
  assert.equal(calls.length, 0);
  assert.equal(kv.size, 0);
});

test("creates a labelled issue with a fenced body, authenticated as the app", async () => {
  const r = await post(good({ body: "uses ``` and @someone and ![x](https://example.invalid/p.png)" }));
  assert.equal(r.status, 201);
  assert.deepEqual(await r.json(), { url: "https://example.invalid/i/8", action: "created" });
  const [tok, list, create] = calls;
  const jwt = tok.init.headers.authorization.replace("Bearer ", "").split(".");
  const payload = JSON.parse(Buffer.from(jwt[1], "base64url"));
  assert.equal(payload.iss, "12345");
  assert.ok(payload.exp - payload.iat <= 600);
  assert.ok(await crypto.subtle.verify("RSASSA-PKCS1-v1_5", pair.publicKey, Buffer.from(jwt[2], "base64url"),
    new TextEncoder().encode(`${jwt[0]}.${jwt[1]}`)), "JWT signature verifies with the app's public key");
  assert.equal(list.init.headers.authorization, "Bearer inst-token");
  assert.match(list.url, /state=open&labels=from-runs/);
  const sent = JSON.parse(create.init.body);
  assert.deepEqual(sent.labels, ["from-runs"]);
  assert.ok(sent.body.startsWith("````text\n") && sent.body.includes("\n````\n"), "body fenced longer than any backtick run");
  assert.match(sent.body, /engine v0\.3\.0 · instance 3f2b8c1e$/);
});

test("comments on an open issue with the same normalised title", async () => {
  const r = await post(good({ title: "fetch — Page text GARBLED on pages without a charset!" }));
  assert.deepEqual(await r.json(), { url: "https://example.invalid/i/7", action: "commented" });
  assert.match(calls.at(-1).url, /issues\/7\/comments$/);
  assert.equal(norm("A–b: C!"), "a b c");
});

test("accepts the engine versions the client sends", async () => {
  for (const engine of ["v0.3.0", "v0.3.0-rc1", "v0.2.1-4-g67a0de0", "67a0de0", "unknown"])
    assert.equal((await post({ ...fresh(), engine, instance: crypto.randomUUID() })).status, 201, engine);
});

test("the same text twice: the earlier issue, no second GitHub call", async () => {
  assert.equal((await post(good())).status, 201);
  const n = calls.length;
  const r = await post(good({ title: "SYNC: a new problem in the example!" }));
  assert.deepEqual(await r.json(), { url: "https://example.invalid/i/8", action: "duplicate" });
  assert.equal(calls.length, n);
});

test("rate limits: 3 a day per instance, per IP, a global daily cap", async () => {
  for (let i = 0; i < 3; i++) assert.equal((await post(fresh())).status, 201);
  const n = calls.length;
  const r = await post(fresh());
  assert.equal(r.status, 429);
  assert.deepEqual(await r.json(), { error: "rate limited" });
  assert.equal(calls.length, n);
  const other = () => ({ ...fresh(), instance: crypto.randomUUID() });   // a new id per request: the IP cap holds
  env.IP_DAILY_CAP = "5";
  assert.equal((await post(other())).status, 201);
  assert.equal((await post(other())).status, 201);
  assert.equal((await post(other())).status, 429);
  assert.equal((await post(other(), undefined, undefined, "192.0.2.9")).status, 201);
  env.GLOBAL_DAILY_CAP = "6";
  assert.equal((await post(other(), undefined, undefined, "192.0.2.10")).status, 429);
});

test("a GitHub failure is a 502, an unconfigured relay a 500", async () => {
  env.INSTALLATION_ID = "999";
  const r = await post(good());
  assert.equal(r.status, 502);
  assert.deepEqual(await r.json(), { error: "upstream error" });
  env.TARGET_REPO = "";
  assert.equal((await post(good())).status, 500);
});
