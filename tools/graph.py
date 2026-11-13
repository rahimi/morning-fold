#!/usr/bin/env python3
"""The Morning Fold knowledge graph.

Source of truth is plain text: graph/nodes.jsonl and graph/edges.jsonl (one JSON object per line).
Vectors (graph/vectors.npz) are a cache: every node and edge is embedded automatically when its
text changes, using multilingual-e5-small (int8 ONNX; fetch it once with tools/get_model.py). Delete
the cache and run `reindex` to rebuild it. Needs: pip install numpy onnxruntime tokenizers

All paths are the reader's instance (see tools/paths.py: HOME), never the engine checkout.

Every command prints JSON. Run `python3 tools/graph.py help` for usage.
"""
import sys, os, json, re, hashlib, unicodedata, datetime, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402

ROOT = paths.HOME           # the instance: every file this tool reads or writes lives under it
GDIR = os.path.join(ROOT, "graph")
NODES, EDGES, VECS = (os.path.join(GDIR, f) for f in ("nodes.jsonl", "edges.jsonl", "vectors.npz"))
MODEL = paths.model_dir()

# Node types are open, but these are the ones the paper relies on.
# The relation vocabulary. upsert accepts others but reports them under `problems`, so the graph converges
# on one word per meaning. Past roles: the role relation with valid_to, not WORKED_AT/LEFT.
RELS = {
    "roles": ["CEO_OF", "CO_CEO_OF", "MANAGING_DIRECTOR_OF", "BOARD_MEMBER_OF", "SUPERVISORY_BOARD_MEMBER_OF",
              "PROKURIST_OF", "PARTNER_AT", "MANAGING_PARTNER_OF", "HEAD_OF", "WORKS_AT", "FOUNDER_OF",
              "SPOKESPERSON_OF", "JOINED", "LEFT", "WORKED_AT"],
    "ownership": ["OWNS", "PARENT_OF", "MANAGES"],
    "deals": ["ACQUIRED", "SOLD", "MERGED_INTO", "INVESTED_IN", "RAISED", "REFINANCED", "ADVISED"],
    "business": ["PARTNERED_WITH", "USES", "COMPETES_WITH", "LAUNCHED", "SPEAKS_AT", "HOSTS"],
    "register": ["DELETED_FROM_REGISTER", "DEFUNCT_PER_REGISTER"],
    "claims": ["SAID", "REPORTED", "REPORTED_SIZE"],
    # policy, regulation and public projects: actor -> Policy (an act, rule, decision or consultation) or -> Project
    "policy": ["PROPOSED", "CONSULTED_ON", "APPROVED", "DECIDED", "LAUNCHED_PROJECT", "APPLIES_TO"],
    "paper": ["FROM", "CITES", "ABOUT", "CONCERNS", "REPORTS_ON", "CONTAINS", "PREVIEWS", "MENTIONS", "PREFERS",
              "EVIDENCE_FOR", "EVIDENCE_AGAINST", "SUPPORTS"],
}
ALL_RELS = {r for v in RELS.values() for r in v}

CORE_TYPES = ["Company", "Fund", "Person", "Deal", "Event", "Source", "Article",
              "Thesis", "Story", "Issue", "Rule", "Reader", "Topic", "Signal",
              "Regulator", "Policy", "Project"]
ACTOR_TYPES = ("Company", "Fund", "Person", "Regulator", "Policy", "Project")   # real-world entities worth watching


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or "x"


def out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- storage
BACKFILL = os.environ.get("FOLD_BACKFILL") == "1"   # writes made by the backfill are history, never news


def load():
    nodes, edges = {}, {}
    for path, store in ((NODES, nodes), (EDGES, edges)):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        o = json.loads(line)
                        store[o["id"]] = o
    return nodes, edges


def save(nodes, edges):
    os.makedirs(GDIR, exist_ok=True)
    for path, store in ((NODES, nodes), (EDGES, edges)):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for k in sorted(store):
                f.write(json.dumps(store[k], ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(tmp, path)


# ---------------------------------------------------------------- embeddings
_model = None


DIMS = 384


def model():
    """multilingual-e5-small (int8 ONNX): one file, or split in parts (an instance that commits the model keeps
    each part under GitHub's 100 MB limit); parts are joined in memory."""
    global _model
    if _model is None:
        import glob
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        files = sorted(glob.glob(os.path.join(MODEL, "e5-small-int8.onnx.part*"))) or \
            [p for p in [os.path.join(MODEL, "e5-small-int8.onnx")] if os.path.exists(p)]
        if not files or not os.path.exists(os.path.join(MODEL, "tokenizer.json")):
            raise SystemExit(f"embedding model missing in {MODEL}: {paths.MODEL_HINT}")
        blob = b"".join(open(p, "rb").read() for p in files)
        so = ort.SessionOptions(); so.log_severity_level = 3
        sess = ort.InferenceSession(blob, so, providers=["CPUExecutionProvider"])
        tok = Tokenizer.from_file(os.path.join(MODEL, "tokenizer.json"))
        tok.enable_truncation(256); tok.enable_padding()
        _model = (np, tok, sess, {i.name for i in sess.get_inputs()})
    return _model


def embed(texts, kind="passage"):
    """kind='passage' for stored text, 'query' for search text (e5 convention)."""
    np, tok, sess, names = model()
    res = np.zeros((len(texts), DIMS), dtype=np.float32)
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))  # batch similar lengths together
    for s in range(0, len(order), 32):
        idx = order[s:s + 32]
        enc = tok.encode_batch([f"{kind}: {texts[i]}" for i in idx])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        am = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": am}
        if "token_type_ids" in names:
            feed["token_type_ids"] = np.zeros_like(ids)
        hid = sess.run(None, feed)[0]
        v = (hid * am[..., None]).sum(1) / am.sum(1, keepdims=True)
        res[idx] = v / np.linalg.norm(v, axis=1, keepdims=True)
    return res


def node_text(n):
    p = n.get("props", {})
    bits = [f"{n['type']}: {n.get('name', '')}"]
    if n.get("aliases"):
        bits.append("also known as " + ", ".join(n["aliases"]))
    if n.get("text"):
        bits.append(n["text"])
    for k in ("role", "summary", "claim", "status", "sector"):
        if p.get(k):
            bits.append(str(p[k]))
    return ". ".join(bits)[:4000]


def edge_text(e, nodes):
    nm = lambda i: nodes.get(i, {}).get("name", i)
    t = f"{nm(e['src'])} {e['rel'].replace('_', ' ').lower()} {nm(e['dst'])}"
    if e.get("note"):
        t += ". " + e["note"]
    return t


def h(s):
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def load_vecs():
    import numpy as np
    if not os.path.exists(VECS):
        return [], [], np.zeros((0, DIMS), dtype=np.float32)
    z = np.load(VECS, allow_pickle=False)
    return list(z["ids"]), list(z["hashes"]), z["vecs"].astype(np.float32)


