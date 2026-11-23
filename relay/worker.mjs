// The Morning Fold feedback relay: a Cloudflare Worker that turns a paper's engine problem into an issue on the
// maintainer's triage repository, so a reader's run needs no GitHub login. Public code; the only secret is the
// GitHub App's private key, held as the Worker secret APP_PRIVATE_KEY. See README.md.
//
// POST /v1/feedback {instance, engine, title, body} -> {url, action: "created" | "commented" | "duplicate"}
// env: APP_ID, INSTALLATION_ID, TARGET_REPO ("owner/repo"), APP_PRIVATE_KEY (PKCS#8 PEM, secret),
//      GLOBAL_DAILY_CAP (default 30), IP_DAILY_CAP (default 10), RATE (KV namespace binding).
// Limits are per UTC day and approximate (KV is eventually consistent). Nothing is logged here; bodies are never stored.

const PER_INSTANCE = 3;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const GH = "https://api.github.com";

const reply = (status, obj) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });

export function validate(d) {
  if (!d || typeof d !== "object" || Array.isArray(d)) return "expected a JSON object";
  const extra = Object.keys(d).filter((k) => !["instance", "engine", "title", "body"].includes(k));
  if (extra.length) return `unknown field ${extra[0]}`;
  if (typeof d.instance !== "string" || !UUID.test(d.instance)) return "instance must be a uuid4";
  if (typeof d.engine !== "string" || !/^(v\d+\.\d+\.\d+(-rc\d+)?(-\d+-g[0-9a-f]{7,40})?|[0-9a-f]{7,40}|unknown)$/.test(d.engine))
    return "engine must be a release tag, a describe of one, or a commit";
  if (typeof d.title !== "string" || !d.title.trim() || d.title.length > 120 || /[\u0000-\u001f]/.test(d.title))
    return "title must be one line of 1-120 characters";
  if (typeof d.body !== "string" || !d.body.trim() || d.body.length > 4000) return "body must be 1-4000 characters";
  return null;
}

export const norm = (t) => t.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();

const b64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");

export async function appJwt(appId, pem, now = Math.floor(Date.now() / 1000)) {
  const der = Uint8Array.from(atob(pem.replace(/-----[^-]+-----|\s/g, "")), (c) => c.charCodeAt(0));
  const key = await crypto.subtle.importKey("pkcs8", der, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"]);
  const enc = (o) => b64url(new TextEncoder().encode(JSON.stringify(o)));
  const data = `${enc({ alg: "RS256", typ: "JWT" })}.${enc({ iat: now - 60, exp: now + 540, iss: String(appId) })}`;
  return `${data}.${b64url(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(data)))}`;
}

async function gh(path, token, init = {}) {
  const r = await fetch(GH + path, {
    ...init,
    headers: { authorization: `Bearer ${token}`, accept: "application/vnd.github+json", "user-agent": "morning-fold-relay",
      "x-github-api-version": "2022-11-28", ...(init.body ? { "content-type": "application/json" } : {}) },
  });
  if (!r.ok) throw new Error(`GitHub ${r.status} on ${path.split("?")[0]}`);
  return r.json();
}

// The text goes in a code block: no mentions, links, images or markup from a public endpoint reach the issue.
const fenced = (d) => {
  const fence = "`".repeat(Math.max(3, ...[...d.body.matchAll(/`+/g)].map((m) => m[0].length + 1)));
  return `${fence}text\n${d.body}\n${fence}\n\nengine ${d.engine} · instance ${d.instance.slice(0, 8)}`;
};

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/v1/feedback") return reply(404, { error: "not found" });
    if (request.method !== "POST") return reply(405, { error: "POST only" });
    const raw = await request.text();
    if (raw.length > 6000) return reply(413, { error: "too large" });
    let d;
    try { d = JSON.parse(raw); } catch { return reply(400, { error: "invalid JSON" }); }
    const bad = validate(d);
    if (bad) return reply(400, { error: bad });
    if (!/^[\w.-]+\/[\w.-]+$/.test(env.TARGET_REPO || "")) return reply(500, { error: "relay not configured" });

    // the same text again: answer with the earlier issue, nothing new is filed
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`${norm(d.title)}\n${d.body.trim()}`)));
    const seen = await env.RATE.get(`sent:${b64url(digest)}`);
    if (seen) return reply(200, { url: seen, action: "duplicate" });

    const day = new Date().toISOString().slice(0, 10);
    const keys = [`${day}:${d.instance}`, `${day}:ip:${request.headers.get("cf-connecting-ip") || "none"}`, `${day}:all`];
    const caps = [PER_INSTANCE, Number(env.IP_DAILY_CAP) || 10, Number(env.GLOBAL_DAILY_CAP) || 30];
    const counts = await Promise.all(keys.map(async (k) => Number(await env.RATE.get(k)) || 0));
    if (counts.some((c, i) => c >= caps[i])) return reply(429, { error: "rate limited" });
    await Promise.all(keys.map((k, i) => env.RATE.put(k, String(counts[i] + 1), { expirationTtl: 172800 })));
    const remember = (url) => env.RATE.put(`sent:${b64url(digest)}`, url, { expirationTtl: 2592000 });

    try {
      const jwt = await appJwt(env.APP_ID, env.APP_PRIVATE_KEY);
      const { token } = await gh(`/app/installations/${env.INSTALLATION_ID}/access_tokens`, jwt, { method: "POST" });
      const repo = `/repos/${env.TARGET_REPO}`;
      const open = await gh(`${repo}/issues?state=open&labels=from-runs&per_page=100`, token);
      const same = open.find((i) => !i.pull_request && norm(i.title) === norm(d.title));
      if (same) {
        await gh(`${repo}/issues/${same.number}/comments`, token, { method: "POST", body: JSON.stringify({ body: fenced(d) }) });
        await remember(same.html_url);
        return reply(200, { url: same.html_url, action: "commented" });
      }
      const made = await gh(`${repo}/issues`, token, {
        method: "POST", body: JSON.stringify({ title: d.title.trim(), body: fenced(d), labels: ["from-runs"] }) });
      await remember(made.html_url);
      return reply(201, { url: made.html_url, action: "created" });
    } catch {
      return reply(502, { error: "upstream error" });   // never echo the triage repository's path
    }
  },
};
