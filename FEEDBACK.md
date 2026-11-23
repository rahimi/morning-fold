# The Morning Fold: engine feedback

How a dreaming run reports engine problems (a tool bug, a gap in `RUN.md`), when `reader.md`'s `engine_feedback` is `relay` (to the maintainer's relay, which needs no login), `issue` or `pr` (on the engine's GitHub repository). Commands run from the instance root. Whatever leaves may become public: nothing from the instance may reach it.

## When
`issue` and `pr` need a GitHub login for `gh` (`GH_TOKEN` or `gh auth login`): only if `gh auth status` succeeds and the engine URL in `.gitmodules` is on github.com; otherwise tell the reader in your message, as `RUN.md` says, and that `engine_feedback: relay` needs no login. File the week's `engine_issues` from the run logs and your own, skipping entries that already have a URL; at most three per run.

## Write, then gate
- Write each for strangers: what the engine does, what it should do, the steps on `examples/instance` with its fictional names. Name the engine version: `instance.py update --check`'s `current`, or `git -C engine describe --tags --always`. Nothing from this instance. Put the title and the body in two files in the work dir.
- Two gates, both must pass, on every text that leaves (title, body, comment, the relay's file; for a pull request also the diff, commit message and branch name):
  1. `python3 engine/tools/instance.py leakcheck <file>`, with the instance's own `engine/` and no `MORNING_FOLD_HOME` set.
  2. A fresh sub-agent that reads only `reader.md` and the outgoing text answers: could this text identify the reader, their employer, their sources or anything the paper printed? yes or no, and why.
- On a leakcheck hit, rephrase once. On a second hit or a yes, don't file it; keep it in the run log.

## Relay (`relay`)
- One file: the title on the first line, the body below it; it passes both gates. Then `python3 engine/tools/instance.py feedback <file>` (it runs leakcheck again and sends nothing on a hit).
- It prints the relay's answer; `action` `duplicate` means the relay already has this text: the entry gets that URL and does not count toward the three. Put its `url` in the `engine_issues` entry (the repository is the maintainer's, private: tell the reader "sent to the engine's maintainer", without the link). On `sent: false`, keep the entry without a URL and say so.

## Issues
- `gh issue list -R <owner/repo from that URL> --state all --search "<words from the checked title>"`. A match is never filed again; comment on an open one (`gh issue comment --body-file <checked file>`).
- Else `gh issue create --title "$(cat <title file>)" --body-file <body file>`, the checked files only.
- Put the URL in the `engine_issues` entry and in your message. If `gh` fails, keep the entry without a URL and tell the reader.

## Pull requests (`pr` only)
A small fix (about 30 lines) also gets a pull request:
1. In a separate clone of the engine in the work dir, on its own branch: make the fix, test it with `MORNING_FOLD_HOME=examples/instance`, commit.
2. Back at the instance root, pass both gates over `git -C <clone> diff origin/main..HEAD`, the commit message, the title and body files and the branch name.
3. Only then push (to a fork, `gh repo fork`, when you lack push rights) and `gh pr create --title "$(cat <title file>)" --body-file <body file>` (`--head <user>:<branch>` from a fork).

Never merge, never move the instance's `engine` pin.