def sync_vecs(nodes, edges, full=False):
    """Embed whatever is new or changed; drop vectors of deleted items."""
    import numpy as np
    want = {i: node_text(n) for i, n in nodes.items()}
    want.update({i: edge_text(e, nodes) for i, e in edges.items()})
    ids, hashes, vecs = ([], [], None) if full else load_vecs()
    have = {i: (hh, k) for k, (i, hh) in enumerate(zip(ids, hashes))}
    keep_ids, keep_h, keep_rows, todo = [], [], [], []
    for i, txt in want.items():
        hh = h(txt)
        if i in have and have[i][0] == hh:
            keep_ids.append(i); keep_h.append(hh); keep_rows.append(vecs[have[i][1]])
        else:
            todo.append((i, hh, txt))
    if todo:
        new = embed([t for _, _, t in todo])
        for (i, hh, _), v in zip(todo, new):
            keep_ids.append(i); keep_h.append(hh); keep_rows.append(v)
    mat = np.stack(keep_rows).astype(np.float16) if keep_rows else np.zeros((0, DIMS), np.float16)
    tmp = VECS[:-4] + f".{os.getpid()}.tmp.npz"     # written aside and swapped in: a parallel reader never sees half a file
    np.savez_compressed(tmp, ids=np.array(keep_ids), hashes=np.array(keep_h), vecs=mat)
    os.replace(tmp, VECS)
    return len(todo)


# ---------------------------------------------------------------- helpers
def resolve(nodes, ref):
    """Accept an id, an exact name or alias (case-insensitive)."""
    if ref in nodes:
        return ref
    r = ref.lower().strip()
    hits = [i for i, n in nodes.items()
            if n.get("name", "").lower() == r or r in [a.lower() for a in n.get("aliases", [])]]
    # real-world entities win over bookkeeping nodes with the same name
    hits.sort(key=lambda i: (nodes[i]["type"] in ("Source", "Article", "Story", "Issue"), i))
    return hits[0] if hits else None


def name_sim(a, b):
    """Best string similarity between any name/alias of a and of b (accents and case ignored)."""
    import difflib
    na = [slug(x) for x in [a.get("name", "")] + a.get("aliases", [])]
    nb = [slug(x) for x in [b.get("name", "")] + b.get("aliases", [])]
    best = 0.0
    for x in na:
        for y in nb:
            if x and y:
                r = 1.0 if (x in y or y in x) and min(len(x), len(y)) >= 4 else difflib.SequenceMatcher(None, x, y).ratio()
                best = max(best, r)
    return best


def brief(n, extra=None):
    d = {"id": n["id"], "type": n["type"], "name": n.get("name")}
    if n.get("text"):
        d["text"] = n["text"][:300]
    if extra:
        d.update(extra)
    return d


def adjacency(edges):
    adj = collections.defaultdict(list)
    for e in edges.values():
        if not live(e):
            continue
        adj[e["src"]].append((e, e["dst"]))
        adj[e["dst"]].append((e, e["src"]))
    return adj


def live(e, asof=None):
    """A fact is live unless it was invalidated (on or before `asof`, default: ever). With `asof`, only facts learned by then."""
    if asof:
        return (e.get("created") or "")[:10] <= asof and not ((e.get("invalid_at") or "9999")[:10] <= asof)
    return not e.get("invalid_at")


def edge_view(e, nodes):
    nm = lambda i: nodes.get(i, {}).get("name", i)
    d = {"id": e["id"], "fact": f"{nm(e['src'])} —{e['rel']}→ {nm(e['dst'])}",
         "src": e["src"], "rel": e["rel"], "dst": e["dst"]}
    for k in ("valid_from", "valid_to", "observed", "confidence", "source", "note", "quote", "grounded", "verified",
              "invalid_at", "invalidated_by", "created"):
        if e.get(k) not in (None, ""):
            d[k] = e[k]
    return d


# ---------------------------------------------------------------- grounding
# Article texts fetched in this run are cached by tools/fetch.py; a fact's quote must occur in its source's text.
TEXT_CACHE = os.environ.get("FOLD_TEXT_CACHE") or os.path.join(paths.WORK, "text")
_DASH = re.compile(r"[\u2010-\u2015\u2212]")
_DQ = re.compile(r"[\u201c-\u201f\u00ab\u00bb\u2033]")
_SQ = re.compile(r"[\u2018-\u201b\u2039\u203a\u2032]")


def text_path(url):
    return os.path.join(TEXT_CACHE, hashlib.sha1(url.encode()).hexdigest() + ".txt")


def _norm(s):
    s = s.replace("\u00ad", "")
    s = re.sub(r"-\s+(?=[a-zäöüß])", "", s)          # words broken across lines
    s = _DASH.sub("-", s); s = _DQ.sub('"', s); s = _SQ.sub("'", s)
    s = unicodedata.normalize("NFKD", s).lower()
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip()


def ground(quote, text):
    """'exact' when the normalised quote occurs in the normalised text, 'alnum' when its letters and digits do, else None."""
    q, t = _norm(quote), _norm(text)
    if q and q in t:
        return "exact"
    qa, ta = re.sub(r"[^\w]", "", q), re.sub(r"[^\w]", "", t)
    if len(qa) >= 12 and qa in ta:
        return "alnum"
    return None


def source_text(nodes, source):
    url = source if source.startswith("http") else nodes.get(resolve(nodes, source) or source, {}).get("props", {}).get("url")
    if not url:
        return None
    p = text_path(url)
    if os.path.exists(p):
        return open(p, encoding="utf-8").read()
    a = archive_path(url)
    if os.path.exists(a):
        import gzip
        return gzip.open(a, "rt", encoding="utf-8").read()
    return None


ARCHIVE = os.path.join(ROOT, "archive")


def archive_path(url):
    """Raw article texts are kept, gzipped, in the repo so facts can be re-checked after the page changes or disappears."""
    hx = hashlib.sha1(url.encode()).hexdigest()
    return os.path.join(ARCHIVE, hx[:2], hx + ".txt.gz")


