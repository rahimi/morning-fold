# The Morning Fold: backfill

A one-off, long-running job that gives a new paper its history, so the first editions can already say "From the memory". It runs on the reader's own computer (local transcription is the slow part; an Apple Silicon GPU or a decent CPU helps), in a Claude Code session in a clone of the instance, with the engine submodule checked out. Commands run from the instance root. The daily runs keep working meanwhile.

## Principles
- **Archive everything, extract selectively.** Every transcript and article text goes into `archive/` and the passage index (`engine/tools/passages.py`), which makes all of it searchable by meaning. Only what the beats and theses need becomes graph facts. Reading costs subscription quota; transcription and indexing don't.
- **History is never news.** Every graph write in the backfill runs with `FOLD_BACKFILL=1` (prefix each `graph.py upsert/merge/invalidate` command with it). Those writes are marked and stay out of `brief` and `changes`, so the next edition doesn't report a year of history as new. Facts carry their real dates (`valid_from`, the article's `published`). Never create `Signal`, `Issue` or `Story` nodes in the backfill.
- **The rules in `engine/RUN.md` apply**: no quote, no fact; search before creating entities; people-move conventions; podcast trust rules (`SAID`, confidence caps, names from show notes, numbers "as said").
- **Theses move once.** After extraction, recalibrate each open thesis in one step: a single `props.history` entry {date: today, probability, why: "backfill: <the two or three facts that matter>"}. No retroactive history.
- **Never push inside `reader.md`'s `quiet_window`** (the scheduled run's window, in its `timezone`). `backfill.py` waits by itself; for your own commits, check the time. Commit and push in small chunks with `git pull --rebase --autostash` first.
- **Keep the computer awake** for long steps (on macOS: run them under `caffeinate -is`).

## How far back
Write the table for this reader before you start, one row per beat in `reader.md` (plus watched subjects), and show it to them. A typical shape for a reader who follows companies; a reader of science or policy gets rows like "papers and preprints on X, 12 months" or "bills and consultations, current session":

| Beat | Horizon | How |
|---|---|---|
| Watched companies (from `reader.md`) | since the event that made them worth watching (find the date first) | their press archives, company-register history (officers, owners), trade-press searches |
| Deals on the reader's beats | 2 to 3 years | buyers' and sellers' press releases, advisers' deal reports, trade-press searches |
| People moves at watched firms | 2 to 3 years | searches per firm and per person in the graph; register changes |
| Each high-priority beat | 12 months | what is in production, who the vendors are, the regulator's milestones |
| Trade podcasts | 12 months, all episodes transcribed | extract from episodes on the beats |
| The reader's own shows (heard) | 3 months, all episodes transcribed | archive only; extract an episode only when it touches a beat or thesis |
| Regulation | current state and upcoming dates | Event nodes for deadlines ahead |

## Steps
0. **Prepare.** `git pull && git submodule update --init engine`. `python3 -m venv .venv && . .venv/bin/activate && pip install -r engine/requirements.txt` (on Apple Silicon also `mlx-whisper`). `python3 engine/tools/get_model.py` if the model is missing. Record the chip and memory. Whisper model: `large-v3-turbo` by default; with plenty of memory you may try a larger one on a few episodes and keep it only if it is clearly better on names and the reader's language.
1. **Test (about 30 minutes).** `python3 engine/tools/backfill.py podcasts --since <12 months ago> --shows "<one or two trade shows>" --limit 4`. Report: minutes per hour of audio, transcript kinds, a sample paragraph. Then one news question from the table end to end with a reporter. Stop and report to the reader before the long run.
2. **Podcasts.** `python3 engine/tools/backfill.py podcasts --since <12 months ago> --group trade`, then `--group heard --since <3 months ago>`. Resumable; check progress with `backfill.py status`.
3. **Article history** (optional): `python3 engine/tools/backfill.py articles-list --since <date> [--sources id,id]` lists each source's past articles (sitemaps, paged feeds, listing pages) into `backfill/articles/`; pick the ones on the beats and read them with reporters.
4. **Index.** `python3 engine/tools/passages.py index`, commit `index/` and push.
5. **News, question by question.** Work through the table, beat by beat, with WebSearch, `fetch.py article` (archives the text) and Sonnet reporters (the RUN.md reporter brief plus: "prefix graph writes with FOLD_BACKFILL=1; this is history, use the article's real dates"). Write a short list of questions per beat first; stop a beat when new searches only repeat what the graph has.
6. **Podcast extraction.** For trade episodes in `backfill/podcasts.jsonl`, pick the ones on the beats (titles, show notes, `passages.py search` per beat) and hand them to reporters with their archived text (`graph.py` checks quotes against it). Heard episodes only when they touch a beat or thesis.
7. **Recalibrate the theses** (see Principles), then `graph.py audit`: merge duplicates, close stale roles, fix conflicts.
8. **Finish.** `passages.py index` again, commit, push, and write `runs/<date>-backfill.json` (episodes and hours by show, articles read per beat, facts added, theses moved and why, problems). Tell the reader in a few lines what the paper now knows that it didn't.

Pace: reading is the scarce resource. Prefer fewer, better sources per question; stop a beat early rather than read noise. If the session's usage gets high, finish the step in progress, commit, and say where to resume.
