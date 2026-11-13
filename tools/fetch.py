#!/usr/bin/env python3
"""Read the source registry straight from the web, without the WebFetch tool (so no approvals).

Needs network access in the task's cloud environment ("Full", or "Custom" with these domains).

  python3 tools/fetch.py sources [--due] [--status active,seed,candidate] [--top 30] [--no-baseline]
      Fetches every matching Source in parallel (RSS/Atom feed when there is one, else the listing page),
      drops items already in the graph, folds near-copies of the same story across sites into one item,
      scores every item for relevance to this reader (embeddings against the beats, open theses and known
      entities, plus a boost when a known entity is named) and prints the top items plus per-source status.
      The full ranked list is in <work>/items.json. Dated items more than a day in the future (events, deadlines) are
      not news: they are listed under `upcoming`, not ranked. --no-baseline: a source read for the first time
      keeps its undated links as today's news instead of setting them aside as backlog (first issue only).

  python3 tools/fetch.py article <url> [<url> ...]
      Main text, title, date and author of each article (HTML or PDF). The text is cached for this run and
      archived gzipped in archive/, so graph.py can check fact quotes against it (grounding), also later.
      Output shows the first 12,000 characters; `text_file` holds the full text (PDF pages led by [page N]).
      `figures` lists charts saved as images under <work>/figures: PDF pages with charts (png, page,
      heading) and an article's chart images (image, caption). Read them to get numbers the text lacks.

  python3 tools/fetch.py events <url> [<url> ...]
      Dated events published as structured data on a page (schema.org Event JSON-LD) or as an .ics calendar:
      name, start and end date, location, organiser, url. Pages without structured events return none; then read
      the page (or PDF) with `article` and let the model pick the dates.

  python3 tools/fetch.py transcribe <episode url> [<episode url> ...] [--budget-minutes N]
      Text of podcast episodes listed by `sources` (kind podcast): the publisher's transcript from the feed
      when there is one, else a transcript on the episode page, else the audio is downloaded and transcribed
      locally with Whisper (large-v3-turbo; about 13 min per hour of audio on 4 CPUs). --budget-minutes caps
      the audio transcribed locally (default: reader.md podcast_budget_weekday); episodes over budget are reported and left for a later run. The text is
      cached and archived like an article (paragraphs start with [mm:ss] and the speaker label when known);
      the full text is in the returned text_file. Progress is appended to <work>/episodes.jsonl.

  python3 tools/fetch.py check <url>
      Reachability test (status, final URL, feed, whether the browser was needed). Use it on every new source.

  python3 tools/fetch.py rank <items.json> [--top 30]
      Re-ranks a saved items list (e.g. <work>/items.json from `sources`) against the current reader profile,
      graph and theses, without fetching anything. Useful after editing reader.md or sources.json queries.

  python3 tools/fetch.py podcast-find "<show name or topic>" [--limit 8]
      Looks a podcast up in the public iTunes directory: show name, publisher and feed URL, for sources.json.

Who the reader is comes from the instance (tools/paths.py): reader.md (profile, watch list, locale, ranking
knobs) and sources.json ("queries": extra ranking texts per beat).

The work dir (fetched texts for the quote check, items.json, episodes, figures) is $MORNING_FOLD_WORK, else
<instance>/.work (it ignores itself in git); `--out <dir>` on any command sets it for that call. Set the
environment variable when a whole run should use another folder: graph.py's quote check reads the same place.

Pages that refuse plain HTTP (403/406/429, a bot-challenge page, or almost no text) are retried in a real
browser (Playwright Chromium) when it is installed. All output is JSON; a failure never stops the batch.
"""
import sys, os, json, re, datetime, hashlib, collections, concurrent.futures as cf
from urllib.parse import urljoin, urlparse

if __name__ == "__main__" and "--out" in sys.argv[:-1]:     # --out <dir> on any command: the work dir for this call
    os.environ["MORNING_FOLD_WORK"] = os.path.abspath(sys.argv[sys.argv.index("--out") + 1])
    del sys.argv[sys.argv.index("--out"):sys.argv.index("--out") + 2]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph  # noqa: E402  (same folder)
import paths  # noqa: E402

CHROME = "130"
HEADERS = {
    "sec-ch-ua": f'"Chromium";v="{CHROME}", "Google Chrome";v="{CHROME}", "Not?A_Brand";v="99"',
    "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"macOS"', "upgrade-insecure-requests": "1",
    "user-agent": f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{CHROME}.0.0.0 Safari/537.36",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": paths.accept_language(),
}
TIMEOUT = 25
CHALLENGE = re.compile(r"just a moment|client challenge|nur einen moment|attention required|access denied|zugriff verweigert|captcha|security check", re.I)
MIN_TEXT = 300


def out(o):
    print(json.dumps(o, ensure_ascii=False, indent=1))


class Resp:
    def __init__(self, url, status, text, content=None, browser=False):
        self.url, self.status_code, self.text, self.browser = url, status, text, browser
        self.content = content if content is not None else text.encode("utf-8", "ignore")
        self.ok = status < 400


def visible_len(html):
    t = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    return len(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).strip())


