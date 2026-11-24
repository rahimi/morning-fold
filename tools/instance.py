#!/usr/bin/env python3
"""Create and check a reader's instance (the private repo that holds reader.md, sources, and all content).

  python3 engine/tools/instance.py init [DIR]
      Lays out an instance in DIR (default: the working directory): the content folders, a .gitignore,
      an instance CLAUDE.md that points every run at engine/RUN.md, and starter reader.md, sources.json and
      theses.json to fill in. Never overwrites a file that exists.

  python3 engine/tools/instance.py check
      Validates the instance the tools would use (HOME): reader.md settings (known keys, types, time zone,
      weekdays), sources.json (names, ids, addresses, kinds, duplicates), theses.json (claim, probability,
      resolution date), the engine submodule and the model. Exit 1 when something must be fixed.

  python3 engine/tools/instance.py leakcheck <file|->
      The leak gate for anything sent to the public engine repository (an issue, a comment, a diff).
      Run it from the instance root with the instance's own engine/; it refuses (exit 2) a home without
      reader.md, inside an engine checkout, or with an example's reader.md. Looks for the instance's private terms in the text: reader.md's name,
      place, masthead, artifact_url and watch list, and the capitalised words of its profile and prose; the names and
      hostnames in sources.json; the names and aliases of the graph's people (also the surname), companies
      and funds (also without a legal suffix such as AG, GmbH, plc), regulators, sources and events, and their ids.
      Whole words, any case, across line breaks and hyphens, umlauts also as ae/oe/ue/ss or dropped. Terms other than
      reader.md's settings are not checked when under 3 characters or already in the engine's own public
      files (docs, tools, templates, the fictional example). Claude's Co-Authored-By trailer line is skipped.
      Prints the hits; exit 1 on any hit, 0 when clean.

  python3 engine/tools/instance.py update [--check | --verify]
      Moves the engine checkout (engine/) to the newest release tag reader.md's engine_updates allows: auto, the
      newest final release vX.Y.Z (majors too); rc, also candidates vX.Y.Z-rcN; off, never. Fetches the tags from
      the engine's origin. Only moves forward along the release line: a checkout that is not an ancestor of that
      tag (a development commit) stays where it is. Prints JSON: current and target version, update (true/false),
      changelog (the CHANGELOG.md sections in between) and instance_changes (their "Instance changes" steps).
      --check: only reports. Otherwise checks the target out and self-tests it with the new tools (instance.py
      check, graph.py brief, fetch.py --help, site.py template issue): on a failure it checks the previous
      commit out again and exits 1 with the failures. When instance_changes are listed it stops after the
      checkout with needs_migration: make those changes in the instance, then run update --verify, which
      self-tests and on a failure returns to the previous commit (undo your changes then: git checkout -- <files>).
      It never commits: on success, commit the engine pointer and the changes as "Engine <current> → <target>".
      A release whose pointer commit the instance reverted (git revert --no-edit) is never taken again.

  python3 engine/tools/instance.py feedback <file|->
      Sends one engine problem to the maintainer's relay (reader.md's feedback_relay): the first line is the
      title, the rest the body. Runs leakcheck on the text first and refuses (exit 1, nothing sent) on any hit.
      Sends the engine version and this instance's random id (data/instance_id, made once; no personal data).
      Prints the relay's answer as JSON: url and action (created, commented or duplicate), or sent false and
      the reason. While feedback_relay is the placeholder (no relay deployed) it sends nothing (exit 0).

  python3 engine/tools/instance.py sync [--push] [--prune]
      Brings in what other runs pushed elsewhere (plain git, the remote origin; none: a no-op). Fetches every
      branch (unshallows a shallow clone), then merges what HEAD lacks: origin's main branch first, then every
      session branch (claude/<name>-<suffix>, <main>-<suffix>; suffix: six lower-case letters and digits) whose
      commits since the merge base touch runs/ or graph/, oldest first; other branches are listed as skipped.
      Refuses (reported, exit 0) while tracked files have changes. Conflicts: graph/*.jsonl three-way by record id
      (changed on both sides: the later updated, else created, wins; a tie, the newer branch; changed on one side
      and deleted on the other: kept and listed); data/first_seen.json by url; graph/vectors.npz, index/ and
      archive/ keep ours (vectors then embed the merged records); the engine pointer the newer engine (then
      reported as engine_changed and checked out). Any other conflict (reader.md, sources.json, an issue ...)
      aborts that merge only and is listed under conflicts. --push: push HEAD to the main branch (if main moved:
      sync again, retry once) and to the current branch when that is another one. A run that starts on a cloud session branch is moved onto
      main first (its commits kept), so the session branch is never pushed and nothing is left to delete. --prune:
      after that push, delete the session branches main holds whose suffix has a digit, never the current one. An
      edge left naming a node the merge removed (src, dst, source) takes the other side's value (conflicts:
      repointed) or is listed (dangling_endpoints). An unexpected error aborts the merge in progress (error).
      Prints JSON: merged, skipped, conflicts, pushed, pruned. Always exits 0.
"""
import collections
import datetime
import json
import os
import re
import subprocess
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402

