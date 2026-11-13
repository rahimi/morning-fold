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
"""
import datetime
import json
import os
import re
import sys

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
    seen = set()
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
            warnings.append(f"{tag}: kind {s['kind']!r} is not one of {sorted(KINDS)}")
        if s.get("kind") == "podcast" and not s.get("feed"):
            problems.append(f"{tag}: a podcast needs its feed URL (fetch.py podcast-find)")
        if s.get("kind") == "podcast" and s.get("group") not in ("trade", "heard"):
            warnings.append(f"{tag}: podcast group should be 'trade' (read in full) or 'heard' (the reader's own shows)")
        if s.get("status") and s["status"] not in STATUSES:
            warnings.append(f"{tag}: status {s['status']!r} unknown")

    theses = paths.load_json("theses.json", [])
    for k, t in enumerate(theses):
        tag = f"theses.json[{k}] {t.get('name')!r}"
        if not t.get("name") or not t.get("claim"):
            problems.append(f"{tag}: needs name and claim (the claim states exactly how it resolves)")
        p = t.get("probability")
        if not isinstance(p, (int, float)) or not 0 <= p <= 1:
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


if __name__ == "__main__":
    cmds = {"init": cmd_init, "check": cmd_check}
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    if c not in cmds:
        sys.exit(__doc__)
    cmds[c](sys.argv[2:])