# ---------------------------------------------------------------- commands
def cmd_upsert(args):
    """Read {"nodes":[...], "edges":[...]} from a file or stdin. Nodes merge by id (or by type+name);
    props/aliases/sources merge, text is replaced if given. Edges need src, rel, dst; same
    src+rel+dst+valid_from updates the existing edge. Returns ids plus possible duplicates."""
    data = json.load(open(args[0], encoding="utf-8") if args else sys.stdin)
    nodes, edges = load()
    ts = now()
    created, updated, dupes, problems = [], [], [], []
    for n in data.get("nodes", []):
        if not n.get("type") or not (n.get("name") or n.get("id")):
            problems.append({"node": n, "error": "needs type and name"}); continue
        nid = n.get("id") or resolve(nodes, n["name"]) or f"{n['type'].lower()}:{slug(n['name'])}"
        if nid in nodes and nodes[nid]["type"] != n["type"] and not n.get("id"):
            nid = f"{n['type'].lower()}:{slug(n['name'])}"
        cur = nodes.get(nid)
        if cur is None:
            cur = {"id": nid, "type": n["type"], "name": n.get("name", nid), "created": ts}
            if BACKFILL:
                cur["backfill"] = True
            nodes[nid] = cur; created.append(nid)
        else:
            updated.append(nid)
        for k in ("name", "text", "type"):
            if n.get(k):
                cur[k] = n[k]
        if n.get("aliases"):
            cur["aliases"] = sorted(set(cur.get("aliases", [])) | set(n["aliases"]) - {cur["name"]})
        if n.get("props"):
            cur.setdefault("props", {}).update(n["props"])
        if n.get("sources"):
            cur["sources"] = sorted(set(cur.get("sources", [])) | set(n["sources"]))
        cur["updated"] = ts
    # The same fact by its fields, not its hash: an edge's id is fixed when it is created, so after its
    # valid_from was corrected (or was given as null rather than omitted) a hash of the new fields misses it,
    # and a second source would open a duplicate instead of landing under `also`.
    fkey = lambda s, r, d, x: (s, r, d, x.get("valid_from") or "", x.get("quote", "") if r == "SAID" else "")
    by_fields = {fkey(x["src"], x["rel"], x["dst"], x): k for k, x in edges.items() if live(x)}
    for e in data.get("edges", []):
        src, dst = resolve(nodes, e.get("src", "")), resolve(nodes, e.get("dst", ""))
        if not src or not dst or not e.get("rel"):
            problems.append({"edge": e, "error": "unknown src/dst or missing rel (create the nodes first)"}); continue
        rel = re.sub(r"[^A-Z0-9_]", "_", e["rel"].upper())
        if src == dst and rel != "SAID":
            problems.append({"edge": {k: e.get(k) for k in ("src", "rel", "dst")}, "warning":
                             "edge from a node to itself; a statement about oneself is SAID, a plan is SAID"})
        if rel not in ALL_RELS:
            problems.append({"edge": {k: e.get(k) for k in ("src", "rel", "dst")}, "warning":
                             f"relation {rel} is not in the vocabulary (graph.py help: relations); written anyway, prefer a listed one"})
        grounded = None
        if e.get("quote") and e.get("source"):
            txt = source_text(nodes, e["source"])
            if txt is not None:
                grounded = ground(e["quote"], txt)
                if not grounded:
                    problems.append({"edge": e, "error": "quote not found in the source text: copy it character for character from the article, or drop the fact"})
                    continue
            else:
                grounded = "unchecked"
        key = f"{src}|{rel}|{dst}|{e.get('valid_from', '')}" + (f"|{e.get('quote', '')}" if rel == "SAID" else "")
        eid = e.get("id") or by_fields.get(fkey(src, rel, dst, e)) or "e:" + h(key)   # SAID: one edge per quote
        by_fields.setdefault(fkey(src, rel, dst, e), eid)
        cur = edges.get(eid)
        if cur is None:
            cur = {"id": eid, "src": src, "rel": rel, "dst": dst, "created": ts}
            if BACKFILL:
                cur["backfill"] = True
            edges[eid] = cur; created.append(eid)
        else:
            updated.append(eid)
        keys = ("valid_from", "valid_to", "observed", "confidence", "source", "note", "quote", "verified", "props")
        if cur.get("source") and e.get("source") and e["source"] != cur["source"] and not e.get("replace") \
                and not (e["source"].startswith("article:") and not str(cur["source"]).startswith("article:")):
            # A second source for a known fact corroborates it: the first source, quote and note stay, the
            # new one is listed under `also`, and confidence becomes the higher of the two.
            also = [a for a in cur.get("also", []) if a.get("source") != e["source"]]
            also.append({k: e[k] for k in ("source", "quote", "note", "confidence") if k in e} | {"observed": ts[:10]}
                        | ({"grounded": grounded} if grounded else {}))
            cur["also"] = also
            if "confidence" in e:
                cur["confidence"] = max(cur.get("confidence", 0), e["confidence"])
            keys = ("valid_to", "props")
            grounded = None
        for k in keys:
            if k in e:
                cur[k] = e[k]
        cur.setdefault("observed", ts[:10])
        if grounded:
            cur["grounded"] = grounded
        cur["updated"] = ts
    save(nodes, edges)
    embedded = sync_vecs(nodes, edges)
    # duplicate check for freshly created nodes
    if created:
        import numpy as np
        ids, _, vecs = load_vecs()
        pos = {i: k for k, i in enumerate(ids)}
        for nid in [c for c in created if c in nodes]:
            if nid not in pos:
                continue
            sims = vecs @ vecs[pos[nid]]
            for k in np.argsort(-sims)[:10]:
                other = ids[k]
                if other == nid or other not in nodes or nodes[other]["type"] != nodes[nid]["type"]:
                    continue
                ns = name_sim(nodes[nid], nodes[other])
                if ns > 0.85 and sims[k] > 0.88:
                    dupes.append({"new": nid, "maybe_same_as": other, "name_similarity": round(ns, 2),
                                  "meaning_similarity": round(float(sims[k]), 3)})
    out({"created": created, "updated": updated, "embedded": embedded,
         "possible_duplicates": dupes, "problems": problems})


def cmd_search(args):
    """search "<text>" [--type T] [--k 12] [--edges]  hybrid: meaning + name match."""
    import numpy as np
    q, typ, k, want_edges = None, None, 12, False
    it = iter(args)
    for a in it:
        if a == "--type": typ = next(it)
        elif a == "--k": k = int(next(it))
        elif a == "--edges": want_edges = True
        else: q = a if q is None else q + " " + a
    nodes, edges = load()
    ids, _, vecs = load_vecs()
    qv = embed([q], kind="query")[0]
    sims = vecs @ qv if len(ids) else np.zeros(0)
    def norm(s):    # "Müller", "Mueller" and "MÜLLER." compare equal; so do "Acme Group" and "acme-group"
        s = (s or "").casefold()
        for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
            s = s.replace(a, b)
        return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())
    ql = norm(q)
    sim = {i: float(s) for i, s in zip(ids, sims)}
    scored = []
    for i, n in nodes.items():     # every node, also one whose vector isn't cached yet: an exact name must never be missed
        if typ and n["type"] != typ: continue
        names = [norm(n.get("name"))] + [norm(a) for a in n.get("aliases", [])]
        bonus = 1.0 if ql in names else 0.25 if any(nm and (nm in ql or ql in nm) for nm in names) else 0
        if i in sim or bonus:
            scored.append((sim.get(i, 0.0) + bonus, i, "node"))
    if want_edges:
        scored += [(s, i, "edge") for i, s in sim.items() if i in edges]
    scored.sort(reverse=True)
    res = []
    for s, i, kind in scored[:k]:
        res.append(brief(nodes[i], {"score": round(s, 3)}) if kind == "node"
                   else dict(edge_view(edges[i], nodes), score=round(s, 3)))
    out(res)


def cmd_get(args):
    nodes, edges = load()
    i = resolve(nodes, " ".join(args))
    if not i:
        return out({"error": "not found", "hint": "use search"})
    n = nodes[i]
    hist = "--history" in sys.argv
    facts = [edge_view(e, nodes) for e in edges.values() if i in (e["src"], e["dst"]) and (hist or live(e))]
    facts.sort(key=lambda f: f.get("valid_from") or f.get("observed") or "", reverse=True)
    out({"node": n, "facts": facts[:80], "fact_count": len(facts)})


def cmd_neighbors(args):
    """neighbors <id|name> [--depth 2] [--rel REL]"""
    depth, rel, ref = 1, None, []
    it = iter(args)
    for a in it:
        if a == "--depth": depth = int(next(it))
        elif a == "--rel": rel = next(it).upper()
        else: ref.append(a)
    nodes, edges = load()
    start = resolve(nodes, " ".join(ref))
    if not start:
        return out({"error": "not found"})
    adj, seen, frontier, facts = adjacency(edges), {start: 0}, [start], {}
    for d in range(depth):
        nxt = []
        for x in frontier:
            for e, y in adj[x]:
                if rel and e["rel"] != rel: continue
                if not live(e): continue
                facts[e["id"]] = e
                if y not in seen:
                    seen[y] = d + 1; nxt.append(y)
        frontier = nxt
    out({"start": start, "nodes": [brief(nodes[n], {"hops": seen[n]}) for n in seen if n in nodes],
         "facts": [edge_view(e, nodes) for e in facts.values()]})