KINDS = {"feed", "page", "newsroom", "podcast", "calendar", "register", "web"}
STATUSES = {"active", "seed", "candidate", "trial", "citation", "dead", "blocked"}
DAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}

GITIGNORE = """# A Morning Fold instance. Keep this repository PRIVATE.
.work/
__pycache__/
*.tmp
.DS_Store
.venv/
graph/.lock
graph/vectors.npz.tmp
backfill/audio/
backfill/.lock
"""

CLAUDE_MD = """# This is a Morning Fold instance (private)

This repository is one reader's personal newspaper: their profile (`reader.md`), sources (`sources.json`),
seed theses (`theses.json`) and everything the paper has learned (`graph/`, `archive/`, `index/`, `issues/`,
`runs/`, `data/`). It is PRIVATE and must stay private.

The engine (tools, method, templates) is the git submodule in `engine/`.

- A scheduled or manual run: follow `engine/RUN.md`, from this directory.
- Setting up or changing the reader's profile, sources or schedule: see `engine/SETUP.md`.
- Never copy content from this repository into the engine, and never push the engine submodule from here
  with content in it. Engine improvements go to the engine repository as their own change (engine/SETUP.md,
  "Contributing to the engine").
"""

READER_MD = """---
name: TODO the reader's first name
masthead: The Morning Fold
place: TODO city for the dateline
language: en
locale: en-US
timezone: UTC
issue_days: ["Mon", "Tue", "Wed", "Thu", "Fri"]
dreaming_day: Sat
week_ahead_day: Sun
publish_by: "06:55"
artifact_url: ""
watch: []
---

TODO One paragraph on who the reader is and what the paper is for. The ranking uses this paragraph.

## Beats
1. **TODO beat (high):** what belongs to it, which companies, regulators, kinds of news.
2. **TODO beat (medium):** ...
3. **TODO wildcard (low):** only when it truly matters.

Skip: TODO what never to print.

## Watched
- Companies: TODO
- People: TODO

## Voice
TODO tone, length, what "My read" should do for this reader.
"""


def cmd_init(args):
    d = os.path.abspath(args[0]) if args else os.getcwd()
    made = []
    for sub in ("graph", "archive", "issues", "runs", "index", "data", "site"):
        p = os.path.join(d, sub)
        if not os.path.isdir(p):
            os.makedirs(p)
            open(os.path.join(p, ".gitkeep"), "w").close()
            made.append(sub + "/")
    files = {".gitignore": GITIGNORE, "CLAUDE.md": CLAUDE_MD, "reader.md": READER_MD,
             "sources.json": json.dumps({"queries": [], "sources": []}, indent=1) + "\n",
             "theses.json": "[]\n", "data/first_seen.json": "{}\n",
             "graph/nodes.jsonl": "", "graph/edges.jsonl": ""}
    for name, body in files.items():
        p = os.path.join(d, name)
        if not os.path.exists(p):
            open(p, "w", encoding="utf-8").write(body)
            made.append(name)
    print(json.dumps({"instance": d, "created": made,
                      "next": "fill reader.md, sources.json, theses.json; then: python3 engine/tools/instance.py check"}, indent=1))


