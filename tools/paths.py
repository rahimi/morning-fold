"""Where things live, and the reader's settings.

Two roots:
  ENGINE  this checkout: the tools, the default site templates, the model folder.
  HOME    the reader's instance: reader.md, sources.json, theses.json and all content
          (graph/, archive/, issues/, runs/, index/, backfill/, data/, site/ overrides).
          Found as: $MORNING_FOLD_HOME, else the nearest parent of the working directory that
          holds a reader.md, else the working directory.

reader.md starts with a front matter block of simple `key: value` lines between two `---` lines.
A value that looks like JSON (a list in brackets, a number, true/false, a quoted string) is read as
JSON; anything else is a plain string. Everything after the block is prose for the editor.
"""
import json
import os

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_home():
    env = os.environ.get("MORNING_FOLD_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    d = os.getcwd()
    while True:
        if os.path.isfile(os.path.join(d, "reader.md")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.getcwd()
        d = parent


HOME = find_home()


def find_work():
    """Scratch space for one run (fetched texts for the quote check, items.json, transcripts, figures):
    $MORNING_FOLD_WORK, else <HOME>/.work. The folder ignores itself in git (it holds a '*' .gitignore)."""
    return os.path.abspath(os.path.expanduser(os.environ.get("MORNING_FOLD_WORK") or os.path.join(HOME, ".work")))


WORK = find_work()


def work_dir(*sub):
    """A folder under the work dir, created on demand; the work dir gets its self-ignoring .gitignore."""
    os.makedirs(WORK, exist_ok=True)
    gi = os.path.join(WORK, ".gitignore")
    if not os.path.exists(gi):
        try:
            open(gi, "w").write("*\n")
        except OSError:
            pass
    p = os.path.join(WORK, *sub)
    os.makedirs(p, exist_ok=True)
    return p

# Every knob the engine reads from reader.md, with its default. RUN.md refers to these by name.
DEFAULTS = {
    "name": "the reader",            # how the paper addresses its reader
    "masthead": "The Morning Fold",  # the paper's name on the cover and the tab
    "place": "",                     # dateline place on the cover, e.g. a city
    "language": "en",                # language the paper is written in
    "locale": "en-US",               # browser locale and Accept-Language when reading sources
    "timezone": "UTC",               # IANA time zone the schedule is in
    "issue_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
    "dreaming_day": "Sat",           # memory consolidation, no edition ("" to skip)
    "week_ahead_day": "Sun",         # week-ahead planner ("" to skip)
    "publish_by": "06:55",           # the issue should be published by this local time
    "quiet_window": "05:00-07:30",   # local window the backfill never pushes in (the morning run)
    "artifact_url": "",              # the claude.ai artifact every edition is published to
    "profile": "",                   # one paragraph used to rank items (default: first paragraph of the body)
    "watch": [],                     # names that always get a ranking boost and early-signal checks
    "regions": [],                   # places the reader cares about: items naming one get region_boost
    "other_regions": [],             # places that mark an item as elsewhere: items naming one (and no region) lose region_boost
    "region_boost": 0.04,
    "fact_types": [],                # what counts as a fact for this reader (the reporters' extraction list); [] = the default list
    "primary_counts_as": 2,          # origins a primary document (the company's release, the act, the regulator's decision) counts for its own claim
    "story_bar": 8,                  # a candidate needs at least this many of 12 points to be a story
    "min_origins": 2,                # ... and at least this many independent origins for its headline claim
    "corroboration_trigger": 7,      # impact+novelty+depth at or above this: search for a second origin first
    "max_stories": 6,
    "min_stories": 2,
    "max_in_brief": 8,
    "max_pages": 10,
    "max_item_age_days": 21,         # unread dated items older than this are history, not news
    "recency_grace_days": 2,         # dated items keep their full score this many days ...
    "recency_decay": 0.01,           # ... then lose this much score per day
    "name_boost": 0.08,              # ranking boost when an item names a known or watched entity
    "podcast_budget_weekday": 90,    # minutes of audio transcribed locally per weekday run
    "podcast_budget_weekend": 240,
}

_cache = {}


def parse_front_matter(text):
    meta, body = {}, text
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for k in range(1, len(lines)):
            if lines[k].strip() == "---":
                for line in lines[1:k]:
                    s = line.strip()
                    if not s or s.startswith("#") or ":" not in s:
                        continue
                    key, val = s.split(":", 1)
                    val = val.strip()
                    try:
                        meta[key.strip()] = json.loads(val) if val else ""
                    except ValueError:
                        meta[key.strip()] = val
                body = "\n".join(lines[k + 1:]).strip()
                break
    return meta, body


def reader():
    """{"meta": front matter merged over DEFAULTS, "text": the prose, "path": reader.md or None}."""
    if "reader" not in _cache:
        p = os.path.join(HOME, "reader.md")
        meta, body = ({}, "")
        if os.path.exists(p):
            meta, body = parse_front_matter(open(p, encoding="utf-8").read())
        _cache["reader"] = {"meta": {**DEFAULTS, **meta}, "own": meta, "text": body,
                            "path": p if os.path.exists(p) else None}
    return _cache["reader"]


def cfg(key):
    return reader()["meta"].get(key, DEFAULTS.get(key))


def profile_text():
    """The paragraph ranking uses to describe the reader: front matter `profile`, else the body's first paragraph."""
    r = reader()
    if r["meta"].get("profile"):
        return r["meta"]["profile"]
    for para in r["text"].split("\n\n"):
        p = para.strip()
        if p and not p.startswith("#"):
            return p
    return ""


def load_json(name, default):
    p = os.path.join(HOME, name)
    if not os.path.exists(p):
        return default
    return json.load(open(p, encoding="utf-8"))


def sources_file():
    """sources.json: {"queries": [...], "sources": [...]} (a bare list is read as sources)."""
    d = load_json("sources.json", {})
    if isinstance(d, list):
        d = {"sources": d}
    return {"queries": d.get("queries", []), "sources": d.get("sources", [])}


def site_file(name):
    """An instance's site/<name> overrides the engine's, file by file."""
    own = os.path.join(HOME, "site", name)
    return own if os.path.exists(own) else os.path.join(ENGINE, "site", name)


def accept_language():
    loc = cfg("locale") or "en-US"
    lang = loc.split("-")[0]
    parts = [loc, f"{lang};q=0.9"]
    if lang != "en":
        parts += ["en-US;q=0.8", "en;q=0.7"]
    return ",".join(parts)


MODEL_HINT = "from the instance root, run: python3 engine/tools/get_model.py --dest model --split"


def model_dir():
    """The embedding model: $MORNING_FOLD_MODEL, else the instance's model/ when it holds the weights, else the engine's."""
    env = os.environ.get("MORNING_FOLD_MODEL")
    if env:
        return env
    own = os.path.join(HOME, "model")
    if os.path.isdir(own) and any(f.startswith("e5-small-int8.onnx") for f in os.listdir(own)):
        return own
    return os.path.join(ENGINE, "model")
