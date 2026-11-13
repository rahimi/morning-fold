#!/usr/bin/env python3
"""Backfill: transcribes podcast history into the archive, so the passage index and the reporters can use it.

  python3 tools/backfill.py podcasts --since YYYY-MM-DD [--group trade|heard|all] [--shows "A,B"]
                                     [--engine auto|mlx|faster] [--model NAME] [--limit N] [--no-git]
      Every episode of the podcast Sources published on or after --since (trade group first, newest first)
      gets a text: the publisher's transcript, else a transcript on the episode page, else local Whisper.
      Engine auto = MLX Whisper on Apple Silicon (GPU), faster-whisper elsewhere; model defaults to
      large-v3-turbo. Each text is cached and archived like an article (archive/), and listed in
      backfill/podcasts.jsonl, which also makes the run resumable: listed episodes are skipped.
      Commits and pushes archive + manifest every 10 episodes, never inside reader.md's quiet_window (the
      morning run's window, in reader.md's timezone); with --no-git it only writes files.

  python3 tools/backfill.py articles-list --since YYYY-MM-DD [--sources id,id,...] [--max-pages 300]
      Lists the history of article Sources (not podcasts) into backfill/articles/<source>.jsonl: url, title (or
      the URL slug), date, how it was found. Tries, in order: the site's sitemaps (robots.txt, sitemap.xml,
      sitemap_index.xml, wp-sitemap.xml; nested and .gz; entries before --since dropped by lastmod or by a year
      in the sitemap's name), the feed paged back (?paged=N, /page/N/feed/), and listing pages (?page=N,
      /page/N/). Keeps going until entries are older than --since. Re-running overwrites a source's list.

  python3 tools/backfill.py status
      Episodes done per show, transcript kinds, audio hours, and what is left since the last --since used.

Stop with one Ctrl+C or `kill <pid>`: the episode in progress finishes, then it commits and exits.
A second signal stops at once. Rerun the same command to resume. One backfill per checkout at a time.
"""
import sys, os, json, time, signal, subprocess, platform, datetime, hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph  # noqa: E402
import fetch  # noqa: E402
import paths  # noqa: E402

ROOT = graph.ROOT
MANIFEST = os.path.join(ROOT, "backfill", "podcasts.jsonl")
LOCK = os.path.join(ROOT, "backfill", ".lock")
STOP = {"asked": 0.0}


def on_signal(sig, frame):
    if STOP["asked"] and time.time() - STOP["asked"] > 1:
        print(json.dumps({"stopped": "now"}), flush=True)
        os._exit(130)
    STOP["asked"] = time.time()
    print(json.dumps({"stopping": "after the episode in progress"}), flush=True)


def opt(args, name, default=None):
    return args[args.index(name) + 1] if name in args else default


def done_urls():
    if not os.path.exists(MANIFEST):
        return set()
    return {json.loads(l)["url"] for l in open(MANIFEST, encoding="utf-8") if l.strip()}


def morning_window():
    """True inside reader.md's quiet_window ("HH:MM-HH:MM", local to its timezone), when the scheduled run pushes."""
    win = paths.cfg("quiet_window") or ""
    if "-" not in win:
        return False
    a, b = (datetime.time(*map(int, x.strip().split(":"))) for x in win.split("-", 1))
    try:
        from zoneinfo import ZoneInfo
        t = datetime.datetime.now(ZoneInfo(paths.cfg("timezone") or "UTC")).time()
    except Exception:
        t = datetime.datetime.now(datetime.timezone.utc).time()
    return a <= t < b if a <= b else (t >= a or t < b)


def git_sync(n, final=False):
    """Commit archive + manifest and push. Inside the morning run's window (reader.md quiet_window) a periodic sync is
    skipped, so transcription keeps going and the next sync catches up; only the final sync waits the window out.
    FOLD_PUSH_ANYTIME=1 (the reader says the morning run is done) ignores the window."""
    anytime = os.environ.get("FOLD_PUSH_ANYTIME") == "1"
    if morning_window() and not anytime and not final:
        print(json.dumps({"git": "skipped: morning run window, catching up later"}), flush=True)
        return
    while morning_window() and not anytime:
        print(json.dumps({"git": f"waiting for the morning run to finish ({paths.cfg('quiet_window')} {paths.cfg('timezone')})"}), flush=True)
        time.sleep(600)
    run = lambda *c: subprocess.run(["git", "-C", ROOT, *c], capture_output=True, text=True)
    run("add", "archive", "backfill/podcasts.jsonl")
    if not run("diff", "--cached", "--quiet").returncode:
        return
    run("commit", "-q", "-m", f"Backfill: {n} podcast episodes")
    for _ in range(2):
        r = run("pull", "-q", "--rebase", "--autostash", "origin", "main")
        p = run("push", "-q", "origin", "HEAD:main")
        if p.returncode == 0:
            print(json.dumps({"git": "pushed", "episodes": n}), flush=True)
            return
    print(json.dumps({"git": "push failed", "error": (r.stderr + p.stderr)[-300:]}), flush=True)