def blocked(r):
    title = (re.search(r"(?is)<title[^>]*>(.*?)</title>", r.text or "") or [None, ""])[1]
    ctype = r.headers.get("content-type", "") if hasattr(r, "headers") else ""
    is_html = "html" in ctype or (r.text or "").lstrip()[:200].lower().startswith(("<!doctype", "<html"))
    return r.status_code in (401, 403, 406, 429, 503) or bool(CHALLENGE.search(title)) or (is_html and visible_len(r.text) < MIN_TEXT)


_browser = None


_browser_thread = None


def render(url):
    """Opens url in headless Chromium and returns the rendered page; None when Playwright is not installed.
    Playwright's sync objects belong to the thread that created them, so every call runs on one dedicated thread
    (a lock alone fails with greenlet "Cannot switch to a different thread" once a second worker calls it)."""
    global _browser_thread
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return None
    if _browser_thread is None:
        _browser_thread = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser")
    return _browser_thread.submit(_render, url).result()


def _render(url):
    global _browser
    from playwright.sync_api import sync_playwright
    if _browser is None:
        pw = sync_playwright().start()
        _browser = pw.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
    ctx = _browser.new_context(user_agent=HEADERS["user-agent"], locale=paths.cfg("locale"),
                               extra_http_headers={"accept-language": HEADERS["accept-language"]})
    page = ctx.new_page()
    try:
        try:
            resp = page.goto(url, timeout=30000, wait_until="domcontentloaded")
        except Exception as e:           # e.g. a TLS-intercepting proxy the browser doesn't trust: plain HTTP result stands
            render.last_error = str(e).splitlines()[0][:200]
            return None
        for _ in range(15):              # let a bot challenge clear
            if not CHALLENGE.search(page.title() or ""):
                break
            page.wait_for_timeout(1000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        return Resp(page.url, resp.status if resp else 0, page.content(), browser=True)
    finally:
        ctx.close()


def get(url, allow_browser=True):
    import requests
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    if allow_browser and blocked(r):
        b = render(url)
        if b is not None and b.status_code < 400 and not CHALLENGE.search(b.text[:3000]):
            return b
    res = Resp(r.url, r.status_code, r.text, r.content)
    res.headers = r.headers
    return res


def find_feed(html, base):
    for m in re.finditer(r"<link[^>]+>", html, re.I):
        tag = m.group(0)
        if re.search(r"application/(rss|atom)\+xml", tag, re.I):
            h = re.search(r'href=["\']([^"\']+)', tag, re.I)
            if h:
                return urljoin(base, h.group(1))
    return None


def feed_items(url):
    import feedparser
    r = get(url, allow_browser=False)
    f = feedparser.parse(r.content)
    items = []
    for e in f.entries[:60]:
        d = e.get("published_parsed") or e.get("updated_parsed")
        items.append({"title": (e.get("title") or "").strip(), "url": urljoin(r.url or url, e.get("link")) if e.get("link") else None,
                      "published": datetime.datetime(*d[:6]).strftime("%Y-%m-%d") if d else None,
                      "summary": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", e.get("summary") or ""))[:400].strip()})
    return items


def seconds(d):
    """itunes:duration as seconds ('3263', '54:21', '01:08:19')."""
    if not d:
        return None
    try:
        parts = [int(float(x)) for x in str(d).split(":")]
    except ValueError:
        return None
    sec = 0
    for x in parts:
        sec = sec * 60 + x
    return sec


def podcast_items(src, since=None, cap=10):
    """Newest episodes of a podcast feed with show notes, audio URL, duration and publisher transcripts:
    the last three weeks (at most `cap`), or everything published on or after `since` (cap=None: no limit)."""
    import feedparser
    p = src.get("props", {})
    r = get(p["feed"], allow_browser=False)
    raw = r.text
    tags = {}
    for item in re.findall(r"<item\b.*?</item>", raw, flags=re.S):
        g = re.search(r"<guid[^>]*>(.*?)</guid>", item, re.S)
        tr = [dict(re.findall(r'(\w+)="([^"]*)"', t)) for t in re.findall(r"<podcast:transcript\b([^>]*)/?>", item)]
        if g and tr:
            tags[g.group(1).strip()] = tr
    f = feedparser.parse(r.content)
    items = []
    for e in f.entries:
        d = e.get("published_parsed") or e.get("updated_parsed")
        enc = next((l.get("href") for l in e.get("links", []) if l.get("rel") == "enclosure"), None)
        notes = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", (e.get("content") or [{}])[0].get("value") or e.get("summary") or "")).strip()
        items.append({"title": (e.get("title") or "").strip(), "url": e.get("link") or enc,
                      "published": datetime.datetime(*d[:6]).strftime("%Y-%m-%d") if d else None,
                      "summary": notes[:400], "show_notes": notes[:3000], "kind": "podcast",
                      "show": src.get("name"), "group": p.get("group"), "lang": p.get("lang"),
                      "audio": enc, "link": e.get("link"), "duration_s": seconds(e.get("itunes_duration")),
                      "transcripts": tags.get((e.get("id") or "").strip(), [])})
    links = collections.Counter(i["url"] for i in items)
    for i in items:                       # an episode's identity: its own page, else its audio (some feeds give every
        if not i["url"] or links[i["url"]] > 1:     # episode the show's homepage as link)
            i["url"] = i["audio"] or i["url"]
    items.sort(key=lambda i: i["published"] or "", reverse=True)
    since = since or (datetime.date.today() - datetime.timedelta(days=21)).isoformat()
    recent = [i for i in items if i["published"] and i["published"] >= since]
    return recent[:cap] if cap else recent


def page_links(html, base):
    """Headline-like links from a listing page: same site, reasonable anchor text, article-ish path."""
    host = urlparse(base).netloc.removeprefix("www.")
    seen, items = set(), []
    for m in re.finditer(r'<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        href = urljoin(base, m.group(1))
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(2))).strip()
        p = urlparse(href)
        if p.netloc.removeprefix("www.") != host or href in seen or len(text) < 25 or len(text) > 220:
            continue
        if p.path.count("/") < 2 and not re.search(r"\d", p.path):
            continue
        seen.add(href)
        items.append({"title": text, "url": href})
    return items[:80]


def due(props, today):
    last = props.get("last_read")
    if not last:
        return True
    try:
        age = (today - datetime.date.fromisoformat(last[:10])).days
    except ValueError:
        return True
    cad = (props.get("cadence") or "daily").lower()
    return age >= {"daily": 1, "weekly": 6, "monthly": 28}.get(cad, 1)


def explain(e):
    s = str(e)
    if "Tunnel connection failed: 403" in s or "host_not_allowed" in s:
        return "blocked by this environment's network policy (needs Full network access); use WebSearch"
    return f"{type(e).__name__}: {s[:200]}"


def fetch_source(src, known):
    p = src.get("props", {})
    url = p.get("url") or p.get("feed")
    res = {"id": src["id"], "name": src.get("name"), "url": url, "ok": False}
    if not url:
        res["error"] = "no url"; return res
    if p.get("kind") == "podcast":
        try:
            items = podcast_items(src)
            for i in items:
                i["source"] = src["id"]
            # an episode is known under any of its names: identity, page link or audio URL (older Articles may use another)
            new = [i for i in items if i.get("url") and not ({i["url"], i.get("link"), i.get("audio")} - {None}) & known]
            res.update(ok=True, mode="podcast", items_total=len(items), new_items=new)
        except Exception as e:
            res["error"] = explain(e)
        return res
    if p.get("feed"):                      # a known feed first: some sites block their pages but not their feed
        try:
            items = feed_items(p["feed"])
            if items:
                for i in items:
                    i["source"] = src["id"]
                new = [i for i in items if i.get("url") and i["url"] not in known]
                res.update(ok=True, mode="feed", feed=p["feed"], items_total=len(items), new_items=new)
                return res
        except Exception as e:
            res["feed_error"] = str(e)[:200]
    try:
        r = get(url)
        res.update(status=r.status_code, final_url=r.url, browser=r.browser)
        if r.status_code >= 400:
            res["error"] = f"HTTP {r.status_code}"; return res
        feed = p.get("feed") or find_feed(r.text, r.url)
        items = []
        if feed:
            try:
                items = feed_items(feed); res["feed"] = feed
            except Exception as e:
                res["feed_error"] = str(e)[:200]
        res["mode"] = "feed" if items else "page"
        if not items:
            items = page_links(r.text, r.url)
        for i in items:
            i["source"] = src["id"]
        new = [i for i in items if i.get("url") and i["url"] not in known]
        res.update(ok=True, items_total=len(items), new_items=new)
    except Exception as e:
        res["error"] = explain(e)
    return res


def profile(nodes, edges):
    """Query texts that describe what this reader cares about, and the entity names worth a boost.
    From the instance: reader.md's profile paragraph (else the graph's Reader node), sources.json "queries"
    (one short text per beat), the open theses; names: well-connected entities plus reader.md `watch`."""
    reader = next((n for n in nodes.values() if n["type"] == "Reader"), {})
    qs = [paths.profile_text() or reader.get("text") or ""]
    qs += list(paths.sources_file()["queries"])
    qs += [f"{t['name']}: {t.get('text', '')}" for t in nodes.values()
           if t["type"] == "Thesis" and t.get("props", {}).get("status", "open") == "open"]
    deg = {}
    for e in edges.values():
        deg[e["src"]] = deg.get(e["src"], 0) + 1; deg[e["dst"]] = deg.get(e["dst"], 0) + 1
    names = set()
    for n in nodes.values():
        if n["type"] in graph.ACTOR_TYPES and deg.get(n["id"], 0) >= 2:
            for nm in [n.get("name", "")] + n.get("aliases", []):
                if len(nm) >= 4:
                    names.add(nm.lower())
    names |= {w.lower() for w in paths.cfg("watch") or [] if len(w) >= 3}
    return [q for q in qs if q.strip()], names


def rank(items, nodes, edges):
    import numpy as np
    if not items:
        return items
    qs, names = profile(nodes, edges)
    grace, decay, boost = int(paths.cfg("recency_grace_days")), float(paths.cfg("recency_decay")), float(paths.cfg("name_boost"))
    word = lambda w: re.compile(r"(?<!\w)" + re.escape(w.lower()) + r"(?!\w)")
    regions = [(w, word(w)) for w in paths.cfg("regions") or [] if w]
    elsewhere = [(w, word(w)) for w in paths.cfg("other_regions") or [] if w]
    rboost = float(paths.cfg("region_boost"))
    Q = graph.embed(qs, kind="query")
    texts = [f"{i.get('title', '')}. {i.get('summary', '')}" for i in items]
    V = graph.embed(texts)
    sim = (V @ Q.T).max(axis=1)
    for i, s, t in zip(items, sim, texts):
        tl = t.lower()
        hits = sorted({nm for nm in names if re.search(r"(?<!\w)" + re.escape(nm) + r"(?!\w)", tl)})
        i["named"] = hits[:5]
        # a daily paper: relevance scores are flat (~0.75-0.93), so age decides between near-equals;
        # by default an article loses 0.01 per day after its second day (an unread three-week-old item drops ~0.19)
        age = 0
        if i.get("published") and i.get("kind") != "podcast":
            try:
                age = max(0, (datetime.date.today() - datetime.date.fromisoformat(i["published"][:10])).days - grace)
            except ValueError:
                pass
        reg = 0.0
        if regions or elsewhere:      # reader.md regions / other_regions: a nudge, not a filter
            mine = [w for w, rx in regions if rx.search(tl)]
            other = [w for w, rx in elsewhere if rx.search(tl)]
            if mine:
                reg, i["region"] = rboost, mine[:3]
            elif other:
                reg, i["region"] = -rboost, ["elsewhere: " + ", ".join(other[:3])]
        i["score"] = round(float(s) + (boost if hits else 0.0) + reg - decay * age, 3)
    # fold near-copies of one story across different sites (same press release reprinted)
    order = sorted(range(len(items)), key=lambda k: -items[k]["score"])
    TT = V @ V.T
    leader = {}
    for k in order:
        host = urlparse(items[k]["url"]).netloc
        for j in list(leader):
            if TT[k, j] > 0.95 and urlparse(items[j]["url"]).netloc != host:
                items[j].setdefault("copies", []).append({"url": items[k]["url"], "source": items[k]["source"]})
                break
        else:
            leader[k] = True
    return [items[k] for k in order if k in leader]


FIRST_SEEN = os.path.join(paths.HOME, "data", "first_seen.json")


def load_first_seen():
    try:
        return json.load(open(FIRST_SEEN, encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save_first_seen(fs, today):
    # links gone from their page for 60 days are forgotten; if one reappears it counts as new
    cut = str(today - datetime.timedelta(days=60))
    fs = {u: v for u, v in fs.items() if v[1] >= cut}
    os.makedirs(os.path.dirname(FIRST_SEEN), exist_ok=True)
    tmp = FIRST_SEEN + f".{os.getpid()}.tmp"
    json.dump(fs, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, FIRST_SEEN)


def cmd_sources(args):
    statuses, outdir, top = {"active", "seed", "candidate"}, paths.work_dir(), 30
    only_due = "--due" in args
    no_baseline = "--no-baseline" in args
    if "--status" in args:
        statuses = set(args[args.index("--status") + 1].split(","))
    if "--out" in args:
        outdir = args[args.index("--out") + 1]
    if "--top" in args:
        top = int(args[args.index("--top") + 1])
    os.makedirs(outdir, exist_ok=True)
    nodes, edges = graph.load()
    known = {n.get("props", {}).get("url") for n in nodes.values() if n["type"] == "Article"}
    today = datetime.date.today()
    srcs = [n for n in nodes.values() if n["type"] == "Source"
            and n.get("props", {}).get("status", "seed") in statuses
            and (n.get("props", {}).get("url") or n.get("props", {}).get("feed"))   # no address: cited, never read
            and (not only_due or due(n.get("props", {}), today))]
    results, items, seen, upcoming = [], [], set(), []
    max_age = int(paths.cfg("max_item_age_days"))
    declared = {s.get("id") or "source:" + graph.slug(s.get("name", "")) for s in paths.sources_file()["sources"]}
    unseeded = sorted(declared - set(nodes))
    # Undated links (listing pages without a feed) carry no date, so a link is news only on the day it first
    # appears on its page; ever after it is backlog. data/first_seen.json remembers {url: [first_seen, last_seen]}.
    # A source read for the first time sets its baseline: everything on its page then is backlog.
    fs = load_first_seen()
    tracked = {v[2] for v in fs.values()}
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        for res in ex.map(lambda s: fetch_source(s, known), srcs):
            results.append(res)
            baseline = res["id"] not in tracked and not no_baseline
            dropped = 0
            for i in res.get("new_items", []):
                # a daily paper: an unread item older than three weeks is history (the backfill's job), not news;
                # without this, a newsroom's whole backlog competes with today's items in the ranking
                if i.get("kind") != "podcast" and i.get("published") and i["published"] < str(today - datetime.timedelta(days=max_age)):
                    continue
                if i.get("kind") != "podcast" and not i.get("published"):
                    first = fs.get(i["url"], [None])[0]
                    if first is None:
                        first = "baseline" if baseline else str(today)
                    fs[i["url"]] = [first, str(today), res["id"]]
                    if first == "baseline" or first < str(today - datetime.timedelta(days=1)):
                        dropped += 1; continue      # seen on an earlier day: backlog, not news
                    i["published"], i["date_note"] = None, f"undated; first seen on its page {first}"
                if i["url"] in seen:
                    continue
                seen.add(i["url"])
                if i.get("kind") != "podcast" and (i.get("published") or "") > str(today + datetime.timedelta(days=1)):
                    upcoming.append(i)      # dated ahead: an event or deadline, not news (week-ahead material)
                else:
                    items.append(i)
            if dropped:
                res["undated_backlog_dropped"] = dropped
            if baseline and dropped:
                res["baseline"] = True
    save_first_seen(fs, today)
    ranked = rank(items, nodes, edges)
    json.dump(ranked, open(os.path.join(outdir, "items.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    status = [{"id": r["id"], "ok": r["ok"], "mode": r.get("mode"), "browser": r.get("browser"),
               "new": len(r.get("new_items", [])), "error": r.get("error"),
               "undated_backlog_dropped": r.get("undated_backlog_dropped"), "baseline": r.get("baseline")} for r in results]
    status.sort(key=lambda s: (not s["ok"], -s["new"]))
    out({"sources_read": len(results), "ok": sum(s["ok"] for s in status),
         "failed": [s for s in status if not s["ok"]],
         "new_items": len(items), "after_folding_copies": len(ranked),
         "upcoming": [{k: i.get(k) for k in ("published", "title", "url", "source")} for i in sorted(upcoming, key=lambda i: i["published"])][:40],
         "undated_backlog_dropped": sum(r.get("undated_backlog_dropped") or 0 for r in results),
         "top": [{k: i[k] for k in ("score", "title", "url", "source", "published", "named", "copies") if i.get(k)} for i in ranked[:top]],
         "podcasts": [{k: i[k] for k in ("score", "show", "group", "title", "url", "published", "duration_s", "named") if i.get(k)}
                      | {"publisher_transcript": bool(i.get("transcripts"))} for i in ranked if i.get("kind") == "podcast"],
         "full_list": os.path.join(outdir, "items.json"),
         **({"unseeded_sources": unseeded, "hint": "run graph.py seed to add them"} if unseeded else {}),
         "per_source": [s for s in status if s["ok"]]})


def cmd_article(args):
    import trafilatura
    paths.work_dir()
    os.makedirs(graph.TEXT_CACHE, exist_ok=True)

    def one(u):
        if not re.match(r"https?://", u or ""):
            return {"url": u, "ok": False, "error": "not an absolute http(s) URL: take the item's url from items.json (fetch.py makes them absolute) or prefix the site's address"}
        tf = os.path.join(paths.WORK, "episodes", hashlib.sha1(u.encode()).hexdigest() + ".txt")
        if os.path.exists(tf):                # a transcribed episode: its transcript, not the episode web page
            return {"url": u, "ok": True, "transcript": True, "text_file": tf, "text": open(tf, encoding="utf-8").read()[:12000]}
        try:
            r = get(u)
            if r.status_code >= 400:
                return {"url": u, "ok": False, "error": f"HTTP {r.status_code}"}
            key = hashlib.sha1(u.encode()).hexdigest()
            if r.content[:5] == b"%PDF-" or r.url.lower().endswith(".pdf"):
                d = {"title": None, "text": pdf_text(r.content)}
                figs = pdf_figures(r.content, key)
            else:
                figs = html_figures(r.text, r.url, key)
                j = trafilatura.extract(r.text, url=r.url, output_format="json", with_metadata=True,
                                        include_comments=False, favor_recall=True)
                if not j:
                    return {"url": u, "ok": False, "error": "no main text found"}
                d = json.loads(j)
            text = d.get("text") or ""
            if not text.strip():
                return {"url": u, "ok": False, "error": "no text found"}
            store_text([u, r.url], text)    # cache under both the asked and the final URL, and archive
            return {"url": r.url, "asked": u, "ok": True, "browser": r.browser, "title": d.get("title"), "date": d.get("date"),
                    "author": d.get("author"), "sitename": d.get("sitename"), "chars": len(text),
                    "text_file": graph.text_path(u), "figures": figs, "text": text[:12000]}
        except Exception as e:
            return {"url": u, "ok": False, "error": explain(e)}

    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        out(list(ex.map(one, args)))


def store_text(keys, text):
    """Caches text for the grounding check under each key and archives it gzipped (once)."""
    import gzip
    os.makedirs(graph.TEXT_CACHE, exist_ok=True)
    for key in {k for k in keys if k}:
        open(graph.text_path(key), "w", encoding="utf-8").write(text)
        ap = graph.archive_path(key)
        if not os.path.exists(ap):
            os.makedirs(os.path.dirname(ap), exist_ok=True)
            with gzip.open(ap, "wt", encoding="utf-8") as g:
                g.write(text)


def paragraphs(segs, span=60):
    """Joins timed segments [(start_s, speaker, text)] into paragraphs of about `span` seconds, each led by
    [mm:ss] and the speaker label when it changes or is known."""
    out, cur, start, spk = [], [], None, None
    for t, sp, tx in segs:
        if cur and (t - start >= span or (sp and sp != spk)):
            out.append((start, spk, " ".join(cur))); cur = []
        if not cur:
            start, spk = t, sp
        cur.append(tx.strip())
    if cur:
        out.append((start, spk, " ".join(cur)))
    stamp = lambda s: f"[{int(s) // 3600}:{int(s) % 3600 // 60:02d}:{int(s) % 60:02d}]" if s >= 3600 else f"[{int(s) // 60:02d}:{int(s) % 60:02d}]"
    return "\n\n".join(f"{stamp(t)} {sp + ': ' if sp else ''}{tx}" for t, sp, tx in out)


def parse_timed(text):
    """Segments from a VTT or SRT transcript, with <v Name> or 'NAME:' speaker labels when present."""
    segs = []
    for m in re.finditer(r"(?:(\d+):)?(\d\d):(\d\d)[.,]\d+\s*-->[^\n]*\n(.*?)(?:\n\s*\n|\Z)", text, flags=re.S):
        t = int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        body = m.group(4).strip()
        sp = None
        v = re.match(r"<v\s+([^>]+)>", body)
        if v:
            sp = v.group(1).strip()
        else:
            v = re.match(r"([A-Z][\w .'-]{1,40}):\s", body)
            if v:
                sp = v.group(1).strip()
                body = body[v.end():]
        body = re.sub(r"<[^>]+>", "", body).replace("\n", " ").strip()
        if body:
            segs.append((t, sp, body))
    return segs


def publisher_transcript(tr):
    """Text of the best publisher transcript: timed (VTT/SRT) first, then JSON, then HTML/plain."""
    rank = lambda t: 0 if re.search(r"vtt|srt|subrip", t.get("type", "")) else 1 if "json" in t.get("type", "") else 2
    for t in sorted(tr, key=rank):
        try:
            r = get(t["url"], allow_browser=False)
            if r.status_code >= 400:
                continue
            body = r.content.decode("utf-8", "replace")
            if rank(t) == 0:
                segs = parse_timed(body)
                if segs:
                    return paragraphs(segs)
            elif rank(t) == 1:
                j = json.loads(body)
                segs = [(float(s.get("startTime", 0)), s.get("speaker"), s.get("body", "")) for s in j.get("segments", [])]
                if segs:
                    return paragraphs(segs)
            else:
                txt = re.sub(r"\s+\n", "\n", re.sub(r"<[^>]+>", "\n", body)).strip()
                if len(txt.split()) > 300:
                    return txt
        except Exception:
            continue
    return None


def page_transcript(url):
    """A full transcript printed on the episode page (a 'Transcript' heading and long main text)."""
    import trafilatura
    try:
        r = get(url)
        if r.status_code >= 400 or not re.search(r">\s*(Transcript|Transkript)\s*<", r.text, re.I):
            return None
        txt = trafilatura.extract(r.text, url=r.url, favor_recall=True) or ""
        return txt if len(txt.split()) > 1500 else None
    except Exception:
        return None


_WHISPER = None


def whisper_text(audio_url, lang, workdir):
    """Downloads the audio and transcribes it with faster-whisper; returns (text, audio seconds, seconds taken)."""
    global _WHISPER
    import time, requests
    from faster_whisper import WhisperModel
    path = os.path.join(workdir, hashlib.sha1(audio_url.encode()).hexdigest() + ".audio")
    with requests.get(audio_url, headers={"user-agent": HEADERS["user-agent"]}, timeout=60, stream=True) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    if _WHISPER is None:
        _WHISPER = WhisperModel(os.environ.get("FOLD_WHISPER_MODEL", "large-v3-turbo"), device="cpu",
                                compute_type="int8", cpu_threads=os.cpu_count() or 2)
    t0 = time.time()
    segs, info = _WHISPER.transcribe(path, language=lang or None, beam_size=1, vad_filter=True,
                                     condition_on_previous_text=False)
    segs = [(s.start, None, s.text) for s in segs]
    os.remove(path)
    return paragraphs(segs), info.duration, time.time() - t0


def cmd_transcribe(args):
    import time
    budget = float(paths.cfg("podcast_budget_weekday"))
    if "--budget-minutes" in args:
        i = args.index("--budget-minutes"); budget = float(args[i + 1]); args = args[:i] + args[i + 2:]
    outdir = paths.work_dir()
    items = {}
    try:
        for i in json.load(open(os.path.join(outdir, "items.json"), encoding="utf-8")):
            if i.get("kind") == "podcast":
                items[i["url"]] = i
    except FileNotFoundError:
        pass
    epdir = os.path.join(outdir, "episodes"); os.makedirs(epdir, exist_ok=True)
    used, results = 0.0, []
    for u in args:
        it = items.get(u)
        res = {"url": u, "ok": False}
        if not it:
            res["error"] = "not in items.json: run `fetch.py sources` first and pass an episode url from its podcasts list"
        else:
            res.update(show=it.get("show"), title=it.get("title"), published=it.get("published"))
            try:
                text, how = publisher_transcript(it.get("transcripts") or []), "publisher"
                if not text:
                    text, how = page_transcript(u) if it.get("audio") != u else None, "page"
                if not text:
                    mins = (it.get("duration_s") or 3600) / 60
                    if used + mins > budget:
                        res["error"] = f"over budget ({used:.0f} of {budget:.0f} min used; episode {mins:.0f} min): leave for a later run"
                        text = None
                    elif not it.get("audio"):
                        res["error"] = "no audio url"
                        text = None
                    else:
                        t0 = time.time()
                        text, dur, took = whisper_text(it["audio"], it.get("lang"), epdir)
                        used += dur / 60
                        how = "whisper"
                        res.update(audio_minutes=round(dur / 60, 1), transcribe_minutes=round(took / 60, 1),
                                   total_minutes=round((time.time() - t0) / 60, 1))
                if text:
                    header = f"{it.get('show')}: {it.get('title')} ({it.get('published')})\n\nShow notes: {it.get('show_notes', '')}\n\nTranscript ({how}):\n\n"
                    full = header + text
                    store_text([u, it.get("audio")], full)
                    tf = os.path.join(epdir, hashlib.sha1(u.encode()).hexdigest() + ".txt")
                    open(tf, "w", encoding="utf-8").write(full)
                    res.update(ok=True, transcript=how, words=len(text.split()), text_file=tf)
            except Exception as e:
                res["error"] = explain(e)
        results.append(res)
        with open(os.path.join(outdir, "episodes.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
    out({"episodes": results, "whisper_minutes_used": round(used, 1), "budget_minutes": budget})


FIG_DIR = os.environ.get("FOLD_FIG_DIR") or os.path.join(paths.WORK, "figures")
CHART_WORDS = re.compile(r"chart|grafik|diagramm|abbildung|infogra|statisti|figure|exhibit|abb\.", re.I)


def pdf_text(data):
    """PDF text via PyMuPDF (self-contained wheel), each page led by [page N]; pypdf as a fallback."""
    try:
        import pymupdf
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            return "\n\n".join(f"[page {i + 1}]\n" + doc[i].get_text() for i in range(min(len(doc), 120)))
    except ImportError:
        import io
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(data))
        return "\n\n".join((pg.extract_text() or "") for pg in rd.pages[:120])


def pdf_figures(data, key, cap=25):
    """Pages that carry charts (many vector drawings, or a large embedded image) rendered as PNG for the model to read."""
    import pymupdf
    os.makedirs(FIG_DIR, exist_ok=True)
    figs = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for i in range(min(len(doc), 120)):
            pg = doc[i]
            area = pg.rect.width * pg.rect.height
            big_img = any(r.width * r.height > 0.12 * area for x in pg.get_images() for r in pg.get_image_rects(x[0]))
            if len(pg.get_drawings()) < 40 and not big_img:
                continue
            path = os.path.join(FIG_DIR, f"{key[:12]}-p{i + 1}.png")
            pg.get_pixmap(dpi=144).save(path)
            head = next((l.strip() for l in pg.get_text().splitlines() if len(l.strip()) > 12), "")
            figs.append({"page": i + 1, "png": path, "heading": head[:120]})
            if len(figs) >= cap:
                break
    return figs


def html_figures(html, base, key, cap=8):
    """Chart images of an article: <figure> images and images whose alt text, caption or file name names a chart."""
    import requests
    os.makedirs(FIG_DIR, exist_ok=True)
    cands = []
    for m in re.finditer(r"<figure\b.*?</figure>|<img\b[^>]*>", html, re.I | re.S):
        blob = m.group(0)
        src = re.search(r'(?:data-src|src)=["\']([^"\']+)', blob, re.I)
        if not src or src.group(1).startswith("data:"):
            continue
        cap_m = re.search(r"<figcaption[^>]*>(.*?)</figcaption>", blob, re.I | re.S)
        alt = re.search(r'alt=["\']([^"\']*)', blob, re.I)
        caption = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", (cap_m.group(1) if cap_m else "") or (alt.group(1) if alt else ""))).strip()
        url = urljoin(base, src.group(1))
        if blob.lower().startswith("<figure") or CHART_WORDS.search(caption + " " + url):
            if url not in [c[0] for c in cands]:
                cands.append((url, caption))
    figs = []
    for n, (url, caption) in enumerate(cands[:cap * 2]):
        try:
            r = requests.get(url, headers={"user-agent": HEADERS["user-agent"]}, timeout=20)
            ct = r.headers.get("content-type", "")
            if r.status_code >= 400 or not ct.startswith("image/") or len(r.content) < 15000 or "svg" in ct:
                continue
            path = os.path.join(FIG_DIR, f"{key[:12]}-f{n + 1}.{ct.split('/')[1].split(';')[0].replace('jpeg', 'jpg')}")
            open(path, "wb").write(r.content)
            figs.append({"image": path, "caption": caption[:200], "src": url})
        except Exception:
            continue
        if len(figs) >= cap:
            break
    return figs


def jsonld_events(html, base):
    evs = []
    for m in re.finditer(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                t = o.get("@type")
                t = t if isinstance(t, list) else [t]
                if any(isinstance(x, str) and x.endswith("Event") for x in t):
                    loc = o.get("location")
                    loc = loc.get("name") if isinstance(loc, dict) else loc
                    org = o.get("organizer")
                    org = org.get("name") if isinstance(org, dict) else org
                    evs.append({"name": o.get("name"), "start": o.get("startDate"), "end": o.get("endDate"),
                                "location": loc if isinstance(loc, str) else None, "organizer": org if isinstance(org, str) else None,
                                "url": urljoin(base, o.get("url") or base)})
                stack.extend(v for v in o.values() if isinstance(v, (dict, list)))
            elif isinstance(o, list):
                stack.extend(o)
    return evs


def ics_events(text):
    evs = []
    for block in re.findall(r"(?s)BEGIN:VEVENT(.*?)END:VEVENT", text):
        un = re.sub(r"\r?\n[ \t]", "", block)
        g = lambda k: (re.search(rf"(?m)^{k}[^:]*:(.*)$", un) or [None, None])[1]
        evs.append({"name": g("SUMMARY"), "start": g("DTSTART"), "end": g("DTEND"), "location": g("LOCATION"), "url": g("URL")})
    return evs


def cmd_events(args):
    res = []
    for u in args:
        try:
            r = get(u)
            if r.status_code >= 400:
                res.append({"url": u, "ok": False, "error": f"HTTP {r.status_code}"}); continue
            evs = ics_events(r.text) if "BEGIN:VCALENDAR" in r.text[:2000] else jsonld_events(r.text, r.url)
            links = [l for l in re.findall(r'href=["\']([^"\']+\.ics)["\']', r.text, re.I)][:5]
            res.append({"url": r.url, "ok": True, "events": evs, "ics_links": [urljoin(r.url, l) for l in links]})
        except Exception as e:
            res.append({"url": u, "ok": False, "error": explain(e)})
    out(res)


def cmd_check(args):
    try:
        r = get(args[0])
        res = {"url": args[0], "status": r.status_code, "final_url": r.url, "browser": r.browser,
               "feed": find_feed(r.text, r.url) if r.ok else None}
        if r.ok and re.search(r"<(rss|feed|rdf:RDF)\b", (r.text or "")[:3000]):
            import feedparser
            f = feedparser.parse(r.content)
            if f.entries:
                dates = sorted(datetime.datetime(*d[:6]).strftime("%Y-%m-%d") for d in
                               (e.get("published_parsed") or e.get("updated_parsed") for e in f.entries) if d)
                res.update(feed=r.url, valid_feed=True, items=len(f.entries), newest=dates[-1] if dates else None,
                           verdict=f"valid feed, {len(f.entries)} items" + (f", newest {dates[-1]}" if dates else ", undated"))
        out(res)
    except Exception as e:
        out({"url": args[0], "error": explain(e)})


def cmd_rank(args):
    if not args or args[0].startswith("--"):
        return out({"error": "usage: rank <items.json> [--top 30]"})
    top = int(args[args.index("--top") + 1]) if "--top" in args else 30
    try:
        items = json.load(open(args[0], encoding="utf-8"))
    except (OSError, ValueError) as e:
        return out({"error": f"cannot read {args[0]}: {e}"})
    for i in items:
        i.pop("copies", None); i.pop("named", None); i.pop("score", None)
    nodes, edges = graph.load()
    ranked = rank(items, nodes, edges)
    qs, names = profile(nodes, edges)
    out({"items": len(items), "after_folding_copies": len(ranked), "profile_queries": len(qs), "boost_names": len(names),
         "top": [{k: i[k] for k in ("score", "title", "url", "source", "published", "named", "copies") if i.get(k)} for i in ranked[:top]]})


def cmd_podcast_find(args):
    import requests
    if not args:
        return out({"error": 'usage: podcast-find "<show name or topic>" [--limit 8]'})
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 8
    term = " ".join(a for k, a in enumerate(args) if a != "--limit" and (k == 0 or args[k - 1] != "--limit"))
    try:
        r = requests.get("https://itunes.apple.com/search", params={"term": term, "media": "podcast", "limit": limit},
                         headers={"user-agent": HEADERS["user-agent"]}, timeout=TIMEOUT)
        out([{"show": x.get("collectionName"), "publisher": x.get("artistName"), "feed": x.get("feedUrl"),
              "genre": x.get("primaryGenreName"), "episodes": x.get("trackCount"), "latest": (x.get("releaseDate") or "")[:10]}
             for x in r.json().get("results", []) if x.get("feedUrl")])
    except Exception as e:
        out({"error": explain(e)})


if __name__ == "__main__":
    cmds = {"sources": cmd_sources, "article": cmd_article, "transcribe": cmd_transcribe, "events": cmd_events,
            "check": cmd_check, "rank": cmd_rank, "podcast-find": cmd_podcast_find}
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    if c not in cmds:
        print(__doc__); sys.exit(0)
    cmds[c](sys.argv[2:])