def cmd_path(args):
    """path <a> <b>  shortest connection between two entities."""
    nodes, edges = load()
    a, b = resolve(nodes, args[0]), resolve(nodes, args[1])
    if not a or not b:
        return out({"error": "not found"})
    meta = {"ABOUT", "EVIDENCE_FOR", "EVIDENCE_AGAINST", "PREFERS", "CITES", "FROM", "MENTIONS"}
    adj, prev, q = adjacency(edges), {a: None}, collections.deque([a])
    while q:
        x = q.popleft()
        if x == b: break
        for e, y in adj[x]:
            if e["rel"] in meta and "--all" not in args:
                continue
            if y not in prev:
                prev[y] = (x, e); q.append(y)
    if b not in prev:
        return out({"path": None})
    steps, x = [], b
    while prev[x]:
        p, e = prev[x]; steps.append(edge_view(e, nodes)); x = p
    out({"path": steps[::-1]})


def cmd_timeline(args):
    """timeline <id|name> [--asof YYYY-MM-DD] [--history]  facts in date order (valid_from, else observed).
    --asof: what the paper knew on that day (learned by then, not yet invalidated). --history: include invalidated facts."""
    asof = args[args.index("--asof") + 1] if "--asof" in args else None
    hist = "--history" in args
    ref = [a for k, a in enumerate(args) if a not in ("--asof", "--history") and (k == 0 or args[k - 1] != "--asof")]
    nodes, edges = load()
    i = resolve(nodes, " ".join(ref))
    if not i:
        return out({"error": "not found"})
    fs = [edge_view(e, nodes) for e in edges.values() if i in (e["src"], e["dst"])
          and (live(e, asof) if asof else (hist or live(e)))]
    fs.sort(key=lambda f: f.get("valid_from") or f.get("observed") or "")
    out(fs)


def cmd_list(args):
    """list --type T [--where key=value] [--k 200]"""
    typ, where, k = None, {}, 200
    it = iter(args)
    for a in it:
        if a == "--type": typ = next(it)
        elif a == "--where": kk, vv = next(it).split("=", 1); where[kk] = vv
        elif a == "--k": k = int(next(it))
    nodes, _ = load()
    res = []
    for n in nodes.values():
        if typ and n["type"] != typ: continue
        p = n.get("props", {})
        if any(str(p.get(kk)) != vv for kk, vv in where.items()): continue
        res.append({"id": n["id"], "name": n.get("name"), "props": p})
    out(res[:k])


def cmd_changes(args):
    """changes --since YYYY-MM-DD [--with-backfill]  what was created or updated since then (backfilled history left out)."""
    since = args[args.index("--since") + 1] if "--since" in args else now()[:10]
    nodes, edges = load()
    if "--with-backfill" not in args:
        edges = {k: v for k, v in edges.items() if not v.get("backfill")}
        touched = {x for e in edges.values() if (e.get("updated") or "") >= since for x in (e["src"], e["dst"])}
        nodes = {k: v for k, v in nodes.items() if not v.get("backfill") or k in touched}
    out({"nodes": [brief(n, {"created": n.get("created"), "updated": n.get("updated")})
                   for n in nodes.values() if (n.get("updated") or "") >= since and n["type"] not in ("Article",)],
         "articles": [{"id": n["id"], "name": n.get("name"), "url": n.get("props", {}).get("url")}
                      for n in nodes.values() if n["type"] == "Article" and (n.get("created") or "") >= since],
         "facts": [edge_view(e, nodes) for e in edges.values() if (e.get("updated") or "") >= since]})


def cmd_seen(args):
    """seen <url> [<url>...]  which article URLs are already in the graph."""
    nodes, _ = load()
    urls = {n.get("props", {}).get("url") for n in nodes.values() if n["type"] == "Article"}
    out({u: (u in urls) for u in args})


def cmd_merge(args):
    """merge <keep> <drop>  move all facts and aliases from drop to keep, delete drop."""
    nodes, edges = load()
    keep, drop = resolve(nodes, args[0]), resolve(nodes, args[1])
    if not keep or not drop or keep == drop:
        return out({"error": "need two different existing nodes"})
    k, d = nodes[keep], nodes.pop(drop)
    k["aliases"] = sorted(set(k.get("aliases", [])) | set(d.get("aliases", [])) | {d.get("name", "")} - {k["name"], ""})
    k.setdefault("props", {}).update({x: y for x, y in d.get("props", {}).items() if x not in k["props"]})
    k["sources"] = sorted(set(k.get("sources", [])) | set(d.get("sources", [])))
    if not k.get("text") and d.get("text"):
        k["text"] = d["text"]
    for e in edges.values():
        if e["src"] == drop: e["src"] = keep
        if e["dst"] == drop: e["dst"] = keep
    k["updated"] = now()
    save(nodes, edges)
    sync_vecs(nodes, edges)
    out({"merged": drop, "into": keep})


def cmd_delete(args):
    nodes, edges = load()
    gone = []
    for ref in args:
        if ref in edges:
            edges.pop(ref); gone.append(ref)
        else:
            i = resolve(nodes, ref)
            if i:
                nodes.pop(i); gone.append(i)
                for eid in [x for x, e in edges.items() if i in (e["src"], e["dst"])]:
                    edges.pop(eid); gone.append(eid)
    save(nodes, edges); sync_vecs(nodes, edges)
    out({"deleted": gone})


def cmd_stats(args):
    nodes, edges = load()
    out({"nodes": len(nodes), "edges": len(edges),
         "by_type": dict(collections.Counter(n["type"] for n in nodes.values()).most_common()),
         "by_rel": dict(collections.Counter(e["rel"] for e in edges.values()).most_common(30))})


def cmd_reindex(args):
    nodes, edges = load()
    out({"embedded": sync_vecs(nodes, edges, full=True)})


def _sources_of(e, nodes):
    s = e.get("source")
    return [s] if s else []


def published(e, nodes):
    """When the evidence for a fact appeared: its source Article's published date, else its valid_from."""
    a = nodes.get(e.get("source") or "", {}).get("props", {}).get("published") or ""
    return (a or e.get("valid_from") or "")[:10]