class Transcriber:
    def __init__(self, engine, model):
        if engine == "auto":
            engine = "mlx" if platform.system() == "Darwin" and platform.machine() == "arm64" else "faster"
        self.engine, self.model = engine, model or "large-v3-turbo"
        if engine == "mlx":
            import mlx_whisper  # noqa: F401  (pip install mlx-whisper)
            self.repo = self.model if "/" in self.model else f"mlx-community/whisper-{self.model}"
        else:
            from faster_whisper import WhisperModel
            self.fw = WhisperModel(self.model, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 2)

    def __call__(self, path, lang):
        """Returns timed segments [(start_s, None, text)] and the audio duration in seconds."""
        if self.engine == "mlx":
            import mlx_whisper
            r = mlx_whisper.transcribe(path, path_or_hf_repo=self.repo, language=lang or None,
                                       condition_on_previous_text=False, verbose=None)
            segs = [(s["start"], None, s["text"]) for s in r.get("segments", [])]
            return segs, (segs[-1][0] if segs else 0)
        segs, info = self.fw.transcribe(path, language=lang or None, beam_size=1, vad_filter=True,
                                        condition_on_previous_text=False)
        return [(s.start, None, s.text) for s in segs], info.duration


def download(url, path):
    import requests
    with requests.get(url, headers={"user-agent": fetch.HEADERS["user-agent"]}, timeout=60, stream=True) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)


