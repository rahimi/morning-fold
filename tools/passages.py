#!/usr/bin/env python3
"""Search the archive by meaning: every archived article and podcast transcript, cut into passages.

  python3 tools/passages.py index
      Cuts archived texts not yet indexed into passages of about 150 words (paragraph-aware; a transcript
      passage keeps its [mm:ss] time stamp) and embeds them (multilingual e5, the graph's model). Append-only:
      each run writes new part files (index/passages/<UTC time>-<n>.npz) and never rewrites old ones, so git
      history grows only by the new passages.

  python3 tools/passages.py search "query" [--k 10] [--since YYYY-MM-DD] [--until YYYY-MM-DD]
                                           [--kind podcast|article] [--source <text in show or source name>]
      The best matching passages: score, date, title, show or source, url, time stamp, text.

Titles, dates and urls come from the graph's Article nodes and backfill/podcasts.jsonl; a text archived
without either is indexed with its first line as title.
"""
import sys, os, re, json, glob, gzip, hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph  # noqa: E402

ROOT = graph.ROOT
IDX = os.path.join(ROOT, "index", "passages")
WORDS = 150
PART_FILES = 400
JUNK = re.compile(r"technischer Fehler|Cookies? (ein|zu)|cookie (policy|settings)|enable javascript|"
                  r"page not found|Seite nicht gefunden|access denied|subscribe to (read|continue)", re.I)


def out(o):
    print(json.dumps(o, ensure_ascii=False, indent=1))


def meta_by_hash():
    """archive hash -> {url, title, date, kind, source} from Article nodes and the podcast backfill manifest."""
    m = {}
    nodes, _ = graph.load()
    srcname = {n["id"]: n["name"] for n in nodes.values() if n["type"] == "Source"}
    for n in nodes.values():
        if n["type"] != "Article":
            continue
        p = n.get("props", {})
        if p.get("url"):
            m[hashlib.sha1(p["url"].encode()).hexdigest()] = {
                "url": p["url"], "title": n.get("name"), "date": (p.get("published") or "")[:10],
                "kind": p.get("kind") or "article", "source": p.get("show") or srcname.get(p.get("source"), p.get("source"))}
    man = os.path.join(ROOT, "backfill", "podcasts.jsonl")
    if os.path.exists(man):
        for l in open(man, encoding="utf-8"):
            r = json.loads(l)
            m.setdefault(hashlib.sha1(r["url"].encode()).hexdigest(), {
                "url": r["url"], "title": r.get("title"), "date": r.get("published") or "", "kind": "podcast",
                "source": (r.get("show") or "").replace(" (podcast)", "")})
    return m