def cmd_brief(args):
    """The small start-up layer: everything a run should know first, in one compact view."""
    nodes, edges = load()
    today = now()[:10]
    issues = sorted((n for n in nodes.values() if n["type"] == "Issue"),
                    key=lambda n: (n.get("props", {}).get("date", ""), n.get("props", {}).get("number", 0)))
    last = issues[-1] if issues else None
    since = (last or {}).get("props", {}).get("date") or (last or {}).get("created", "")[:10] or today
    theses = [n for n in nodes.values() if n["type"] == "Thesis" and n.get("props", {}).get("status", "open") == "open"]
    def tline(t):
        p = t.get("props", {}); h = p.get("history", [])
        prev = h[-2]["probability"] if len(h) > 1 else None
        return {"id": t["id"], "name": t["name"], "p": p.get("probability"), "prev": prev,
                "resolves": p.get("resolves"), "last_why": (h[-1].get("why", "")[:160] if h else "")}
    rules = [{"name": r["name"], "why": (r.get("text") or "")[:200], "helpful": r.get("props", {}).get("helpful", 0),
              "harmful": r.get("props", {}).get("harmful", 0)} for r in nodes.values() if r["type"] == "Rule"]
    recent = [edge_view(e, nodes) for e in edges.values()
              if live(e) and (e.get("created") or "") >= since
              and (not e.get("backfill") or published(e, nodes) >= since)
              and e["rel"] not in ("FROM", "CITES", "CONTAINS", "ABOUT")]
    rules = sorted(rules, key=lambda r: -(r.get("helpful", 0) - r.get("harmful", 0)))
    recent.sort(key=lambda f: f.get("valid_from") or f.get("observed") or "", reverse=True)
    deg = collections.Counter()
    for e in edges.values():
        deg[e["src"]] += 1; deg[e["dst"]] += 1
    watched = [{"id": i, "name": nodes[i]["name"], "type": nodes[i]["type"], "facts": c}
               for i, c in deg.most_common(60) if i in nodes and nodes[i]["type"] in ACTOR_TYPES][:20]
    src = collections.Counter(n.get("props", {}).get("status", "seed") for n in nodes.values() if n["type"] == "Source")
    rd = paths.reader()
    reader_node = next((n for n in nodes.values() if n["type"] == "Reader"), {})
    # every headline the paper printed in the last 7 days (all editions, also-noted lines too): never run them again
    import glob
    week_ago = (datetime.date.fromisoformat(today) - datetime.timedelta(days=7)).isoformat()
    printed = []
    for f in sorted(glob.glob(os.path.join(ROOT, "issues", "*.json"))):
        try:
            iss = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (iss.get("date") or "") < week_ago:
            continue
        ed = {"date": iss.get("date"), "edition": iss.get("edition") or iss.get("kind"),
              "stories": [s.get("headline") for s in iss.get("stories", []) if s.get("headline")]}
        also = iss.get("also_noted") or []
        if isinstance(also, str):
            also = [also]
        also = list(also) + list(iss.get("in_brief") or [])
        if also:
            ed["also_noted"] = [a if isinstance(a, str) else (a.get("headline") or a.get("text") or "")[:160] for a in also]
        printed.append(ed)
    out({"today": today, "last_issue": last and {"number": last.get("props", {}).get("number"), "date": since},
         "reader": ({"file": rd["path"], "settings": rd["meta"], "text": rd["text"]} if rd["path"]
                    else {"text": reader_node.get("text"), "beats": reader_node.get("props", {}).get("beats")}),
         "printed_last_7_days": printed,
         "rules": rules, "open_theses": [tline(t) for t in theses],
         "facts_since_last_issue": recent[:40], "facts_since_count": len(recent),
         "most_connected": watched, "sources": dict(src),
         "graph": {"nodes": len(nodes), "edges": len(edges)}})


# ---------------------------------------------------------------- story work: dossier and patterns
PAPER_RELS = {"FROM", "CITES", "CONTAINS", "ABOUT", "MENTIONS", "REPORTS_ON", "PREVIEWS", "CONCERNS", "PREFERS"}
ENTITY_TYPES = ("Company", "Fund", "Person", "Topic", "Event", "Deal", "Regulator", "Policy", "Project")


def origin(src, nodes):
    """The outlet behind a piece of evidence, so a press release and its rewrites in one outlet count once:
    an Article's Source (or show), a Source node itself, else the host of a URL."""
    if not src:
        return None
    n = nodes.get(src)
    if n and n["type"] == "Article":
        p = n.get("props", {})
        return p.get("source") or p.get("show") or re.sub(r"^https?://(www\.)?([^/]+).*", r"\2", p.get("url") or src)
    if n:
        return src
    return re.sub(r"^https?://(www\.)?([^/]+).*", r"\2", src)


def origins_of(e, nodes):
    return {o for o in [origin(e.get("source"), nodes)] + [origin(a.get("source"), nodes) for a in e.get("also", [])] if o}


def is_podcast(o, nodes):
    """A podcast never counts as an independent origin (RUN.md, Podcasts)."""
    return (nodes.get(o, {}).get("props") or {}).get("kind") == "podcast"


def outlets_of(es, nodes):
    """(counted outlets, podcasts) behind a set of facts."""
    allo = set().union(*(origins_of(e, nodes) for e in es)) if es else set()
    pods = {o for o in allo if is_podcast(o, nodes)}
    return allo - pods, pods


def resolve_loose(nodes, ref):
    """resolve(), else the entity whose name or alias contains / best matches the reference."""
    i = resolve(nodes, ref)
    if i:
        return i
    s = slug(ref)
    cands = [n for n in nodes.values() if n["type"] in ENTITY_TYPES]
    hit = [n for n in cands if any(s and s in slug(x) for x in [n.get("name", "")] + n.get("aliases", []))]
    if not hit:
        hit = [n for n in cands if name_sim(n, {"name": ref}) >= 0.85]
    hit.sort(key=lambda n: len(n.get("name", "")))
    return hit[0]["id"] if hit else None


def _printed_recently(nodes, edges, days=7):
    """Entity ids that a Story printed in the last `days` days is ABOUT."""
    cut = (datetime.date.fromisoformat(now()[:10]) - datetime.timedelta(days=days)).isoformat()
    stories = {i for i, n in nodes.items() if n["type"] == "Story" and (n.get("created") or "")[:10] >= cut}
    return {e["dst"] for e in edges.values() if e["rel"] == "ABOUT" and e["src"] in stories}


