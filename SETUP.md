# Setting up a reader's Morning Fold

Instructions for Claude. A user pointed you at this engine and wants their own Morning Fold. Work through the steps in order, in their session, and tell them what you are doing at each step. Everything personal you learn goes into **their private instance**, never into this engine.

```
their-fold/                  PRIVATE repo, one per reader
  reader.md                  who they are: profile, beats, watch list, language, schedule, story bar
  sources.json               where the paper reads (feeds, pages, newsrooms, podcasts, calendars) + ranking queries
  theses.json                the forecasts the paper starts with
  site/                      optional overrides: theme.css, or whole templates
  graph/ archive/ index/ issues/ runs/ data/ backfill/ model/    everything the paper learns and writes
  CLAUDE.md                  "this is an instance: follow engine/RUN.md"
  engine/                    THIS repo, as a git submodule (public, read-only from here)
```

Why a submodule: a private repo cannot be a fork of a public one, and with the engine as a submodule a `git add -A` in the instance can never push content into the public engine.

## 0. Check the ground
- Is there an instance already? If the working directory (or a parent) has a `reader.md`, you are in one: do not set up again; follow `RUN.md`, or change what the user asks in `reader.md` / `sources.json` / `theses.json`.
- Tools: `git`, `python3` (3.10+), and `gh` logged in (`gh auth status`). Without `gh`, ask the user to create an empty private repository on their git host and give you its URL.
- Note the engine's public **https** URL for the submodule: start from `git -C <this engine checkout> remote get-url origin`; turn an ssh form (`git@host:owner/repo.git`) into `https://host/owner/repo.git`, and check that `git ls-remote <url>` works without credentials (the scheduled cloud run clones it anonymously). A local path is not usable. If the checkout has no `origin` remote (a downloaded archive, a copy), ask the user for the engine's public repository URL; do not use a local path.
- Install the dependencies for this session: `python3 -m pip install -r <engine>/requirements.txt` (in a venv if the system Python refuses). The model comes in step 2a, inside the instance.
- The headless browser, for sources that block plain requests: `python3 -m pip install playwright && python3 -m playwright install chromium`. If the environment keeps browsers elsewhere, set `PLAYWRIGHT_BROWSERS_PATH` before both the install and the runs. Without it, such sources fail and fall back to WebSearch.

## 1. Interview the user
Three short rounds, one message each. Propose a default for every question, so "yes" or "default" is a full answer. Confirm a summary of all three rounds before writing anything.

