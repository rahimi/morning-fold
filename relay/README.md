# Feedback relay

**Readers and their instances need nothing from this folder**: their runs send to the relay the engine names
(`feedback_relay` in `tools/paths.py`), and setup only asks whether to send. This folder is for whoever runs
that relay, the maintainer, once (or someone running a fork with a relay of their own).

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
Keep everything you fill in or download **outside this repository** (it is public): the key files and your
filled `wrangler.toml` go in a private folder, e.g. `~/.config/morning-fold-relay/`.
4. Convert the key to PKCS#8 (WebCrypto reads only that), writing it outside the repository:
   `openssl pkcs8 -topk8 -nocrypt -in ~/Downloads/<app>.private-key.pem -out ~/.config/morning-fold-relay/app-pkcs8.pem`.
5. Copy `wrangler.toml` to that folder, set `main` to the absolute path of `worker.mjs` here, and fill the App ID,
   installation id and `owner/repo`. With a Cloudflare account, from that folder: `npx wrangler login`, then
   `npx wrangler kv namespace create RATE` and put the printed id into the copy.
6. From that folder: `npx wrangler secret put APP_PRIVATE_KEY -c wrangler.toml < app-pkcs8.pem`, then delete both
   `.pem` files. (Or paste the key in the dashboard: the Worker → Settings → Variables and Secrets → Secret.)
7. `npx wrangler deploy -c wrangler.toml`. Check the printed URL: a `workers.dev` address contains your account's
   subdomain, which is often your name; change it (dashboard, workers.dev subdomain) or add a custom domain before
   you publish the URL. Put it plus `/v1/feedback` into the `feedback_relay` default in `tools/paths.py` and release it.
