# Example instance (fictional)

A complete instance for a made-up reader: Mara, an independent analyst of underwriting and claims technology
in a made-up city. Every person, company, regulator, article and issue here is fictional; only the feeds in
`sources.json` are real public feeds. Use it as the format reference when setting up a real instance, and to
try the tools:

```bash
export MORNING_FOLD_HOME=examples/instance        # from the engine root
python3 tools/instance.py check
python3 tools/graph.py brief
python3 tools/graph.py dossier "Halcyon Mutual"
python3 tools/site.py template issue --out /tmp/issue.html   # theme.css applied, masthead filled
```

| File | Role |
|---|---|
| `reader.md` | profile, beats with priorities, watch list, voice; settings in the front matter |
| `sources.json` | ranking queries and the source list |
| `theses.json` | seed theses (named forecasts) |
| `site/theme.css` | colour override for the templates |
| `graph/nodes.jsonl`, `graph/edges.jsonl` | a small graph: sources and theses (from `graph.py seed`), insurers, an insurtech, a regulator and its consultation (Regulator, Policy), a person, two articles, facts with grounded quotes, events, a rule |
| `archive/` | the fictional articles' texts, against which the quotes are checked |
| `issues/2026-03-03.json`, `runs/2026-03-03-issue.json` | what an issue run leaves behind |
| `CLAUDE.md`, `.gitignore` | as `tools/instance.py init` writes them |

A real instance also has `engine/` (this repository as a submodule) and, once the paper runs, `index/`,
`data/first_seen.json` filling up, and `graph/vectors.npz` (an embedding cache, rebuilt by `graph.py reindex`).