def cmd_check(args):
    problems, warnings = [], []
    home = paths.HOME
    r = paths.reader()
    if not r["path"]:
        problems.append(f"no reader.md in {home} (set MORNING_FOLD_HOME or run from the instance)")
    own = r["own"]
    for k in own:
        if k not in paths.DEFAULTS:
            warnings.append(f"reader.md: unknown setting '{k}' (ignored by the tools; fine if it's for the editor)")
    for k, v in own.items():
        if k in paths.DEFAULTS and not isinstance(v, type(paths.DEFAULTS[k])) \
                and not (isinstance(paths.DEFAULTS[k], float) and isinstance(v, int)):
            problems.append(f"reader.md: '{k}' should be {type(paths.DEFAULTS[k]).__name__}, got {v!r}")
    todo = lambda t: any(line.strip().lstrip("-0123456789.* ").startswith("TODO") for line in str(t).splitlines())
    if any(todo(v) for v in own.values()) or todo(r["text"]):
        problems.append("reader.md still has TODO placeholders: fill them from the interview")
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(paths.cfg("timezone"))
    except Exception:
        problems.append(f"reader.md: timezone {paths.cfg('timezone')!r} is not an IANA zone name (e.g. Europe/Paris)")
    days = paths.cfg("issue_days")
    if not isinstance(days, list) or not set(days) <= DAYS:
        problems.append(f"reader.md: issue_days must be a list of {sorted(DAYS)}")
    for k in ("dreaming_day", "week_ahead_day"):
        if paths.cfg(k) and paths.cfg(k) not in DAYS:
            problems.append(f"reader.md: {k} must be one of {sorted(DAYS)} or empty")
    hhmm = r"([01]\d|2[0-3]):[0-5]\d"
    if not re.fullmatch(hhmm, str(paths.cfg("publish_by"))):
        problems.append("reader.md: publish_by must be HH:MM (quote it: publish_by: \"06:55\")")
    if paths.cfg("quiet_window") and not re.fullmatch(hhmm + "-" + hhmm, str(paths.cfg("quiet_window"))):
        problems.append("reader.md: quiet_window must be HH:MM-HH:MM (the scheduled run's start to publish_by plus 30 minutes)")
    if not 0 <= paths.cfg("story_bar") <= 12:
        problems.append("reader.md: story_bar is points out of 12")
    if paths.cfg("min_stories") > paths.cfg("max_stories"):
        problems.append("reader.md: min_stories is larger than max_stories")
    if paths.cfg("engine_feedback") not in ("off", "relay", "issue", "pr"):
        problems.append("reader.md: engine_feedback must be off, relay, issue or pr")
    if paths.cfg("engine_updates") not in ("auto", "rc", "off"):
        problems.append("reader.md: engine_updates must be auto, rc or off")
    if not paths.cfg("artifact_url"):
        warnings.append("reader.md: artifact_url is empty (set it after the first publish)")
    if len(paths.profile_text()) < 80:
        warnings.append("reader.md: the profile paragraph is very short; ranking leans on it")

    sf = paths.sources_file()
    srcs = sf["sources"]
    if not srcs:
        problems.append("sources.json: no sources")
    if not sf["queries"]:
        warnings.append("sources.json: no queries (one short ranking text per beat helps a lot)")
    seen, odd_kinds = set(), []
    for k, s in enumerate(srcs):
        tag = f"sources.json[{k}] {s.get('name')!r}"
        if not s.get("name"):
            problems.append(f"{tag}: needs a name")
            continue
        sid = s.get("id") or "source:" + s["name"].lower()
        if s.get("id") and not s["id"].startswith("source:"):
            problems.append(f"{tag}: id must start with 'source:'")
        if sid in seen:
            problems.append(f"{tag}: duplicate id {sid}")
        seen.add(sid)
        if not (s.get("url") or s.get("feed")):
            warnings.append(f"{tag}: no url or feed (it will be cited, never read)")
        if s.get("kind") and s["kind"] not in KINDS:
            odd_kinds.append(s["kind"])
        if s.get("kind") == "podcast" and not s.get("feed"):
            problems.append(f"{tag}: a podcast needs its feed URL (fetch.py podcast-find)")
        if s.get("kind") == "podcast" and s.get("group") not in ("trade", "heard"):
            warnings.append(f"{tag}: podcast group should be 'trade' (read in full) or 'heard' (the reader's own shows)")
        if s.get("status") and s["status"] not in STATUSES:
            warnings.append(f"{tag}: status {s['status']!r} unknown")

    if odd_kinds:
        warnings.append(f"sources.json: {len(odd_kinds)} sources have a kind outside {sorted(KINDS)} "
                        f"(e.g. {sorted(set(odd_kinds))[:3]}); fine as a label, only 'podcast' and 'calendar' change behaviour")

    theses = paths.load_json("theses.json", [])
    for k, t in enumerate(theses):
        tag = f"theses.json[{k}] {t.get('name')!r}"
        if not t.get("name") or not t.get("claim"):
            problems.append(f"{tag}: needs name and claim (the claim states exactly how it resolves)")
        p = t.get("probability")
        if t.get("status", "open") == "open" and (not isinstance(p, (int, float)) or not 0 <= p <= 1):
            problems.append(f"{tag}: probability must be between 0 and 1")
        try:
            datetime.date.fromisoformat(str(t.get("resolves")))
        except ValueError:
            problems.append(f"{tag}: resolves must be a date YYYY-MM-DD")

    ex = os.path.join(paths.ENGINE, "examples", "instance")
    if os.path.realpath(ex) != os.path.realpath(home) and os.path.isdir(ex):
        exs = json.load(open(os.path.join(ex, "sources.json"))).get("sources", [])
        ex_keys = {v for s in exs for v in (s.get("id"), s.get("url"), s.get("feed")) if v}
        ex_theses = {t.get("name") for t in json.load(open(os.path.join(ex, "theses.json")))}
        for s in srcs:
            if {s.get("id"), s.get("url"), s.get("feed")} & ex_keys:
                warnings.append(f"sources.json: {s.get('name')!r} comes from the fictional example instance; "
                                "keep it only if the user chose it")
        for t in theses:
            if t.get("name") in ex_theses:
                warnings.append(f"theses.json: {t.get('name')!r} is the example instance's thesis; write the user's own")

    if not os.path.isdir(os.path.join(home, "engine", "tools")):
        warnings.append("no engine/ submodule here (fine when the engine is checked out elsewhere)")
    md = paths.model_dir()
    if not (os.path.isdir(md) and any(f.startswith("e5-small-int8.onnx") for f in os.listdir(md))):
        problems.append(f"embedding model missing in {md}: {paths.MODEL_HINT}")

    res = {"home": home, "engine": paths.ENGINE, "model": md, "sources": len(srcs), "queries": len(sf["queries"]),
           "theses": len(theses), "settings": {k: paths.cfg(k) for k in paths.DEFAULTS},
           "problems": problems, "warnings": warnings, "ok": not problems}
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if problems:
        sys.exit(1)