def cut(text):
    """Paragraph-aware passages of about WORDS words; each keeps the last [mm:ss] stamp seen before it."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out_, cur, stamp, start_stamp = [], [], None, None
    for p in paras:
        m = re.match(r"\[(\d+:\d\d(?::\d\d)?)\]", p)
        if m:
            stamp = m.group(1)
        words = p.split()
        while words:
            if not cur:
                start_stamp = stamp
            take = words[:WORDS - len(cur)]
            cur += take; words = words[len(take):]
            if len(cur) >= WORDS:
                out_.append((start_stamp, " ".join(cur))); cur = []
    if len(cur) >= 25 or (cur and not out_):
        out_.append((start_stamp, " ".join(cur)))
    return out_


def load_shard(sh):
    """A shard: int8 vectors with per-row scale, `meta` rows aligned with the vectors, and `skipped`
    (archive files whose text was already indexed under another url)."""
    import numpy as np
    p = os.path.join(IDX, f"{sh}.npz")
    if not os.path.exists(p):
        return None
    z = np.load(p, allow_pickle=False)
    j = json.loads(z["meta"].tobytes().decode("utf-8"))
    return {"vec": z["vec"], "scale": z["scale"], "meta": j["rows"], "skipped": j.get("skipped", [])}


def save_shard(sh, vec, scale, rows, skipped):
    import numpy as np
    os.makedirs(IDX, exist_ok=True)
    tmp = os.path.join(IDX, f"{sh}.tmp.npz")
    blob = json.dumps({"rows": rows, "skipped": skipped}, ensure_ascii=False).encode("utf-8")
    np.savez_compressed(tmp, vec=vec, scale=scale, meta=np.frombuffer(blob, dtype=np.uint8))
    os.replace(tmp, os.path.join(IDX, f"{sh}.npz"))


def cmd_index(args):
    """Append-only: texts not yet indexed go into NEW part files (index/passages/<UTC time>-<n>.npz, at most
    PART_FILES archive files each); existing parts are never rewritten, so git history grows only by new passages."""
    import numpy as np, datetime
    files = sorted(glob.glob(os.path.join(ROOT, "archive", "*", "*.txt.gz")))
    meta = meta_by_hash()
    parts = [load_shard(os.path.basename(p)[:-4]) for p in glob.glob(os.path.join(IDX, "*.npz"))]
    have = {m["h"] for s in parts for m in s["meta"]} | {h for s in parts for h in s["skipped"]}
    seen_c = {m.get("c") for s in parts for m in s["meta"]}   # one text archived under two urls is indexed once
    todo = [(os.path.basename(f).split(".")[0], f) for f in files]
    todo = [(hx, f) for hx, f in todo if hx not in have]
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    added_passages = 0
    for n, start in enumerate(range(0, len(todo), PART_FILES)):
        texts, rows, skipped = [], [], []
        for hx, f in todo[start:start + PART_FILES]:
            t = gzip.open(f, "rt", encoding="utf-8").read()
            c = hashlib.sha1(t.encode()).hexdigest()[:16]
            if c in seen_c:
                skipped.append(hx); continue
            seen_c.add(c)
            info = meta.get(hx, {"title": t.strip().split("\n", 1)[0][:160], "date": "", "kind": "article", "source": None, "url": None})
            for at, ptxt in cut(t):
                if len(ptxt.split()) < 25 or JUNK.search(ptxt):
                    continue
                texts.append(ptxt)
                rows.append({"h": hx, "c": c, "url": info.get("url"), "title": info.get("title"), "date": info.get("date") or "",
                             "kind": info.get("kind"), "source": info.get("source"), "at": at, "text": ptxt})
            if not any(r["h"] == hx for r in rows[-1:]) and hx not in skipped:
                skipped.append(hx)                      # nothing indexable: remember it so it is not re-read
        if texts:
            V = graph.embed(texts)
            scale = (np.abs(V).max(axis=1, keepdims=True).clip(1e-6) / 127.0).astype(np.float32)
            q = np.round(V / scale).astype(np.int8)
        else:
            q, scale = np.zeros((0, graph.DIMS), np.int8), np.zeros((0, 1), np.float32)
        save_shard(f"{stamp}-{n:03d}", q, scale, rows, skipped)
        added_passages += len(texts)
        print(json.dumps({"part": f"{stamp}-{n:03d}", "files": len(todo[start:start + PART_FILES]), "passages": len(texts)}), flush=True)
    out({"indexed_files": len(todo), "new_passages": added_passages, "archive_files": len(files), "parts": len(parts) + (len(todo) + PART_FILES - 1) // PART_FILES})


def cmd_search(args):
    import numpy as np
    if not args or args[0].startswith("--"):
        return out({"error": 'usage: search "query" [--k 10] [--since D] [--until D] [--kind podcast|article] [--source text]'})
    q = args[0]
    get = lambda n, d=None: args[args.index(n) + 1] if n in args else d
    k, since, until, kind, src = int(get("--k", 10)), get("--since", ""), get("--until", "9999"), get("--kind"), (get("--source") or "").lower()
    Q = graph.embed([q], kind="query")[0]
    terms = {w for w in re.findall(r"\w{4,}", q.lower())}
    hits = []
    for p in glob.glob(os.path.join(IDX, "*.npz")):
        s = load_shard(os.path.basename(p)[:-4])
        if not s or not s["meta"]:
            continue
        keep = []                                       # filters first, then rank only what passes them
        for i, m in enumerate(s["meta"]):
            d = m.get("date") or ""
            if (since and d and d < since) or (d and d > until) or (kind and m.get("kind") != kind) \
                    or (src and src not in (m.get("source") or "").lower()):
                continue
            keep.append(i)
        if not keep:
            continue
        keep = np.array(keep)
        sims = (s["vec"][keep].astype(np.float32) * s["scale"][keep]) @ Q
        for j in np.argsort(-sims)[: k * 8]:
            i = keep[j]; m = s["meta"][i]; sims_i = float(sims[j])
            lex = sum(1 for w in terms if w in m["text"].lower()) / max(len(terms), 1)
            hits.append((sims_i + 0.05 * lex, m))   # names and terms the query states count a little extra
    hits.sort(key=lambda x: -x[0])
    seen, res = set(), []
    for sc, m in hits:
        key = (m["h"], m.get("at"), m["text"][:40])
        if key in seen:
            continue
        seen.add(key)
        res.append({"score": round(sc, 3), "date": m.get("date"), "kind": m.get("kind"), "source": m.get("source"),
                    "title": m.get("title"), "url": m.get("url"), "at": m.get("at"), "text": m["text"]})
        if len(res) >= k:
            break
    out({"query": q, "results": res})


if __name__ == "__main__":
    cmds = {"index": cmd_index, "search": cmd_search}
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    if c not in cmds:
        print(__doc__); sys.exit(1)
    cmds[c](sys.argv[2:])