def cmd_dossier(args):
    """dossier <id|name> [<id|name> ...] [--days 90] [--q "archive question"] [--no-passages]
    Everything the memory holds on a candidate story's entities, to write from before writing."""
    days = int(args[args.index("--days") + 1]) if "--days" in args else 90
    use_pass = "--no-passages" not in args
    q_arg = args[args.index("--q") + 1] if "--q" in args else None
    refs = [a for k, a in enumerate(args) if not a.startswith("--") and (k == 0 or args[k - 1] not in ("--days", "--q"))]
    nodes, edges = load()
    ids, missing = [], []
    for r in refs:
        i = resolve_loose(nodes, r)
        (ids.append(i) if i else missing.append(r))
    if not ids:
        return out({"error": "not found", "missing": missing})
    today = now()[:10]
    cut = (datetime.date.fromisoformat(today) - datetime.timedelta(days=days)).isoformat()
    idset = set(ids)
    def fact(e):
        v = edge_view(e, nodes)
        v["published"] = published(e, nodes)
        v["origins"] = sorted(origins_of(e, nodes))
        if e.get("backfill"): v["backfill"] = True
        if e.get("also"): v["corroborated_by"] = [a.get("source") for a in e["also"]]
        if v.get("quote"): v["quote"] = v["quote"][:200]
        for k in ("src", "dst", "rel", "grounded", "created"): v.pop(k, None)
        return v
    ents, srcs_used, all_origins = [], set(), set()
    for i in ids:
        mine = [e for e in edges.values() if i in (e["src"], e["dst"]) and e["rel"] not in PAPER_RELS]
        cur = [e for e in mine if live(e)]
        recent = [e for e in cur if (published(e, nodes) or e.get("observed") or "") >= cut]
        older = [e for e in cur if e not in recent]
        gone = [e for e in mine if e.get("invalid_at") and e["invalid_at"][:10] >= cut]
        # one-holder facts with two live holders (two current CEOs, two parents): a conflict worth telling the reader
        by = collections.defaultdict(set)
        for e in cur:
            if e.get("valid_to"):
                continue
            if e["rel"] in ("CEO_OF", "CO_CEO_OF"):
                by[(e["dst"], e["rel"], "holder")].add(e["src"])
            elif e["rel"] in ("PARENT_OF", "OWNS"):
                by[(e["dst"], e["rel"], "owner")].add(e["src"])
            elif e["rel"] == "MERGED_INTO":
                by[(e["src"], e["rel"], "into")].add(e["dst"])
        nm = lambda x: nodes.get(x, {}).get("name", x)
        conflicts = [{"about": nm(k), "rel": r, "values": sorted(nm(x) for x in v)} for (k, r, _), v in by.items()
                     if len(v) > (2 if r == "CO_CEO_OF" else 1) and r != "OWNS"]
        key = lambda e: published(e, nodes) or e.get("observed") or ""
        for e in cur:
            srcs_used.add(e.get("source")); all_origins |= origins_of(e, nodes)
        n = nodes[i]
        ents.append({"id": i, "name": n.get("name"), "type": n["type"], "text": (n.get("text") or "")[:400],
                     "recent_facts": [fact(e) for e in sorted(recent, key=key, reverse=True)[:40]],
                     "older_facts": [fact(e) for e in sorted(older, key=key, reverse=True)[:25]],
                     "invalidated_in_window": [fact(e) for e in gone][:10],
                     "conflicts": conflicts,
                     "counts": {"live": len(cur), "recent": len(recent), "older": len(older)}})
    # theses touched: directly, or through an article these facts rest on
    theses = {}
    for e in edges.values():
        if not live(e) or e["rel"] not in ("EVIDENCE_FOR", "EVIDENCE_AGAINST", "SUPPORTS", "ABOUT"):
            continue
        t = e["dst"] if nodes.get(e["dst"], {}).get("type") == "Thesis" else None
        if t and (e["src"] in idset or e["src"] in srcs_used or e.get("source") in srcs_used):
            tp = nodes[t].get("props", {})
            theses.setdefault(t, {"id": t, "name": nodes[t]["name"], "p": tp.get("probability"), "links": []})
            theses[t]["links"].append({"rel": e["rel"], "via": nodes.get(e["src"], {}).get("name", e["src"]), "note": (e.get("note") or "")[:160]})
    # what the paper already printed about them
    stories = []
    for e in edges.values():
        if e["rel"] == "ABOUT" and e["dst"] in idset and nodes.get(e["src"], {}).get("type") == "Story":
            s = nodes[e["src"]]
            stories.append({"id": s["id"], "date": (s.get("created") or "")[:10], "headline": s.get("name")})
    signals = [brief(nodes[e["src"] if e["dst"] in idset else e["dst"]]) for e in edges.values()
               if idset & {e["src"], e["dst"]} and "Signal" in (nodes.get(e["src"], {}).get("type"), nodes.get(e["dst"], {}).get("type"))]
    res = {"entities": ents, "missing": missing, "theses": list(theses.values()),
           "printed_before": sorted({s["id"]: s for s in stories}.values(), key=lambda s: s["date"], reverse=True),
           "signals": signals, "independent_origins": sorted(o for o in all_origins if not is_podcast(o, nodes)),
                 "podcasts": sorted(o for o in all_origins if is_podcast(o, nodes))}
    if use_pass:
        try:
            import io, contextlib, passages
            q = q_arg or " ".join(nodes[i]["name"] for i in ids)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                passages.cmd_search([q, "--k", "30"])
            per_url, keep = collections.Counter(), []
            for r in json.loads(buf.getvalue()).get("results", []):
                per_url[r.get("url") or r.get("title")] += 1
                if per_url[r.get("url") or r.get("title")] <= 2 and len(keep) < 8:   # breadth over one long article
                    keep.append({k: r.get(k) for k in ("date", "kind", "source", "title", "url", "at")} | {"text": r["text"][:500]})
            res["archive_passages"] = keep
        except Exception as ex:   # the index is optional; the dossier still works without it
            res["archive_passages_error"] = str(ex)[:200]
    out(res)


def cmd_patterns(args):
    """patterns [--days 14] [--min 3] [--all]
    Story candidates no single article shows: entities with >= min new facts from >= 2 independent outlets in the
    window, not printed in the last 7 days (--all shows those too)."""
    get = lambda k, d: int(args[args.index(k) + 1]) if k in args else d
    days, mn, show_all = get("--days", 14), get("--min", 3), "--all" in args
    nodes, edges = load()
    cut = (datetime.date.fromisoformat(now()[:10]) - datetime.timedelta(days=days)).isoformat()
    printed = _printed_recently(nodes, edges)
    per = collections.defaultdict(list)
    for e in edges.values():
        if not live(e) or e["rel"] in PAPER_RELS or e["rel"] in ("EVIDENCE_FOR", "EVIDENCE_AGAINST", "SUPPORTS"):
            continue
        if (published(e, nodes) or "") < cut:     # backfilled history is old by its own date, so it drops out here
            continue
        for x in {e["src"], e["dst"]}:
            if nodes.get(x, {}).get("type") in ENTITY_TYPES:
                per[x].append(e)
    thesis_of = collections.defaultdict(set)
    for e in edges.values():
        if live(e) and e["rel"] in ("EVIDENCE_FOR", "EVIDENCE_AGAINST") and nodes.get(e["dst"], {}).get("type") == "Thesis":
            thesis_of[e["src"]].add(nodes[e["dst"]]["name"][:80])
            if e.get("source"): thesis_of[e["source"]].add(nodes[e["dst"]]["name"][:80])
    cands = []
    for x, es in per.items():
        orig, pods = outlets_of(es, nodes)
        if len(es) < mn or len(orig) < 2:
            continue
        if x in printed and not show_all:
            continue
        others = collections.Counter(nodes.get(y, {}).get("name", y) for e in es for y in (e["src"], e["dst"])
                                     if y != x and nodes.get(y, {}).get("type") in ENTITY_TYPES)
        ths = set(thesis_of.get(x, set())).union(*(thesis_of.get(e.get("source"), set()) for e in es))
        cands.append({"id": x, "name": nodes[x]["name"], "type": nodes[x]["type"], "facts": len(es), "origins": sorted(orig), "podcasts": sorted(pods),
                      "rels": dict(collections.Counter(e["rel"] for e in es).most_common(5)),
                      "with": [n for n, _ in others.most_common(6)], "theses": sorted(ths),
                      "printed_recently": x in printed,
                      "sample": [edge_view(e, nodes)["fact"] + f" ({published(e, nodes)})" for e in
                                 sorted(es, key=lambda e: published(e, nodes), reverse=True)[:5]]})
    cands.sort(key=lambda c: (-len(c["origins"]), -c["facts"]))
    out({"window_days": days, "since": cut, "candidates": cands[:25], "count": len(cands)})


