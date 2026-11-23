# Feedback relay

A Cloudflare Worker that lets a paper's dreaming run report an engine problem without a GitHub login
(`engine_feedback: relay`, see `../FEEDBACK.md`). The run sends generic, leak-checked text to
`POST /v1/feedback` `{instance, engine, title, body}`; the relay checks sizes and daily limits (3 per
instance, `IP_DAILY_CAP` per address, `GLOBAL_DAILY_CAP` in all), answers a text it already filed with that
issue (`duplicate`), else comments on an open `from-runs` issue with the same title or opens one, on a
repository only the maintainer can see. It answers `{url, action}`.

- The limits are approximate: KV is eventually consistent, so a burst can pass a cap by a few.
- Cloud runs may share an address: keep `IP_DAILY_CAP` well above 3.
- The Worker logs nothing itself. Cloudflare sees the caller's IP; KV holds per-day counters (per instance id,
  per IP, two days) and a hash of each filed text with its issue URL (30 days), never the text.
- The only secret is the GitHub App's private key, a Worker secret. Nothing secret is in this repository.

Tests (no network): `cd relay && node --test`.

## One-time setup (the maintainer)
1. Create a **private** triage repository and a label `from-runs` in it.
2. Create a GitHub App (Settings → Developer settings → GitHub Apps): no webhook, repository permission
   **Issues: read and write**, nothing else. Note its App ID; generate a private key (a `.pem` download).
3. Install the app on the triage repository only. The installation id is the number at the end of the
   installation's settings URL.
4. Convert the key to PKCS#8 (WebCrypto reads only that):
   `openssl pkcs8 -topk8 -nocrypt -in app.pem -out app-pkcs8.pem`.
5. With a Cloudflare account and `npx wrangler login`: `npx wrangler kv namespace create RATE`, then fill
   `wrangler.toml` (App ID, installation id, `owner/repo`, the KV id).
6. `npx wrangler secret put APP_PRIVATE_KEY` and paste the PKCS#8 key yourself; delete both `.pem` files after.
7. `npx wrangler deploy`. Put the printed URL plus `/v1/feedback` into the `feedback_relay` default in
   `tools/paths.py` and release it.
