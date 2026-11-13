"""Copies the reply snippet into the edition templates between the REPLY markers.

    python3 tools/sync_reply.py            the engine's site/ templates, from the engine's snippet
    python3 tools/sync_reply.py --instance the instance's own site/ templates (overrides), from the effective
                                           snippet (the instance's site/reply_snippet.html, else the engine's)
"""
import os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402

if any(a not in ("--instance",) for a in sys.argv[1:]):
    sys.exit(__doc__)
if "--instance" in sys.argv:
    folder, snippet_path = os.path.join(paths.HOME, "site"), paths.site_file("reply_snippet.html")
else:
    folder, snippet_path = os.path.join(paths.ENGINE, "site"), os.path.join(paths.ENGINE, "site", "reply_snippet.html")
snippet = open(snippet_path, encoding="utf-8").read().split("\n", 1)[1].strip()
block = f"<!-- REPLY:start -->\n{snippet}\n<!-- REPLY:end -->"
done = 0
for name in ("issue_template.html", "week_template.html"):
    path = os.path.join(folder, name)
    if not os.path.exists(path):
        continue
    html = open(path, encoding="utf-8").read()
    new, n = re.subn(r"<!-- REPLY:start -->.*?<!-- REPLY:end -->", lambda m: block, html, flags=re.S)
    if n != 1:
        raise SystemExit(f"{path}: expected one REPLY marker pair, found {n}")
    open(path, "w", encoding="utf-8").write(new)
    done += 1
    print(f"{path}: reply block synced")
if not done:
    print(f"no templates in {folder}")