def cmd_audit(args):
    """Work list for the dreaming pass: what to verify, merge, close or prune."""
    nodes, edges = load()
    edges = {k: e for k, e in edges.items() if live(e)}     # invalidated facts are history, not work
    today = datetime.date.today()
    # facts resting on one source with confidence < 0.8 and not yet verified
    weak = [edge_view(e, nodes) for e in edges.values()
            if e["rel"] not in ("FROM", "CITES", "CONTAINS", "ABOUT", "PREFERS")
            and not e.get("verified") and (float(e.get("confidence") or 1) < 0.8 or e.get("grounded") == "unchecked")]
    # duplicate candidates among entities (name similarity)
    ents = [n for n in nodes.values() if n["type"] in ACTOR_TYPES]
    dup = []
    for i, a in enumerate(ents):
        for b in ents[i + 1:]:
            if a["type"] == b["type"]:
                s = name_sim(a, b)
                if s >= 0.85:
                    dup.append({"a": a["id"], "b": b["id"], "name_similarity": round(s, 2)})
    # roles that may be stale: open-ended role facts older than 18 months
    roles = {"CEO_OF", "MANAGING_DIRECTOR_OF", "WORKS_AT", "WORKED_AT", "JOINED", "PARTNER_AT", "HEAD_OF"}
    stale = []
    for e in edges.values():
        if e["rel"] in roles and not e.get("valid_to"):
            vf = (e.get("valid_from") or e.get("observed") or "")[:10]
            try:
                y, m = int(vf[:4]), int(vf[5:7]) if len(vf) >= 7 else 1
                if (today.year - y) * 12 + today.month - m > 18:
                    stale.append(edge_view(e, nodes))
            except ValueError:
                pass
    # conflicting facts: same src+rel, different dst, overlapping or open validity
    by = collections.defaultdict(list)
    for e in edges.values():
        if e["rel"] in ("CEO_OF", "OWNS", "MANAGING_DIRECTOR_OF") and not e.get("valid_to"):
            by[(e["rel"], e["dst"] if e["rel"] == "OWNS" else e["src"])].append(e)
    conflicts = [[edge_view(x, nodes) for x in v] for v in by.values() if len({x["src"] if x["rel"] == "OWNS" else x["dst"] for x in v}) > 1]
    orphans = [brief(n) for n in nodes.values() if n["type"] in ACTOR_TYPES + ("Topic",)
               and not any(n["id"] in (e["src"], e["dst"]) for e in edges.values())]
    thin = [brief(n) for n in ents if not n.get("text")]
    src_fail = [{"id": n["id"], "name": n["name"], "status": n.get("props", {}).get("status"),
                 "failures": n.get("props", {}).get("failures"), "stories": n.get("props", {}).get("stories", 0),
                 "articles": n.get("props", {}).get("articles", 0)}
                for n in nodes.values() if n["type"] == "Source"]
    rules = [{"id": n["id"], "name": n["name"], "text": n.get("text"), "created": n.get("created")}
             for n in nodes.values() if n["type"] == "Rule"]
    out({"verify_weak_facts": weak[:60], "weak_count": len(weak), "possible_duplicates": dup[:40],
         "possibly_stale_roles": stale[:40], "conflicts": conflicts[:20], "orphans": orphans[:40],
         "entities_without_summary": thin[:60], "sources": src_fail, "rules": rules})


def cmd_invalidate(args):
    """invalidate <edge_id> --by <article id | reason>  a fact stopped being true or was superseded: it stays in history."""
    if "--by" not in args or len(args) < 3:
        return out({"error": "usage: invalidate <edge_id> --by <article id or reason>"})
    eid, by = args[0], args[args.index("--by") + 1]
    nodes, edges = load()
    if eid not in edges:
        return out({"error": f"no edge {eid}"})
    edges[eid]["invalid_at"] = now()
    edges[eid]["invalidated_by"] = by
    edges[eid]["updated"] = now()
    save(nodes, edges)
    out({"invalidated": edge_view(edges[eid], nodes)})


def cmd_rule(args):
    """rule <name|id> helpful|harmful [--note "..."]  count a learned rule as helpful or harmful in this run (ACE-style playbook)."""
    if len(args) < 2 or args[1] not in ("helpful", "harmful"):
        return out({"error": "usage: rule <name|id> helpful|harmful [--note text]"})
    nodes, edges = load()
    i = resolve(nodes, args[0])
    if not i or nodes[i]["type"] != "Rule":
        return out({"error": "no such rule; create it with upsert (type Rule)"})
    pr = nodes[i].setdefault("props", {})
    pr[args[1]] = pr.get(args[1], 0) + 1
    if "--note" in args:
        pr.setdefault("log", []).append({"date": now()[:10], "vote": args[1], "note": args[args.index("--note") + 1][:200]})
        pr["log"] = pr["log"][-20:]
    nodes[i]["updated"] = now()
    save(nodes, edges)
    out({"rule": nodes[i]["name"], "helpful": pr.get("helpful", 0), "harmful": pr.get("harmful", 0)})


def _days(a, b):
    import datetime as dt
    return (dt.date.fromisoformat(b[:10]) - dt.date.fromisoformat(a[:10])).days


def _brier(th, outcome):
    """Final and time-averaged Brier score of one resolved thesis. Each probability in props.history
    holds from its date until the next entry or the resolution date."""
    pr = th.get("props", {})
    hist = sorted((h for h in pr.get("history", []) if "probability" in h), key=lambda h: h["date"])
    end = pr.get("resolved_on") or pr.get("resolves") or now()[:10]
    if not hist:
        return None
    final = hist[-1]["probability"]
    total = weight = 0.0
    for i, h in enumerate(hist):
        until = hist[i + 1]["date"] if i + 1 < len(hist) else end
        w = max(_days(h["date"], until), 1)
        total += w * (h["probability"] - outcome) ** 2
        weight += w
    return {"final_p": final, "final_brier": round((final - outcome) ** 2, 4),
            "time_avg_brier": round(total / weight, 4), "days_tracked": _days(hist[0]["date"], end)}


