# The Morning Fold engine

This repository is the **engine** of The Morning Fold: a personal newspaper Claude writes overnight for one reader. It holds the tools (`tools/`), the run method (`RUN.md`, `BACKFILL.md`), the default edition templates (`site/`), setup instructions (`SETUP.md`) and a fictional example instance (`examples/instance/`). It is public and holds nothing about any real reader.

Each reader's paper lives in their own **private instance** repository, with this engine as the submodule `engine/`.

What to do:
- **Inside an instance** (a `reader.md` in the working directory or a parent, the engine at `engine/`): you are running or tending a paper. Follow `engine/RUN.md` for a run; change the reader's settings in their `reader.md`, `sources.json`, `theses.json`.
- **In this engine checkout, and the user wants their own paper** ("set up my Morning Fold"): they have no instance yet. Follow `SETUP.md` step by step. Never write the user's details into this repository.
- **In this engine checkout, working on the engine itself**: keep it generic. Test against the fictional instance with `MORNING_FOLD_HOME=examples/instance python3 tools/<tool>.py ...`. Examples and template samples must stay fictional. See "Contributing to the engine" in `SETUP.md`.

How the tools find things: `tools/paths.py`. `ENGINE` is this checkout; `HOME` is the instance (`$MORNING_FOLD_HOME`, else the nearest parent of the working directory with a `reader.md`, else the working directory). Every file a tool writes goes under `HOME`. The embedding model is not in git: `python3 tools/get_model.py`. `requirements.txt` and `tools/env_setup.sh` (the cloud environment's setup script) list the same packages; change both together.