def cmd_podcasts(args):
    since = opt(args, "--since")
    if not since:
        return fetch.out({"error": "--since YYYY-MM-DD is required"})
    group = opt(args, "--group", "all")
    shows = {s.strip().lower() for s in opt(args, "--shows", "").split(",") if s.strip()}
    limit = int(opt(args, "--limit", "0")) or None
    use_git = "--no-git" not in args
    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    if os.path.exists(LOCK):
        pid = open(LOCK).read().strip()
        try:
            os.kill(int(pid), 0)
            return fetch.out({"error": f"a backfill is already running (pid {pid})"})
        except (ProcessLookupError, ValueError):
            pass
    open(LOCK, "w").write(str(os.getpid()))
    signal.signal(signal.SIGINT, on_signal); signal.signal(signal.SIGTERM, on_signal)
    try:
        nodes, _ = graph.load()
        srcs = [n for n in nodes.values() if n["type"] == "Source" and n.get("props", {}).get("kind") == "podcast"
                and (group == "all" or n["props"].get("group") == group)
                and (not shows or n["name"].lower().replace(" (podcast)", "") in shows)]
        srcs.sort(key=lambda n: n["props"].get("group") != "trade")
        done = done_urls() | {n.get("props", {}).get("url") for n in nodes.values() if n["type"] == "Article"}
        queue = []
        for s in srcs:
            try:
                for it in fetch.podcast_items(s, since=since, cap=None):
                    it["source"] = s["id"]
                    if it.get("url") and it["url"] not in done:
                        queue.append(it)
            except Exception as e:
                print(json.dumps({"show": s["name"], "feed_error": fetch.explain(e)}), flush=True)
        queue.sort(key=lambda i: (i.get("group") != "trade", -(int((i.get("published") or "0000-00-00").replace("-", "")))))
        if limit:
            queue = queue[:limit]
        print(json.dumps({"episodes_to_do": len(queue), "shows": len(srcs), "since": since}), flush=True)
        stt, work = None, os.path.join(ROOT, "backfill", "audio")
        os.makedirs(work, exist_ok=True)
        n, last_sync = 0, time.time()
        for it in queue:
            if STOP["asked"]:
                break
            u, rec = it["url"], {"url": it["url"], "show": it.get("show"), "source": it["source"], "group": it.get("group"),
                                 "title": it.get("title"), "published": it.get("published"), "duration_s": it.get("duration_s")}
            t0 = time.time()
            try:
                text, how = fetch.publisher_transcript(it.get("transcripts") or []), "publisher"
                if not text and it.get("audio") != u:
                    text, how = fetch.page_transcript(u), "page"
                if not text:
                    if not it.get("audio"):
                        raise RuntimeError("no audio url")
                    stt = stt or Transcriber(opt(args, "--engine", "auto"), opt(args, "--model"))
                    path = os.path.join(work, hashlib.sha1(u.encode()).hexdigest() + ".audio")
                    download(it["audio"], path)
                    segs, dur = stt(path, it.get("lang"))
                    os.remove(path)
                    text, how = fetch.paragraphs(segs), f"whisper:{stt.engine}:{stt.model}"
                    rec["audio_s"] = round(dur)
                header = f"{it.get('show')}: {it.get('title')} ({it.get('published')})\n\nShow notes: {it.get('show_notes', '')}\n\nTranscript ({how}):\n\n"
                fetch.store_text([u, it.get("audio")], header + text)
                rec.update(ok=True, transcript=how, words=len(text.split()), seconds=round(time.time() - t0))
            except Exception as e:
                rec.update(ok=False, error=fetch.explain(e))
            if rec.get("ok") or "no audio" in rec.get("error", ""):
                with open(MANIFEST, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(json.dumps(rec, ensure_ascii=False), flush=True)
            n += 1
            if use_git and (n % 10 == 0 or time.time() - last_sync > 1200):
                git_sync(n); last_sync = time.time()
        if use_git:
            git_sync(n, final=True)
        print(json.dumps({"finished": n, "stopped_early": bool(STOP["asked"])}), flush=True)
    finally:
        if os.path.exists(LOCK) and open(LOCK).read().strip() == str(os.getpid()):
            os.remove(LOCK)


def cmd_status(args):
    rows = [json.loads(l) for l in open(MANIFEST, encoding="utf-8")] if os.path.exists(MANIFEST) else []
    per = {}
    for r in rows:
        p = per.setdefault(r.get("show"), {"episodes": 0, "audio_hours": 0.0, "kinds": {}, "oldest": None, "newest": None})
        p["episodes"] += 1
        p["audio_hours"] = round(p["audio_hours"] + (r.get("audio_s") or 0) / 3600, 1)
        k = (r.get("transcript") or "failed").split(":")[0]
        p["kinds"][k] = p["kinds"].get(k, 0) + 1
        d = r.get("published")
        if d:
            p["oldest"] = min(p["oldest"] or d, d); p["newest"] = max(p["newest"] or d, d)
    fetch.out({"episodes": len(rows), "per_show": per})


ART_DIR = os.path.join(ROOT, "backfill", "articles")


def _xml_locs(body):
    """(loc, lastmod) pairs of a sitemap or sitemap index."""
    import re
    out = []
    for blk in re.findall(r"<(?:url|sitemap)>(.*?)</(?:url|sitemap)>", body, re.S):
        loc = re.search(r"<loc>\s*(.*?)\s*</loc>", blk, re.S)
        lm = re.search(r"<lastmod>\s*(\d{4}-\d\d-\d\d)", blk)
        title = re.search(r"<news:title>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</news:title>", blk, re.S)
        pub = re.search(r"<news:publication_date>\s*(\d{4}-\d\d-\d\d)", blk)
        if loc:
            out.append({"loc": loc.group(1).replace("&amp;", "&"), "date": (pub or lm).group(1) if (pub or lm) else None,
                        "title": title.group(1).strip() if title else None})
    return out


def _get_text(url):
    import gzip as gz
    r = fetch.get(url, allow_browser=False)
    if r.status_code >= 400:
        return None
    raw = getattr(r, "content", None) or r.text.encode()
    if url.endswith(".gz") or raw[:2] == b"\x1f\x8b":
        try:
            return gz.decompress(raw).decode("utf-8", "replace")
        except Exception:
            return None
    return r.text


def _slug_title(u):
    import re
    from urllib.parse import urlparse
    seg = [x for x in urlparse(u).path.split("/") if x]
    t = re.sub(r"\.(html?|php)$", "", seg[-1] if seg else u)
    t = re.sub(r"[-_]+", " ", re.sub(r"[-_]?\d{5,}$", "", t)).strip()
    return t[:160]


def _year_ok(name, since):
    import re
    ys = re.findall(r"(20\d\d)", name)
    return not ys or max(int(y) for y in ys) >= int(since[:4])


def list_sitemaps(base, since, budget=400):
    import re
    from urllib.parse import urljoin
    starts = []
    rb = _get_text(urljoin(base, "/robots.txt")) or ""
    starts += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", rb)
    starts += [urljoin(base, p) for p in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml", "/sitemap-news.xml")]
    seen, todo, items = set(), list(dict.fromkeys(starts)), {}
    host = re.sub(r"^www\.", "", re.sub(r"^https?://([^/]+).*", r"\1", base))
    while todo and budget > 0:
        sm = todo.pop(0)
        if sm in seen:
            continue
        seen.add(sm); budget -= 1
        body = _get_text(sm)
        if not body or "<loc>" not in body:
            continue
        locs = _xml_locs(body)
        if "<sitemapindex" in body or all(re.search(r"\.xml(\.gz)?($|\?)", x["loc"]) for x in locs[:5]):
            for x in locs:
                if (x["date"] and x["date"] < since) or not _year_ok(x["loc"], since):
                    continue
                # sub-sitemaps of archive pages, not articles (common CMS names, English and German)
                if re.search(r"(page|categor|tag|taxonom|firmen|person|people|author|issue|image|video|attachment)s?[-_.]", x["loc"].rsplit("/", 1)[-1], re.I):
                    continue
                todo.append(x["loc"])
            continue
        for x in locs:
            # listing and archive sections of common CMSs (English and German), not articles
            if host not in x["loc"] or re.search(r"/(category|kategorie|tag|firmen|personen?|autor|author|flag|thema|themen)/", x["loc"]):
                continue
            if x["date"] and x["date"] < since:
                continue
            items.setdefault(x["loc"], {"url": x["loc"], "title": x["title"] or _slug_title(x["loc"]), "date": x["date"], "via": "sitemap"})
    return list(items.values())


def list_feed_pages(feed, since, max_pages):
    import feedparser
    pats = [lambda n: f"{feed}{'&' if '?' in feed else '?'}paged={n}", lambda n: feed.rstrip("/").rsplit("/feed", 1)[0] + f"/page/{n}/feed/"]
    for pat in pats:
        items, first, n = {}, None, 1
        while n <= max_pages:
            try:
                f = feedparser.parse(fetch.get(pat(n) if n > 1 else feed, allow_browser=False).content)
            except Exception:
                break
            links = [e.get("link") for e in f.entries if e.get("link")]
            if not links or (n > 1 and links[0] == first):
                break
            first = first or links[0]
            oldest = None
            for e in f.entries:
                d = e.get("published_parsed") or e.get("updated_parsed")
                d = datetime.datetime(*d[:6]).strftime("%Y-%m-%d") if d else None
                oldest = d or oldest
                if e.get("link") and (not d or d >= since):
                    items.setdefault(e["link"], {"url": e["link"], "title": (e.get("title") or "").strip(), "date": d, "via": "feed-paged"})
            if oldest and oldest < since:
                break
            n += 1
        if n > 2:
            return list(items.values())
    return []


def cmd_articles_list(args):
    since = opt(args, "--since")
    if not since:
        return fetch.out({"error": "--since YYYY-MM-DD required"})
    want = set((opt(args, "--sources") or "").split(",")) - {""}
    max_pages = int(opt(args, "--max-pages", 300))
    nodes, _ = graph.load()
    srcs = [n for n in nodes.values() if n["type"] == "Source" and (n.get("props") or {}).get("kind") != "podcast"
            and ((n["id"] in want) if want else (n.get("props") or {}).get("status") == "active")]
    os.makedirs(ART_DIR, exist_ok=True)
    summary = []
    for s in srcs:
        p = s.get("props") or {}
        base = p.get("url") or p.get("feed")
        res = {"id": s["id"]}
        try:
            items = list_sitemaps(base, since) if base else []
            res["sitemap"] = len(items)
            if len(items) < 20 and p.get("feed"):
                fi = list_feed_pages(p["feed"], since, max_pages)
                res["feed_paged"] = len(fi)
                if len(fi) > len(items):
                    items = fi
        except Exception as e:
            res["error"] = f"{type(e).__name__}: {str(e)[:150]}"; items = []
        items.sort(key=lambda x: x.get("date") or "", reverse=True)
        with open(os.path.join(ART_DIR, s["id"].split(":", 1)[1] + ".jsonl"), "w", encoding="utf-8") as fh:
            for it in items:
                fh.write(json.dumps({**it, "source": s["id"]}, ensure_ascii=False) + "\n")
        res.update(listed=len(items), oldest=items[-1].get("date") if items else None)
        summary.append(res)
        print(json.dumps(res, ensure_ascii=False), flush=True)
    fetch.out({"sources": len(summary), "listed": sum(r["listed"] for r in summary)})


if __name__ == "__main__":
    cmds = {"podcasts": cmd_podcasts, "status": cmd_status, "articles-list": cmd_articles_list}
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    if c not in cmds:
        print(__doc__); sys.exit(1)
    cmds[c](sys.argv[2:])