def cmd_score(args):
    """score  the Fold's own track record: Brier scores of resolved theses, and the early-signal log
    (what it published first, whether others reported it later, and how many days later)."""
    nodes, _ = load()
    today = now()[:10]
    resolved, open_ = [], []
    for n in nodes.values():
        if n["type"] != "Thesis":
            continue
        pr = n.get("props", {})
        st = pr.get("status", "open")
        if st in ("resolved_true", "resolved_false"):
            b = _brier(n, 1.0 if st == "resolved_true" else 0.0)
            if b:
                resolved.append({"thesis": n["name"], "outcome": st[9:], "resolved_on": pr.get("resolved_on"), **b})
        elif st == "open":
            open_.append({"thesis": n["name"], "p": pr.get("probability"), "resolves": pr.get("resolves"),
                          "days_left": _days(today, pr["resolves"]) if pr.get("resolves") else None,
                          "moves": max(len(pr.get("history", [])) - 1, 0)})
    theses = {"resolved": sorted(resolved, key=lambda r: r.get("resolved_on") or ""),
              "open": sorted(open_, key=lambda r: r["days_left"] if r["days_left"] is not None else 99999),
              "baseline_brier": 0.25}
    if resolved:
        theses["mean_final_brier"] = round(sum(r["final_brier"] for r in resolved) / len(resolved), 4)
        theses["mean_time_avg_brier"] = round(sum(r["time_avg_brier"] for r in resolved) / len(resolved), 4)
    sig = {"open": [], "confirmed": [], "refuted": [], "expired": []}
    for n in nodes.values():
        if n["type"] != "Signal":
            continue
        pr = n.get("props", {})
        st = pr.get("status", "open")
        pub = pr.get("published")
        row = {"signal": n["name"], "kind": pr.get("kind"), "published": pub}
        if st == "confirmed" and pub and pr.get("confirmed_on"):
            row.update(confirmed_on=pr["confirmed_on"], confirmed_by=pr.get("confirmed_by"),
                       lead_days=_days(pub, pr["confirmed_on"]))
        elif st == "open" and pub:
            row["age_days"] = _days(pub, today)
            if row["age_days"] > 90:
                st = "expired"
        sig.setdefault(st, []).append(row)
    leads = [r["lead_days"] for r in sig["confirmed"] if "lead_days" in r]
    decided = len(sig["confirmed"]) + len(sig["refuted"]) + len(sig["expired"])
    summary = {"published": sum(len(v) for v in sig.values()), "confirmed": len(sig["confirmed"]),
               "hit_rate": round(len(sig["confirmed"]) / decided, 2) if decided else None,
               "median_lead_days": sorted(leads)[len(leads) // 2] if leads else None}
    out({"theses": theses, "signals": {"summary": summary, **sig}})


SYNC_KEYS = ("url", "feed", "kind", "group", "lang", "cadence", "status")


def seed_from_instance(nodes, edges, sync=False):
    """Adds what the instance's files declare and the graph lacks: a Source node per sources.json entry and an
    open Thesis per theses.json entry. Never overwrites: once a node exists, the graph (its counters, status,
    history) is the truth and the file is only its starting point. Returns the ids created."""
    ts, made = now(), []
    for s in paths.sources_file()["sources"]:
        if not s.get("name"):
            continue
        sid = s.get("id") or f"source:{slug(s['name'])}"
        if sid in nodes:
            if sync:      # the file's address and status win; counters (last_read, failures, articles) stay
                pr = nodes[sid].setdefault("props", {})
                new = {k: s[k] for k in SYNC_KEYS if k in s and pr.get(k) != s[k]}
                if new:
                    pr.update(new); nodes[sid]["updated"] = ts; made.append(sid + " (synced)")
            continue
        props = {k: v for k, v in s.items() if k not in ("id", "name", "text")}
        props.setdefault("status", "active")
        props.setdefault("cadence", "daily")
        nodes[sid] = {"id": sid, "type": "Source", "name": s["name"], "text": s.get("text", ""), "props": props,
                      "created": ts, "updated": ts}
        made.append(sid)
    for t in paths.load_json("theses.json", []):
        if not t.get("name") or not t.get("claim"):
            continue
        tid = t.get("id") or f"thesis:{slug(t['name'])}"
        if tid in nodes or resolve(nodes, t["name"]):
            continue
        p = float(t.get("probability", 0.5))
        nodes[tid] = {"id": tid, "type": "Thesis", "name": t["name"], "text": t["claim"],
                      "props": {"claim": t["claim"], "probability": p, "resolves": t.get("resolves"), "status": "open",
                                "origin": t.get("origin", "reader"),
                                "history": [{"date": ts[:10], "probability": p, "why": t.get("why", "seed thesis")}]},
                      "created": ts, "updated": ts}
        made.append(tid)
    return made


def cmd_seed(args):
    """seed [--sync] [--no-embed]  add Source nodes from sources.json and Thesis nodes from theses.json that the graph
    doesn't have yet. --sync also copies url, feed, kind, group, lang, cadence and status of existing sources from the file."""
    nodes, edges = load()
    made = seed_from_instance(nodes, edges, sync="--sync" in args)
    if made:
        save(nodes, edges)
        if "--no-embed" not in args:
            sync_vecs(nodes, edges)
    out({"created": made, "home": ROOT})


def cmd_help(args):
    out({"commands": {
        "brief": "START HERE: compact start-up view (reader, rules, open theses, facts since last issue, key entities)",
        "dossier <id|name>... [--days 90] [--q \"archive question\"] [--no-passages]": "before writing a story: its entities' recent and older facts with outlets, conflicts, invalidations, linked theses, earlier stories, signals, archive passages",
        "patterns [--days 14] [--min 3] [--all]": "story candidates from the memory: entities with several new facts from >= 2 independent outlets, not printed in the last 7 days",
        "audit": "work list for the Saturday dreaming pass (weak facts, duplicates, stale roles, conflicts, orphans)",
        "stats": "counts by node type and relation",
        "search \"text\" [--type T] [--k N] [--edges]": "find by meaning and name; --edges includes facts",
        "get <id|name>": "a node with its facts",
        "neighbors <id|name> [--depth N] [--rel REL]": "surrounding subgraph",
        "path <a> <b>": "shortest connection between two entities",
        "timeline <id|name> [--asof YYYY-MM-DD] [--history]": "facts in date order; --asof = what the paper knew that day",
        "invalidate <edge_id> --by <article|reason>": "a fact stopped being true or was superseded (kept in history; use delete only for extraction errors)",
        "rule <name> helpful|harmful [--note]": "count a learned rule's effect in this run",
        "score": "track record: Brier scores of resolved theses; early-signal log (hit rate, lead days, open signals to check)",
        "list --type T [--where k=v]": "all nodes of a type",
        "changes --since YYYY-MM-DD": "what was added or changed",
        "seen <url>...": "which article URLs are already stored",
        "upsert [file.json]": "add/update {nodes:[], edges:[]} (stdin if no file); embeds automatically",
        "merge <keep> <drop>": "resolve duplicates",
        "delete <id|name>...": "remove nodes (with their facts) or edges",
        "reindex": "rebuild the vector cache",
        "seed [--sync] [--no-embed]": "add Source nodes from sources.json and Thesis nodes from theses.json that the graph lacks; --sync also updates existing sources' url, feed, kind, group, lang, cadence, status from the file (counters stay)"},
        "node_shapes": {
            "Rule": "name = the lesson in a few words, text = the rule and why, props {origin, helpful, harmful}",
            "Thesis": "name, text = claim, props {claim, probability, resolves, status: open|resolved_true|resolved_false|withdrawn, origin: reader|editor, history: [{date, probability, why}]}",
            "Signal": "name = the claim, text = what was seen where, props {published, kind, status: open|confirmed|refuted, confirmed_on, confirmed_by}",
            "Source": "props {url, feed, kind, group, lang, cadence, status, last_read, failures, articles, stories}",
            "Story": "name = headline, text = deck, props {date, issue, kicker, position}; edges CITES articles, ABOUT entities",
            "Issue": "name, props {number, date, kind: issue|week-ahead, url}; edges CONTAINS stories",
            "Article": "name = headline, text = dense summary, props {url, published, lang, source, kind?, show?}; edge FROM its Source",
            "Event": "name, props {date, end_date, kind, location}",
            "Regulator": "a supervisor, agency, ministry, court or parliament acting as such; name, text",
            "Policy": "an act, rule, standard, decision or consultation: name, props {kind: act|rule|standard|decision|consultation, status: proposed|consulting|adopted|in_force|withdrawn, jurisdiction, in_force_from}",
            "Project": "a public or industrial project (a plant, a grid line, a programme): name, props {kind, location, capacity, status: planned|approved|building|operating|cancelled}"},
        "policy_relations": "Regulator|Company|Person PROPOSED Policy; CONSULTED_ON Policy (opened it, or responded); APPROVED Policy|Project; DECIDED Policy|Project (a ruling, a rejection: say which in props.outcome); Company LAUNCHED_PROJECT Project; Policy APPLIES_TO Company|Topic|Project. Dates: valid_from = the day of the act.",
        "relations": RELS,
        "node": {"type": CORE_TYPES, "fields": "id?, type, name, aliases[], text, props{}, sources[]"},
        "edge": {"fields": "src, rel (UPPER_SNAKE), dst, valid_from?, valid_to?, confidence 0-1, source (Article id or URL), quote (short verbatim source wording), note, verified (date checked in a dreaming pass). A quote is checked against the article text cached by fetch.py article (grounded: exact|alnum|unchecked); a quote that is not in the text is refused"}})


WRITES = {"upsert", "merge", "delete", "reindex", "invalidate", "rule", "seed"}

if __name__ == "__main__":
    cmds = {k[4:]: v for k, v in globals().items() if k.startswith("cmd_")}
    c = sys.argv[1] if len(sys.argv) > 1 else "help"
    if c not in cmds:
        c = "help"
    if c in WRITES:
        import fcntl
        os.makedirs(GDIR, exist_ok=True)
        with open(os.path.join(GDIR, ".lock"), "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)      # parallel reporters write one at a time
            cmds[c](sys.argv[2:])
    else:
        cmds[c](sys.argv[2:])
