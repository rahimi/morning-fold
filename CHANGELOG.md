# Changelog

What each engine release changes in a reader's paper, newest first. Instances take the latest release tag (`reader.md`'s `engine_updates`); the dreaming run installs it and tells the reader the first section below. "Instance changes" lists what an instance must change by hand or by the run before the release works; most releases have none.

## 0.3.0

### What changes in your paper
- Nothing a run learned gets lost between runs any more. Scheduled cloud runs work on a branch of their own, and some of them never reached `main`, so the next run started without the previous run's memory. Every run now starts by merging any such branch (`instance.py sync`), pushes both its branch and `main`, and deletes finished run branches. A branch that can't be merged safely is left alone and reported, with the commands to resolve it. If the run that installs this release itself lands only on its own branch, the next runs still use the previous engine until the next Saturday installs it again; nothing is lost.
- German pages read cleanly: pages served without a declared character set no longer turn umlauts into garbled text, and quotes copied from garbled text before still count as found in their source.
- A placeholder page (filler text instead of the article) is reported as unreadable instead of being stored as the article.
- Podcasts you listen to anyway are ranked among themselves and no longer push news down the list; trade podcasts are transcribed first.
- The Saturday memory pass works on a shorter, truer list: real look-alike entities are listed apart from brand and group pairs (a group and its subsidiary), and facts whose source was merged away are listed so they get fixed.
- When two articles report different figures for one fact, the editor now sees both.
- New `engine_feedback: relay`: engine problems the Saturday run finds go to the engine's maintainer as generic, leak-checked text, without a GitHub login. Off unless you say yes; reply "send engine problems to the maintainer" to turn it on. Issues go to a private inbox only the maintainer reads, filed by the relay, not under your name.

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
