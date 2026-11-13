# The Morning Fold

A personal newspaper that Claude writes overnight for one reader.

Every morning it reads that reader's sources, sends reporter sub-agents through the day's articles and podcast transcripts, files what they find into a knowledge graph where every fact carries a verbatim quote, scores the candidate stories against a fixed bar, and publishes an issue as a claude.ai artifact: a small book you swipe through on a phone, two pages side by side on a wide screen. The back page has a reply box. Whatever the reader types there is read first by the next run, and changes what the paper does.

This repository is the **engine**: the tools, the method, the templates. Your own paper (your profile, your sources, everything it learns) lives in your own private repository, with this engine inside it as a submodule.

## Quickstart

Point Claude Code at this repository and say:

> Set up my Morning Fold.

Claude follows [`SETUP.md`](SETUP.md): it interviews you (your beats, the companies and people to watch, your sources, language and schedule), creates your private instance repository, finds and tests sources, shows you the design to adjust, publishes a first issue, and schedules the daily run in the cloud. It does what it can itself and walks you through the few clicks only you can make.

You need: a Claude plan with Claude Code cloud sessions (Pro, Max, Team or Enterprise), a GitHub account, and about an hour for setup. Best with the Claude app on your phone and its notifications allowed: each morning's edition then arrives as a notification.

## How it works

```
 sources.json ──► fetch.py sources ──► ranked items ──► reporters (Sonnet sub-agents)
 (feeds, pages,    read in parallel,     relevance to         read each article, write facts
  newsrooms,       fold copies, drop     reader.md's profile,  with verbatim quotes
  podcasts)        backlog (first_seen)  queries, theses        │
                                                                ▼
                     ┌────────────────── knowledge graph (graph.py) ◄── quote must be in the
                     │                    nodes + edges, every fact       archived article text
                     │                    sourced, nothing deleted,       ("no quote, no fact")
                     │                    superseded facts invalidated
                     ▼
   candidates: today's news + patterns no single article shows
                     │
                     ▼  score impact / novelty / origins / depth (0–3 each);
                        search for a second origin before scoring origins;
                        story = 8+ of 12 and 2+ independent origins
                     │
                     ▼
   dossier per story ─► write (From the memory, My read) ─► desk check + rival read
                     │
                     ▼
   site.py template ─► check_issue.py ─► artifact on claude.ai ─► reader replies
                                                                      │
   next run reads the replies first: rules, profile changes, theses ◄─┘
```

- **Weekdays:** the issue run. **Saturday:** the dreaming run, which consolidates the memory, verifies weak facts, merges duplicates and learns rules from the week's run logs. **Sunday:** a week-ahead planner with the full theses board. All configurable per reader.
- **Theses** are named forecasts with a claim that says how it resolves, a date and a probability. They move with evidence and are scored with the Brier score when they resolve; early signals the paper publishes first are logged and checked against later reporting.
- **Memory**: a plain-text knowledge graph (`graph/nodes.jsonl`, `graph/edges.jsonl`) with a local embedding model for search and duplicate detection; every article and transcript archived and searchable by meaning (`passages.py`).

## The honesty rules

- **No quote, no fact.** A fact enters the graph only with a short verbatim quote, and the tool refuses it when the quote isn't in the archived source text.
- **Origins, not URLs.** A press release and its rewrites count once. A story needs independent origins for its headline claim, and the editor searches for a second one before scoring.
- **From the memory.** Each story carries at least one dated claim the paper knew before today.
- **Contradictions are kept,** facts are invalidated rather than deleted, so the paper can say what it knew on any day.
- **Podcasts are claims, not facts,** with capped confidence, and never count as an origin.
- **Forecasts are scored honestly;** probability history is never edited after the fact.
- **The issue check** refuses to publish a page with template leftovers, broken structure or the wrong date.
- **Never invent** facts, numbers or quotes.

## Repository

| Path | What |
|---|---|
| `SETUP.md` | how Claude sets up a new reader's instance |
| `RUN.md` | the run method every scheduled run follows (issue, week-ahead, dreaming) |
| `BACKFILL.md` | one-off job that gives a new paper its history |
| `tools/` | `graph.py` (memory), `fetch.py` (reading, ranking, transcripts), `passages.py` (archive search), `backfill.py`, `check_issue.py`, `site.py`, `sync_reply.py`, `instance.py`, `get_model.py`, `paths.py` |
| `site/` | default issue and week-ahead templates, reply box |
| `examples/instance/` | a complete fictional instance (insurance and AI reader) |
| `model/MODEL.json` | provenance and checksums of the embedding model |

Requirements: Python 3.10+, `pip install -r requirements.txt` (for the scheduled cloud run: `tools/env_setup.sh`, the environment's setup script; SETUP.md step 8), Claude Code with the Artifact tools. The embedding model is downloaded on setup, not stored here: `python3 tools/get_model.py` fetches [multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small) (int8 ONNX conversion by [Xenova](https://huggingface.co/Xenova/multilingual-e5-small), pinned revision, checksums verified), Licence: **MIT** (base model intfloat/multilingual-e5-small); the Xenova ONNX conversion declares no separate licence.

Try the tools on the fictional instance:

```bash
python3 tools/get_model.py
MORNING_FOLD_HOME=examples/instance python3 tools/graph.py brief
MORNING_FOLD_HOME=examples/instance python3 tools/instance.py check
```

## Licence

MIT, see [LICENSE](LICENSE).