SUFFIX = re.compile(r"(?:[\s,]+(?:ag|se|gmbh|kgaa|kg|group|gruppe|holding|plc|inc\.?|n\.v\.|s\.a\.|s\.p\.a\.|ltd\.?|&\s*co\.?|konzern))+$")
UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def cmd_leakcheck(args):
    home = os.path.realpath(paths.HOME)
    engine = os.path.realpath(paths.ENGINE)
    rd = paths.reader()["path"]
    examples = [os.path.join(engine, "examples", d, "reader.md") for d in os.listdir(os.path.join(engine, "examples"))] \
        if os.path.isdir(os.path.join(engine, "examples")) else []
    if not rd or home == engine or home.startswith(engine + os.sep) \
            or os.path.exists(os.path.join(home, "tools", "paths.py")) \
            or any(os.path.isfile(e) and open(e, "rb").read() == open(rd, "rb").read() for e in examples):
        print(json.dumps({"home": home, "ok": False, "problem": "not an instance: run from the instance root, "
                          "without MORNING_FOLD_HOME pointing elsewhere (not an engine checkout or its example)"}, ensure_ascii=False, indent=1))
        sys.exit(2)
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    text = sys.stdin.read() if args[0] == "-" else open(args[0], encoding="utf-8").read()
    import graph
    toks = lambda t: tuple(re.findall(r"\w+", t.lower().translate(UMLAUTS)))   # whole words; any separator between them
    lines = []      # the engine's public files, one token line per file line, so a phrase never spans lines
    for d, sub, files in os.walk(paths.ENGINE):
        sub[:] = [x for x in sub if not x.startswith(".") and x != "model"]
        for f in files:
            if f.endswith((".md", ".py", ".sh", ".json", ".jsonl", ".html", ".css")):
                lines += [" ".join(toks(line)) for line in open(os.path.join(d, f), encoding="utf-8", errors="ignore")]
    words, public = {w for line in lines for w in line.split()}, " %s " % " \n ".join(lines)
    terms, skipped = {}, set()

    def add(t, where, always=False):
        bare = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode()
        if bare.strip() and toks(bare) != toks(t):
            add(bare, where, always)     # the same name with its accents dropped ("Muller" for "Müller")
        tk = toks(t or "")
        if not tk or tk in terms or tk in skipped:
            return
        if not always and (len(" ".join(tk)) < 3 or (tk[0] in words if len(tk) == 1 else " %s " % " ".join(tk) in public)):
            skipped.add(tk)
        else:
            terms[tk] = (t.strip(), where)

    r = paths.reader()
    for k in ("name", "place", "masthead", "artifact_url"):
        if r["own"].get(k) and r["own"][k] != paths.DEFAULTS[k]:
            add(str(r["own"][k]), "reader.md " + k, True)
    for w in paths.cfg("watch"):
        add(w, "reader.md watch", True)
    prose = paths.profile_text() + "\n" + r["text"]
    plain = set(re.findall(r"(?<![\w-])[a-zäöüß]\w*", prose))     # words the prose also writes in lower case are common words
    for run in re.findall(r"\w*[A-ZÄÖÜ]\w*(?:[ \-]+\w*[A-ZÄÖÜ]\w*)*", prose):
        for t in [run] + run.split():
            if t.lower() not in plain:
                add(t, "reader.md prose")
    for s in paths.sources_file()["sources"]:
        add(s.get("name"), "sources.json name")
        for k in ("url", "feed"):
            if s.get(k):
                add(re.sub(r"^www\.", "", re.sub(r"^\w+://([^/:]+).*", r"\1", s[k])), "sources.json host")
    nodes, _ = graph.load()
    for n in nodes.values():
        if n["type"] in ("Person", "Company", "Fund", "Regulator", "Source", "Event"):
            add(n["id"].split(":", 1)[-1].replace("-", " "), "graph id")    # ids are slugs: umlauts dropped, names shortened
            for nm in [n.get("name") or ""] + n.get("aliases", []):
                add(nm, "graph " + n["type"])
                short = SUFFIX.sub("", nm.lower()) if n["type"] in ("Company", "Fund") else \
                    (nm.split()[-1] if n["type"] == "Person" and len(nm.split()) > 1 else "")
                if len(short) >= (4 if n["type"] != "Person" else 5):
                    add(short, "graph " + n["type"])
    first = collections.defaultdict(list)
    for tk in terms:
        first[tk[0]].append(tk)
    text = re.sub(r"(?mi)^co-authored-by: claude[^<\n]*<noreply@anthropic\.com>\r?$", "", text)    # Claude's co-author trailer
    tt, hits = toks(text), collections.Counter()
    for i, w in enumerate(tt):
        hits.update(tk for tk in first.get(w, ()) if tt[i:i + len(tk)] == tk)
    res = {"home": home, "terms": len(terms), "not_checked": len(skipped),
           "hits": [{"term": terms[tk][0], "from": terms[tk][1], "count": c} for tk, c in hits.most_common()],
           "ok": not hits}
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if hits:
        sys.exit(1)


TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?")


def version(tag):
    """Sort key of a release tag vX.Y.Z or vX.Y.Z-rcN (a final release after its candidates); None for other tags."""
    m = TAG.fullmatch(tag or "")
    return m and (*map(int, m.group(1, 2, 3)), not m.group(4), int(m.group(4) or 0))


def git(*a):
    r = subprocess.run(["git", "-C", paths.ENGINE, *a], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def report(res, code=0):
    print(json.dumps(res, ensure_ascii=False, indent=1))
    sys.exit(code)


def self_test(prev, res):
    failures = []
    for c in (["instance.py", "check"], ["graph.py", "brief"], ["fetch.py", "--help"], ["site.py", "template", "issue"]):
        r = subprocess.run([sys.executable, os.path.join(paths.ENGINE, "tools", c[0]), *c[1:]], cwd=paths.HOME,
                           capture_output=True, text=True)
        if r.returncode:
            failures.append({"run": " ".join(c), "exit": r.returncode, "output": (r.stdout + r.stderr)[-1500:]})
    if failures:
        git("checkout", "-q", "--detach", prev)
        report({**res, "ok": False, "failures": failures, "rolled_back_to": git("describe", "--tags", "--always")}, 1)
    report({**res, "ok": True})


def cmd_update(args):
    if git("rev-parse", "HEAD") is None:
        report({"update": False, "reason": f"{paths.ENGINE} is not a git checkout (an unpacked archive?)"})
    if "--verify" in args:
        prev = git("rev-parse", "HEAD@{1}")
        if not prev:
            report({"ok": False, "reason": "no previous engine checkout to verify against"}, 1)
        self_test(prev, {"current": git("describe", "--tags", "--always", prev), "target": git("describe", "--tags", "--always")})
    channel = paths.cfg("engine_updates")
    if channel != "off" and git("fetch", "-q", "--tags", "--force", "origin") is None:
        report({"update": False, "reason": "could not fetch the engine's tags from origin"})
    head = git("rev-parse", "HEAD")
    current = max(git("tag", "--points-at", "HEAD").split(), key=lambda t: version(t) or (), default=None)
    if not version(current):
        current = git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*")
    res = {"current": current if version(current) else None, "target": None, "update": False}
    if channel == "off":
        report({**res, "reason": "engine_updates is off"})
    reverted = re.findall(r'(?m)^Revert "Engine \S+ → (v\S+)"', git("-C", paths.HOME, "log", "--format=%s") or "")
    tags = [t for t in git("tag", "--list", "v*").split() if version(t) and (channel == "rc" or version(t)[3])
            and t not in reverted]
    target = res["target"] = max(tags, key=version, default=None)
    cur = version(res["current"])
    if not target or (cur and version(target) <= cur) or git("rev-parse", target + "^{commit}") == head:
        report({**res, "reason": "up to date"})
    if git("merge-base", "--is-ancestor", "HEAD", target) is None:
        report({**res, "reason": "the engine is at a commit off the release line (a development commit?); nothing changed"})
    secs = [s.strip() for s in re.split(r"(?m)^(?=## )", git("show", target + ":CHANGELOG.md") or "")
            if (m := re.match(r"## v?(\d+)\.(\d+)\.(\d+)", s)) and (base := tuple(map(int, m.groups()))) <= version(target)[:3]
            and (not cur or (*base, True) > cur[:4])]
    steps = [m.group(1).strip() for s in secs if (m := re.search(r"(?ms)^### Instance changes\s*$(.*?)(?=^##|\Z)", s))]
    res.update(update=True, changelog="\n\n".join(secs), instance_changes=steps)
    if "--check" in args:
        report(res)
    if git("checkout", "-q", "--detach", target) is None:
        report({**res, "ok": False, "reason": f"could not check out {target} (local changes in the engine?)"}, 1)
    if steps:
        report({**res, "needs_migration": True, "next": "make the instance_changes, then: instance.py update --verify"})
    self_test(head, res)


def cmd_feedback(args):
    if len(args) != 1:
        print(__doc__, file=sys.stderr); sys.exit(2)
    text = sys.stdin.read() if args[0] == "-" else open(args[0], encoding="utf-8").read()
    gate = subprocess.run([sys.executable, os.path.abspath(__file__), "leakcheck", "-"], input=text, capture_output=True, text=True)
    if gate.returncode:
        print(gate.stdout + gate.stderr, file=sys.stderr)
        report({"sent": False, "reason": "leakcheck refused the text"}, 1)
    url = paths.cfg("feedback_relay")
    if re.match(r"https?://[^/]*\.invalid(/|$)", url or "https://x.invalid"):
        report({"sent": False, "reason": "no relay configured: feedback_relay is the placeholder"})
    idf = os.path.join(paths.HOME, "data", "instance_id")
    if not os.path.exists(idf):
        import uuid
        os.makedirs(os.path.dirname(idf), exist_ok=True)
        open(idf, "w").write(str(uuid.uuid4()) + "\n")
    title, _, body = text.strip().partition("\n")
    data = {"instance": open(idf).read().strip(), "engine": git("describe", "--tags", "--always") or "unknown",
            "title": title.strip(), "body": body.strip()}
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, json.dumps(data).encode(), {"content-type": "application/json",
                                                                     "user-agent": "morning-fold-feedback (+https://github.com/rahimi/morning-fold)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ans = json.loads(r.read())
        print({"created": "sent: a new issue", "commented": "sent: a comment on the open issue with that title",
               "duplicate": "not sent again: the relay already has this text"}.get(ans.get("action"), "sent"), file=sys.stderr)
        report(ans)
    except urllib.error.HTTPError as e:
        report({"sent": False, "status": e.code, "reason": e.read().decode(errors="replace")[:300]})
    except (OSError, ValueError) as e:
        report({"sent": False, "reason": str(e)[:300]})


def stage(n, p):
    """A conflicted file's text at merge stage n (1 base, 2 ours, 3 theirs), "" when that side has none."""
    return git("-C", paths.HOME, "show", f":{n}:{p}") or ""


def join_jsonl(p, kept):
    """Three-way join of a graph file by record id against the merge base. Changed on both sides: the later
    updated (else created) wins, a tie the side with the newer last non-merge commit. Changed on one side and
    deleted on the other: kept, its id added to `kept`."""
    b, o, t = ({json.loads(x)["id"]: x for x in stage(n, p).splitlines() if x.strip()} for n in (1, 2, 3))
    tips = [int(git("-C", paths.HOME, "log", "-1", "--no-merges", "--format=%ct", r) or 0) for r in ("HEAD", "MERGE_HEAD")]
    stamp = lambda v, k: (json.loads(v).get("updated") or json.loads(v).get("created") or "", tips[k])
    out = {}
    for i in set(o) | set(t):
        x, y = o.get(i), t.get(i)
        if x == y or y == b.get(i):
            r = x
        elif x == b.get(i):
            r = y
        elif x is None or y is None:
            r = x or y
            kept.append(i)
        else:
            r = y if stamp(y, 1) > stamp(x, 0) else x
        if r:
            out[i] = r
    return "".join(out[i] + "\n" for i in sorted(out))


def join_first_seen(p):
    """Union by url: "baseline" is the earliest first_seen; the latest last_seen; fetch.py's 60-day cut after."""
    o, t = (json.loads(stage(n, p) or "{}") for n in (2, 3))
    for u, v in t.items():
        w = o.get(u)
        o[u] = v if not w else [min(w[0], v[0], key=lambda d: (d != "baseline", d)), max(w[1], v[1]),
                                *(w if w[1] >= v[1] else v)[2:]]
    cut = str(datetime.date.today() - datetime.timedelta(days=60))
    return json.dumps({u: v for u, v in o.items() if v[1] >= cut}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def newer_engine(o, t):
    """Both sides moved the engine pointer: the descendant, else the higher release tag, else ours."""
    eg = lambda *a: git("-C", os.path.join(paths.HOME, "engine"), *a)
    tag = lambda c: max((eg("tag", "--points-at", c) or "").split() or [eg("describe", "--tags", "--abbrev=0", c) or ""],
                        key=lambda x: version(x) or ())
    return t if eg("merge-base", "--is-ancestor", o, t) is not None or (version(tag(t)) or ()) > (version(tag(o)) or ()) else o


def endpoints(ref, res):
    """Edges the merge left naming a node it removed (src/dst, source, also[].source): take that field from the side
    whose value still names existing nodes."""
    load = lambda text: {json.loads(x)["id"]: x for x in text.splitlines() if x.strip()}
    cur = lambda f: open(os.path.join(paths.HOME, "graph", f), encoding="utf-8").read()
    nodes, head_nodes = set(load(cur("nodes.jsonl"))), set(load(git("-C", paths.HOME, "show", "HEAD:graph/nodes.jsonl") or ""))
    sides = [{i: json.loads(x) for i, x in load(git("-C", paths.HOME, "show", f"{r}:graph/edges.jsonl") or "").items()}
             for r in ("HEAD", "MERGE_HEAD")]
    refs = lambda e, k: {a.get("source") for a in e.get("also", [])} - {None} if k == ("also",) else {e.get(x) for x in k} - {None}
    edges, fixed, lost = load(cur("edges.jsonl")), [], []
    for i, x in edges.items():
        e, h, new = json.loads(x), sides[0].get(i), None
        for k in (("src", "dst"), ("source",), ("also",)):
            if refs(e, k) <= nodes or (h and not refs(h, k) <= head_nodes):
                continue        # whole, or already dangling before this merge
            alt = next((s[i] for s in sides if i in s and refs(s[i], k) <= nodes), None)
            if alt:
                new = {**(new or e), **{f: alt[f] for f in k if f in alt}}
            else:
                lost.append(i)
        if new:
            edges[i] = json.dumps(new, ensure_ascii=False, sort_keys=True)
            fixed.append(i)
    if fixed:
        open(os.path.join(paths.HOME, "graph", "edges.jsonl"), "w", encoding="utf-8").write(
            "".join(edges[i] + "\n" for i in sorted(edges)))
        git("-C", paths.HOME, "add", "graph/edges.jsonl")
        res["conflicts"].append({"branch": ref, "repointed": fixed, "why": "a node it names was removed by the other side"})
    res.setdefault("dangling_endpoints", []).extend(lost) if lost else None


def merge(ref, res):
    """Merge one ref into HEAD: known conflicts are resolved, any other aborts this merge only. True when merged."""
    g = lambda *a: git("-C", paths.HOME, *a)
    b = ref.split("/", 1)[1]
    nxt = f"git merge {ref}, resolve, git commit; or drop it: git push origin --delete {b}"
    started = g("merge", "-q", "--no-commit", ref) is not None
    if g("rev-parse", "-q", "--verify", "MERGE_HEAD") is None:     # a fast-forward, or the merge never started
        return started or res["conflicts"].append({"branch": ref, "paths": ["(merge did not start)"], "next": nxt})
    try:
        todo, kept = (g("diff", "--name-only", "--diff-filter=U") or "").split(), []
        bad = [p for p in todo if not re.fullmatch(r"graph/[^/]+\.jsonl|data/first_seen\.json|engine|graph/vectors\.npz"
                                                   r"|(index|archive)/.+", p)]
        for p in [] if bad else todo:
            if p == "engine":
                o, t = (re.search(r"160000 (\w+) %s\t" % n, g("ls-files", "-u", "engine")).group(1) for n in "23")
                g("update-index", "--cacheinfo", f"160000,{newer_engine(o, t)},engine")
                continue
            if p.endswith(".jsonl") or p == "data/first_seen.json":
                open(os.path.join(paths.HOME, p), "w", encoding="utf-8").write(
                    join_jsonl(p, kept) if p.endswith(".jsonl") else join_first_seen(p))
            elif g("checkout", "--ours", "--", p) is None:
                g("checkout", "--theirs", "--", p)      # only their side has it
            elif p.startswith("archive/") and p.endswith(".gz"):     # the same url archived twice: this run can ground on both
                import gzip
                import graph
                os.makedirs(graph.TEXT_CACHE, exist_ok=True)
                texts = [gzip.decompress(subprocess.run(["git", "-C", paths.HOME, "show", f":{n}:{p}"], capture_output=True).stdout)
                         .decode("utf-8", "replace") for n in (2, 3)]
                open(os.path.join(graph.TEXT_CACHE, os.path.basename(p)[:-3]), "w", encoding="utf-8").write(
                    texts[0] if texts[0] == texts[1] else "\n\n".join(texts))
            res["vectors"] |= p == "graph/vectors.npz"
            g("add", "--", p)
        if not bad and os.path.exists(os.path.join(paths.HOME, "graph", "edges.jsonl")):
            endpoints(ref, res)
    except Exception as e:      # never leave a half-merge for the run's next `git add -A && git commit`
        bad = [f"(error: {type(e).__name__}: {e})"]
    if bad or g("commit", "-q", "--no-edit") is None:
        g("merge", "--abort")
        res["conflicts"].append({"branch": ref, "paths": bad or ["(commit failed)"], "next": nxt})
        return False
    if kept:
        res["conflicts"].append({"branch": ref, "kept": sorted(kept), "why": "changed on one side, deleted on the other"})
    return True


def cmd_sync(args):
    g = lambda *a: git("-C", paths.HOME, *a)
    res = {"merged": [], "skipped": [], "conflicts": [], "pushed": [], "pruned": [], "vectors": False}
    if g("remote", "get-url", "origin") is None:
        report({**res, "remote": None})
    main = (g("symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD") or "origin/main").split("/", 1)[1]
    cur = g("branch", "--show-current")
    branches = lambda: [r for r in (g("for-each-ref", "--format=%(refname:short)", "--sort=committerdate",
                                      "refs/remotes/origin/") or "").split() if r.startswith("origin/") and r != "origin/HEAD"]
    has = lambda ref: g("merge-base", "--is-ancestor", ref, "HEAD") is not None
    session = lambda b, digit="": re.fullmatch(r"(claude/[\w.-]+|%s)-%s[a-z0-9]{6}" % (re.escape(main), digit), b)
    if cur and cur != main and session(cur):     # a cloud session branch: work on main, so no branch is left behind
        if g("checkout", "-q", "-B", main) is not None:
            g("branch", "-q", "--set-upstream-to", "origin/" + main)
            res["switched"] = {"from": cur, "to": main}
            cur = main
            hook = os.path.join(paths.HOME, g("rev-parse", "--git-path", "hooks/pre-push") or ".git/hooks/pre-push")
            if not os.path.exists(hook) or "morning-fold" in open(hook, encoding="utf-8", errors="replace").read():
                # the session's own rules say "push to your branch" and outrank RUN.md: refuse it here, in this clone only
                os.makedirs(os.path.dirname(hook), exist_ok=True)
                open(hook, "w", encoding="utf-8").write(
                    "#!/bin/sh\n# morning-fold (instance.py sync): a cloud run pushes only %s\n"
                    "while read l ls r rs; do n=${r#refs/heads/}\n"
                    "  case $ls in *[!0]*) ;; *) continue;; esac   # a deletion (--prune) is fine\n"
                    "  if printf '%%s' \"$n\" | grep -Eq '^(claude/[A-Za-z0-9_.-]+|%s)-[a-z0-9]{6}$'; then\n"
                    "    echo \"Morning Fold: this run works on %s. Don't push the session branch $n:\" >&2\n"
                    "    echo \"run 'python3 engine/tools/instance.py sync --push' (it pushes %s).\" >&2; exit 1; fi\n"
                    "done\n" % (main, re.escape(main), main, main))
                os.chmod(hook, 0o755)
                res["switched"]["guard"] = "pre-push refuses session branches"

    def pull():
        if g("status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"):
            return res["conflicts"].append({"branch": None, "paths": ["(working tree has changes: commit first)"]})
        engine0 = g("rev-parse", "HEAD:engine")
        if g("rev-parse", "--is-shallow-repository") == "true":
            res["unshallowed"] = g("fetch", "-q", "--unshallow", "origin") is not None
        if g("fetch", "-q", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*") is None:
            res["conflicts"].append({"branch": None, "paths": ["(fetch failed)"]})
        import fcntl
        import graph
        with open(os.path.join(graph.GDIR, ".lock"), "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)          # no reporter writes the graph while it is merged
            for ref in sorted((r for r in branches() if not has(r)), key=lambda r: r != "origin/" + main):   # main first
                base = g("merge-base", "HEAD", ref)
                why = None if ref == "origin/" + main else "not named like a session branch" \
                    if not session(ref.split("/", 1)[1]) else None if base and \
                    g("diff", "--name-only", base, ref, "--", "runs/", "graph/") else "no runs/ or graph/ changes"
                if why:
                    res["skipped"].append({"branch": ref, "why": why})     # never merged: the reader's own
                elif merge(ref, res):
                    res["merged"].append(ref)
            if res["vectors"] is True:              # ours were kept: embed what the other side added or changed
                try:
                    graph.sync_vecs(*graph.load())
                    res["vectors"] = "updated"
                except Exception as e:
                    res["vectors"] = f"not updated ({e}): graph.py reindex"
                g("add", "graph/vectors.npz")
                g("commit", "-q", "-m", "Sync: vectors of the merged records")
        if g("rev-parse", "HEAD:engine") != engine0:
            res["engine_changed"] = True
            if os.path.exists(os.path.join(paths.HOME, "engine", ".git")):
                g("submodule", "update", "-q", "--init", "engine")   # else the next `git add -A` puts the old pointer back

    try:
        pull()
        if "--push" in args:
            push = lambda b: g("push", "-q", "origin", "HEAD:refs/heads/" + b) is not None and not res["pushed"].append(b)
            if not push(main) and not (pull() or push(main)):     # main moved meanwhile: sync again, retry once
                res["conflicts"].append({"branch": main, "paths": ["(push rejected)"]})
            if cur and cur != main:
                push(cur)
        if "--prune" in args and main in res["pushed"]:      # the platform's session branches that main now holds
            for ref in branches():
                b = ref.split("/", 1)[1]
                if b != cur and session(b, r"(?=[a-z]*\d)") and has(ref):
                    r = subprocess.run(["git", "-C", paths.HOME, "push", "-q", "origin", "--delete", b],
                                       capture_output=True, text=True)
                    if r.returncode:      # a remote that refuses deletions refuses them all: say why, stop trying
                        res["prune_failed"] = {"branch": b, "error": (r.stderr.strip().splitlines() or ["?"])[-1][:300]}
                        break
                    res["pruned"].append(b)
    except Exception as e:      # never exit non-zero, never leave a half-merge behind
        if g("rev-parse", "-q", "--verify", "MERGE_HEAD") is not None:
            g("merge", "--abort")
        res["error"] = f"{type(e).__name__}: {e}"
    report(res)


if __name__ == "__main__":
    cmds = {"init": cmd_init, "check": cmd_check, "leakcheck": cmd_leakcheck, "update": cmd_update, "sync": cmd_sync, "feedback": cmd_feedback}
    c, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if c in ("", "-h", "--help") or {"-h", "--help"} & set(args):
        print(__doc__); sys.exit(0)
    bad = [a for a in args if a.startswith("-") and a != "-" and not (c == "update" and a in ("--check", "--verify")) \
           and not (c == "sync" and a in ("--push", "--prune"))]
    if c not in cmds or bad:     # nothing runs on a typo: `init --help` would lay out an instance in ./--help
        print(f"{__doc__}\nunknown {'flag ' + bad[0] if c in cmds else 'command ' + c}", file=sys.stderr); sys.exit(2)
    cmds[c](args)