**Round 1: who and what**
1. Your name (as the paper should address you), the city for the dateline, and the regions or countries your news is about.
2. What do you do, and what is the paper for? (Role, team, what you want to be better at or known for.)
3. Your beats, most important first: three to six topics. For each, what counts (examples of news you'd want) and what doesn't.
4. Which companies, organisations and people should it watch closely? Any you have a personal stake in (employer, investments)? The paper treats those strictly from public sources.
5. What counts as a fact for you? For example: people moves, deals, launches, results; or bills, rulings, consultations, approvals, projects; or studies and datasets. (Becomes `fact_types` and the "Facts that count" section of `reader.md`; the reporters extract exactly that.)
6. Anything it must never cover or never say?

**Round 2: the paper**
7. **Language**: which language should the paper be written in (propose the one the user writes to you in), and which languages do your sources write in? Sets `language` and `locale`. Quotes always stay in their original language.
8. **Schedule**: your time zone (propose the one this computer is set to), which days you want an issue (default Monday to Friday), and by what time it should be ready (default 06:55). The Sunday week-ahead and the Saturday memory pass (default: both)? Work out the start time (step 8) and say it back in their time zone.
9. Name of the paper (default "The Morning Fold"), tone (plain, dry, opinionated?), and should "My read" give advice or stay analysis?
10. **Look**: tell them the paper starts in the default design and that you will show it in step 3a before anything is published. Ask now only whether they already have a colour or font in mind.

**Round 3: sources and forecasts**
11. Sources you already trust or read: sites, newsletters with a web archive, regulators, company newsrooms, registers or databases.
12. **Podcasts**, as their own question: which professional podcasts in your field are must-hears (group "trade": relevant episodes read in full), and which shows do you listen to anyway, on any topic (group "heard": only episodes touching your beats, for the "You heard it" column)?
13. **Recommendations**: do you want me to search for feeds, sites and podcasts you haven't named? If yes, step 4 starts with that search and you pick from a shortlist.
14. Two or three things you expect to happen in your field in the next 6 to 18 months (these become the first theses: named forecasts with a date and a probability).

## 2. Create the private instance
The instance is a **sibling** of the engine checkout, never inside it: `cd` to the engine's parent folder (or wherever the user keeps code) first.
```bash
gh repo create <their-name>-fold --private --clone      # ALWAYS --private
cd <their-name>-fold
git checkout -b main 2>/dev/null || git branch -M main   # runs push to main
git submodule add <engine https url> engine
python3 engine/tools/instance.py init .
```

### 2a. The model, before any tool runs
The submodule has no model (weights are never in the engine repository). From the instance root:
```bash
python3 engine/tools/get_model.py --dest model --split
```
It downloads multilingual-e5-small (int8 ONNX, about 135 MB; licence MIT for the base model), verifies the checksums and writes it in parts under 100 MB, which you commit with the instance so scheduled cloud runs don't download it each morning. Every graph and ranking command needs it; their error message, and `instance.py check`, say exactly this command when it is missing.
Tell the user plainly: **this repository must stay private.** It will hold their profile, their interests, their employer and investments, and every article the paper reads. Never make it public, never add it to a public organisation, never push it to a public remote. (`gh repo view --json visibility` should say PRIVATE; check it.)

## 3. Write the instance files
Use `engine/examples/instance/` (a fictional reader) as the **format** reference only. Its sources, queries, theses, people and companies belong to that fictional reader (the feeds are real, but chosen for her): never copy any of them. Every source comes from the user's answers or from your search in step 4; `instance.py check` warns about leftovers from the example.
- **`reader.md`**: front matter with the settings below, then the prose: a first paragraph that says who the reader is and what the paper is for (the ranking embeds it, so make it dense and specific), `## Beats` as a numbered list with priority (high / medium / low / wildcard) and what belongs to each, `Skip:` what never to print, `## Watched` organisations, people, projects or policies (mark personal stakes and say "public sources only"), `## Facts that count`, `## Voice`.
- **`sources.json`**: `{"queries": [...], "sources": [...]}`. `queries`: one short ranking text per beat (keywords in the languages the sources write in). Each source: `id` ("source:<slug>"), `name`, `kind` (feed | page | newsroom | podcast | calendar | register | web), `url` (the site or listing page), `feed` (RSS/Atom when there is one), `lang`, `cadence` (daily | weekly | monthly), `status` (active | candidate), and for podcasts `group` ("trade": read in full; "heard": the reader's own shows, checked for claims).
- **`theses.json`**: a list of `{name, claim, probability, resolves, why}`. The claim says exactly what makes it true and by when; the name is short and readable. Probabilities are honest starting points, not cheerleading.
- **`site/theme.css`** (optional): written with the user in step 3a.

### reader.md settings (front matter)
| Key | Meaning | Default |
|---|---|---|
| `name`, `masthead`, `place` | how the paper addresses them; its name; the dateline place | "the reader", "The Morning Fold", "" |
| `language`, `locale` | language the paper is written in; locale for reading sources (Accept-Language, browser) | en, en-US |
| `timezone` | IANA zone the schedule is in | UTC |
| `issue_days`, `dreaming_day`, `week_ahead_day` | which run happens on which weekday (`""` skips one) | Mon–Fri, Sat, Sun |
| `publish_by`, `quiet_window` | issue ready by; window the backfill never pushes in | 06:55, 05:00-07:30 |
| `artifact_url` | the claude.ai artifact every edition is published to (set after step 7) | "" |
| `watch` | names that always get a ranking boost and early-signal checks | [] |
| `regions`, `other_regions`, `region_boost` | items naming one of `regions` rank a little higher; items naming only `other_regions` a little lower | [], [], 0.04 |
| `fact_types` | what counts as a fact for this reader (the reporters' extraction list) | [] = default list in RUN.md |
| `primary_counts_as` | how many origins a primary document counts for its own claim | 2 |
| `profile` | override for the ranking paragraph | first paragraph of the body |
| `story_bar`, `min_origins`, `corroboration_trigger` | story bar: points of 12, independent origins, when to search for a second origin | 8, 2, 7 |
| `min_stories`, `max_stories`, `max_in_brief`, `max_pages` | issue size | 2, 6, 8, 10 |
| `max_item_age_days`, `recency_grace_days`, `recency_decay`, `name_boost` | ranking | 21, 2, 0.01, 0.08 |
| `podcast_budget_weekday`, `podcast_budget_weekend` | minutes of audio transcribed locally per run | 90, 240 |

Values are `key: value` lines; lists and quoted strings are JSON (`issue_days: ["Mon", "Wed", "Fri"]`). `python3 engine/tools/instance.py check` validates all of it. The tools read `locale`, `timezone`, `quiet_window`, `profile`, `watch`, `regions`, the ranking keys, `podcast_budget_weekday` and the masthead/place/language (templates); the rest (story bar, issue size, schedule, `name`) is read by the editor from `graph.py brief`. Set `quiet_window` to the scheduled run's start time through `publish_by` plus 30 minutes (step 8).

### 3a. Look and feel
Show the user the default design before anything is published, and adapt it with them.
1. `python3 engine/tools/site.py template issue --out .work/preview.html` renders the edition template with its fictional sample pages, their masthead and place, and the current `site/theme.css`. Open it for them (`open` on macOS, `xdg-open` on Linux), or send them the file in a remote session.
2. Propose the default as it is, and say what can change: accent colour, paper and page colours, ink, and the display, body and mono fonts (any Google Font). The page has a light and a dark version; show both.
3. For changes, write only the changed tokens to `site/theme.css` (token names: the template's `:root`; format: the example's `site/theme.css`, including its two dark-mode blocks; a font needs an `@import` of the web font at the top). Re-render and show again until they say it's right. The default with no theme.css is a fine answer.
4. Changes to structure (sections, layout) are "Overriding templates" below; offer them only if the user asks.

## 4. Find and test sources
**Recommendations first**, if the user asked for them (round 3): per beat, search with WebSearch for trade press, regulators' and associations' news pages, newsrooms of watched companies, research publishers and event calendars; for podcasts, `python3 engine/tools/fetch.py podcast-find "<topic>"` plus WebSearch. Present a shortlist grouped by beat: name, what it covers, how often it publishes, one recent headline or episode as evidence, feed yes/no. Only what the user picks goes in. Then add the sources they named themselves.

Aim for 4 to 10 article sources per beat that publish concrete facts, and a few podcasts.

**Podcasts** need their own care:
- Each needs its feed URL (`fetch.py podcast-find "<show>"`) and a `group`: "trade" or "heard" (round 3). Check the feed with `fetch.py check <feed>`: valid, with recent episodes.
- Episodes without a publisher transcript are transcribed locally with Whisper, about 13 minutes per hour of audio on 4 CPUs, inside the run. `podcast_budget_weekday` / `podcast_budget_weekend` (default 90 / 240 minutes of audio) cap it; agree the budget with the user, and start the schedule earlier for a long podcast list (step 8). Episodes over budget wait for a later run.
- Tell the user how the paper treats them: what is said on a show is reported as said, by whom, never as a confirmed fact, and never as a story's independent origin (RUN.md, "Podcasts").
- To start with the past year of episodes in memory, see step 6 (backfill).

**Article sources:**
- Test every candidate: `python3 engine/tools/fetch.py check <url>` (status, feed found, whether the browser was needed). Prefer the feed when one exists. A page that only works in the browser needs Playwright in the scheduled environment; note it.
- Then `python3 engine/tools/graph.py seed --sync` and `python3 engine/tools/fetch.py sources --top 20`. This reads every source once, **sets the first_seen baseline** (undated links already on listing pages count as backlog, so later issues aren't flooded with old items), and shows what the ranking puts on top. Warning: the baseline also drops everything currently on undated listing pages, which for a reader who relies on regulator or ministry pages without feeds can leave the first issue nearly empty. For the first issue only, you may read with `fetch.py sources --no-baseline` (undated links count as today's news); every later run uses the baseline. Items dated ahead (consultation deadlines, hearings) appear under `upcoming`, not in the ranking. If the top is off-beat, sharpen the profile paragraph and the `queries`, then re-rank without fetching: `python3 engine/tools/fetch.py rank .work/items.json`.
- Drop or demote sources that fail or only produce noise: change their `status` in `sources.json` ("dead", or "candidate") and run `graph.py seed --sync`, which copies url, feed, kind, group, lang, cadence and status from the file onto existing Source nodes (`seed` alone only adds missing ones). `fetch.py sources` reads sources with status active, seed and candidate; "dead", "trial" and "citation" are not read. Keep the list short and good; the paper adds candidates as it goes.

## 5. The check
- The model is in `model/` since step 2a; commit it with the instance.
- `python3 engine/tools/instance.py check` must say `"ok": true`. Fix every problem; read the warnings.
- Commit: `git add -A && git commit -m "Set up the Morning Fold" && git push -u origin main`.

## 6. Optional: backfill
If the user wants the paper to start with a memory (people moves, deals, what was said on their podcasts over the last year), follow `engine/BACKFILL.md` on their own computer. It is long-running and can come after the first issue.

## 7. The first issue
Run an issue by following `engine/RUN.md` ("The issue run") in this session, start to finish, so the user sees it work. Differences on the first run:
- There is no artifact yet. Publish the checked page as a **new** artifact with the Artifact tool. The reply box needs the page to declare two capabilities: the database (for the `replies` collection, readable and writable by the owner only) and viewer identity (so only the owner sees the box). The declaration a working Morning Fold uses is `{"db": {"rules": [{"path": "", "read": "owner", "write": "owner"}]}, "user": {}}`; load the `artifact-capabilities` skill first and adjust if its current contract differs. Only this first publish passes `capabilities`; every later run publishes to the same URL without them, so the declaration is kept.
- Write the artifact's URL into `reader.md` as `artifact_url`, commit, push.
- Show the user the page, the Reply box on the back page, and ask for their first reactions there.

## 8. Cloud run, phone and schedule
Goal: the paper runs in the cloud every morning while their computer sleeps, and lands on their phone. **Do everything you can yourself. For the few steps only the user can do, guide one step at a time:** say exactly where to click and what to choose, wait for "done", verify, then give the next step. Never put several click-steps in one message; many users are not technical.

Before starting, ask: is their Claude plan Pro, Max, Team or Enterprise? Cloud runs need one of these (on Team and Enterprise an admin can switch scheduled tasks off). Without one, use the local fallback at the end of this step.

**You prepare, without the user:**
- The setup script: copy `engine/tools/env_setup.sh` to the clipboard (`pbcopy < engine/tools/env_setup.sh` on macOS; `wl-copy` or `xclip -sel c` on Linux) and save a copy as `.work/setup-script.txt` in case the clipboard is lost.
- The schedule: start time about two hours before `publish_by`, earlier with many podcasts to transcribe (step 4); as cron `CRON_TZ=<their timezone> <min> <hour> * * *` (a cron without `CRON_TZ` is UTC and drifts an hour with daylight saving). Say the start time back to them in their time zone; set `quiet_window` from it (step 3).

**The user does, one step at a time:**
1. **Give the cloud access to the private repository.** Easiest: ask them to type `/web-setup` in this Claude Code session (it hands their GitHub login to claude.ai). If that isn't available, open `https://github.com/apps/claude` for them (`open <url>`): Install (or Configure), choose their account, "Only select repositories", pick the instance repository, Save.
2. **Create the cloud environment.** Open `https://claude.ai/code` for them. Then, one at a time: click the environment selector (the first control in the row under the message box) → "New environment"; name it "Morning Fold"; **Network access: Full**; **Setup script**: paste (it is on the clipboard); leave environment variables empty; click "Add environment". Ask them to confirm the name.
3. **The phone (optional, the best experience).** The edition arrives as a notification and opens on the phone. Install the Claude app (App Store or Google Play), sign in with the **same account** as claude.ai, and **allow notifications** when the app asks. If they missed that prompt: phone Settings → Notifications → Claude → Allow.

**Then you again:**
4. **Create the scheduled task** with the `schedule` skill (`/schedule`), or the scheduled-task tools if this session has them. Repository: their instance. Environment: the one from step 2 (look its id up; never invent one). Prompt: `Follow engine/RUN.md.` Schedule: the cron prepared above. Turn push notifications on if they set up the phone. If a tool asks the user to approve, tell them in one line what they are approving.
5. **Run it once now** (run-now), and explain the reply loop (step 9) while it works. Then `git pull` and read the run log in `runs/`. Check:
   - `environment_cached` is true. False means the setup script didn't run or wasn't cached: in step 2, check that the script was pasted whole.
   - The engine was there. If `engine/` failed to clone (the cloud's git proxy serves only repositories attached to the task), add the engine repository as a second repository on the task, or rely on RUN.md's download fallback; then note which one worked.
   - No source failed for network reasons, and the page was published.
   - On the phone: a notification arrived, and the link opens the edition. If not: same account in the app? Notifications allowed in the phone's settings?
   Fix and run again until all of these hold. Then tell them: runs approve their own tool use; if a run ever stops to ask, the question is on the task's page at claude.ai/code.

Fallback: a local schedule on a computer that is on at that time. In the instance directory, a cron line such as
`30 4 * * * cd <instance path> && git pull -q && git submodule update --init -q engine && claude -p "Follow engine/RUN.md." --allowedTools "Bash Read Write Edit Agent WebSearch Artifact ArtifactData" >> runs/cron.log 2>&1`
with `CRON_TZ=<their timezone>` on the line above where the cron supports it (otherwise the time is the computer's local time), or a launchd agent on macOS with the same command. The run is unattended, so every tool it uses must be pre-approved; check the first log for a denied tool. Tell the user the computer must be awake; on macOS `pmset` can schedule a wake.

Install the dependencies on that computer once (step 0), and run the line once by hand before leaving it alone.

## 9. Explain the reply loop to the user
Every edition's back page has **Reply to the editor**. Whatever they type there (one line is enough) is stored with the page. The next run reads new replies first and acts on them: a preference becomes a lasting rule, a new interest goes into `reader.md`, a correction gets checked, a question gets answered in the next letter to the editor, and a thesis can be added, rephrased or dropped. Under each reply the page then shows one line saying what changed. Only the page's owner sees the box.

## Later: engine updates
The engine improves independently. In the instance:
```bash
git submodule update --remote engine
python3 engine/tools/instance.py check
python3 engine/tools/sync_reply.py --instance    # only if the instance overrides whole templates in site/
git add engine && git commit -m "Engine update" && git push
```
Read the engine's commit log first (`git -C engine log --oneline HEAD@{1}..HEAD`); a change to `RUN.md` changes how every run behaves.

## Overriding templates
- Colours and fonts: `site/theme.css` (tokens only). The tools pour it into the template's `fold-theme` slot.
- Anything more: copy `engine/site/issue_template.html` (or the week template, or `reply_snippet.html`) into the instance's `site/` and edit it there. The instance file wins over the engine's, file by file (`python3 engine/tools/site.py which`). Keep the contract described at the top of each template: sample pages inside `<main>` marked `data-sample`, the REPLY markers, the theme slot. You then own that copy: engine improvements to the template no longer reach it until you merge them by hand.

## Contributing to the engine
Engine changes (a tool fix, a better method in RUN.md, a template improvement) go to the engine repository, never with content:
- Work in a **separate clone** of the engine, not inside the instance's `engine/` submodule.
- Nothing personal: no names, employers, sources lists, theses, issue text, graph data or quotes from any instance. Examples in docs and templates must be fictional (extend `examples/instance/`, which is fictional).
- Before committing, grep the diff for the user's own names, employer, city and watched companies; it must find nothing.
- Test the change against `examples/instance` (`MORNING_FOLD_HOME=examples/instance python3 tools/graph.py brief`, `check_issue.py`, `fetch.py rank`) and, privately, against the user's own instance.
- Then open a pull request against the engine.
