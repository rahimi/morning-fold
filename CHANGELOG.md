# Changelog

What each engine release changes in a reader's paper, newest first. Instances take the latest release tag (`reader.md`'s `engine_updates`); the dreaming run installs it and tells the reader the first section below. "Instance changes" lists what an instance must change by hand or by the run before the release works; most releases have none.

## 0.2.2

### What changes in your paper
- Nothing in the paper. New setups give the scheduled task the working branch `main`, so runs no longer leave a `claude/…` branch in the repository each day. If yours collects them, ask Claude Code on your computer to set the scheduled task's working branch to `main`; the branches only hold what is already on `main` and can then be deleted.

## 0.2.1

### What changes in your paper
- Financial calendars keep their dates: a calendar page whose date table or list the text extraction used to drop is now read in full, and a calendar published as a data feed (JSON) is read as text instead of failing. Ordinary articles read as before.
- The Saturday run no longer re-checks the same people's roles every week: a role confirmed in the last six months is left off the list of possibly outdated roles.

## 0.2.0

### What changes in your paper
- Engine updates arrive by themselves: the Saturday run installs the newest engine release, checks it against your paper and goes back to the previous one if anything fails, then tells you in one line what changed. `engine_updates` in `reader.md` chooses `auto` (default), `rc` (also release candidates) or `off`. Reply "roll back the engine" to undo the last update.
- Opt-in engine feedback: with `engine_feedback: issue` or `pr` the Saturday run files engine problems on the engine's GitHub, after a leak check of every text for your names, sources and graph entities and a second, reader-blind check. Default `off`.
- On a watched company's results day the paper reads the company's own investor-relations page and the reports it links, not only press relays.
- A rival read that changes the lead or its headline claim sends the story back through the desk check before it is published.
- Sources: reading statistics (last read, failed days in a row, articles per source) are kept by the tools, an outage no longer counts as failures of every source, and a podcast episode is recognised under any URL.
- Memory: a fact can be corrected in one step with its history kept, a checked pair of different entities is no longer listed as a possible duplicate, and a fact can no longer point from an entity to itself.
- The tools print their usage on `--help` and stop on an unknown command or flag instead of running.

Coming from 0.1.0, which has no `instance.py update`: take this release once by hand, as SETUP.md's "Later: engine updates" says. Later releases arrive with the Saturday run.

## 0.1.0

### What changes in your paper
The first tagged state of the engine: daily issues with stories scored against a fixed bar and grounded in verbatim quotes, a knowledge graph as the paper's memory, named theses scored when they resolve, the Saturday dreaming run that consolidates the memory, the Sunday week-ahead planner, podcasts as claims, and the reply box that the next run reads first. A run started by hand on a day already published does its checks and handles replies, but prints no second edition.
