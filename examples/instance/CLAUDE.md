# This is a Morning Fold instance (private)

This repository is one reader's personal newspaper: their profile (`reader.md`), sources (`sources.json`),
seed theses (`theses.json`) and everything the paper has learned (`graph/`, `archive/`, `index/`, `issues/`,
`runs/`, `data/`). It is PRIVATE and must stay private.

The engine (tools, method, templates) is the git submodule in `engine/`.

- A scheduled or manual run: follow `engine/RUN.md`, from this directory.
- Setting up or changing the reader's profile, sources or schedule: see `engine/SETUP.md`.
- Never copy content from this repository into the engine, and never push the engine submodule from here
  with content in it. Engine improvements go to the engine repository as their own change (engine/SETUP.md,
  "Contributing to the engine").
