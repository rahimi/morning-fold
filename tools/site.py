#!/usr/bin/env python3
"""The edition templates, resolved for this instance.

  python3 tools/site.py template issue|week [--out FILE]
      The template: the instance's site/<name>_template.html when it has one, else the engine's; with the
      instance's site/theme.css (colour and font tokens) poured into the fold-theme style slot, and the
      masthead, place and language placeholders filled from reader.md. Prints the HTML, or writes it to FILE.

  python3 tools/site.py template issue --body PAGES.html --date YYYY-MM-DD --out FILE
      Today's issue: the template with its sample pages replaced by PAGES.html (the section elements, cover
      first, back page last). In PAGES.html, a line holding only the comment REPLY (written as an HTML
      comment) marks where the reply block goes on the back page; site.py puts the template's reply block
      there. --date sets the edition meta. Then run check_issue.py on FILE.

  python3 tools/site.py template week --json WEEK.json --date YYYY-MM-DD --out FILE
      The week-ahead page: the template with its #week JSON replaced by WEEK.json (validated).

  python3 tools/site.py which
      Which file each template, the reply snippet and the theme come from (engine or instance).

Page structure and what may change are described in the templates' own comments and in RUN.md.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402

NAMES = {"issue": "issue_template.html", "week": "week_template.html"}


def theme_css():
    p = os.path.join(paths.HOME, "site", "theme.css")
    return open(p, encoding="utf-8").read().strip() if os.path.exists(p) else ""


def render(kind):
    html = open(paths.site_file(NAMES[kind]), encoding="utf-8").read()
    css = theme_css()
    html, n = re.subn(r'<style id="fold-theme">.*?</style>',
                      lambda m: f'<style id="fold-theme">\n{css}\n</style>' if css else '<style id="fold-theme"></style>',
                      html, flags=re.S)
    if css and not n:
        sys.stderr.write("warning: template has no <style id=\"fold-theme\"> slot; site/theme.css not applied\n")
    return html


def fill(html):
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    for key, val in (("MASTHEAD", paths.cfg("masthead")), ("PLACE", paths.cfg("place")), ("LANG", paths.cfg("language"))):
        html = html.replace("{{%s}}" % key, esc(val or ""))
    return html


REPLY_RX = re.compile(r"<!-- REPLY:start -->.*?<!-- REPLY:end -->", re.S)
MAIN_RX = re.compile(r'<main\b[^>]*class="book"[^>]*>')


def with_body(html, body):
    """Replaces everything inside the book with `body`, and the REPLY placeholder in it with the template's reply block."""
    reply = REPLY_RX.search(html)
    if not reply:
        raise SystemExit("the template has no reply block (REPLY markers); run sync_reply.py")
    if not REPLY_RX.search(body):
        body, n = re.subn(r"^[ \t]*<!--\s*REPLY\s*-->[ \t]*$", lambda m: reply.group(0), body, count=1, flags=re.M)
        if not n:
            raise SystemExit("the pages file has no reply placeholder: put a line holding only an HTML comment "
                             "with the word REPLY on the back page")
    if re.search(r"<main\b|</main>", body):
        raise SystemExit("the pages file must hold only the page sections, not the book element itself")
    m = MAIN_RX.search(html)
    close = html.rfind("</main>")
    if not m or close < m.end():
        raise SystemExit("the template has no book element (main with class book)")
    return html[:m.end()] + "\n" + body.strip("\n") + "\n" + html[close:]


def with_week(html, data):
    j = re.compile(r'(<script type="application/json" id="week">).*?(</script>)', re.S)
    if not j.search(html):
        raise SystemExit("the template has no #week JSON block")
    data.pop("sample", None)
    blob = json.dumps(data, ensure_ascii=False, indent=1).replace("</", "<\\/")
    return j.sub(lambda m: m.group(1) + "\n" + blob + "\n" + m.group(2), html, count=1)


def with_date(html, date):
    if not re.fullmatch(r"\d{4}-\d\d-\d\d", date):
        raise SystemExit("--date must be YYYY-MM-DD")
    return re.sub(r'<meta name="fold:edition" content="[^"]*">', f'<meta name="fold:edition" content="{date}">', html, count=1)


def main(args):
    if args[:1] == ["which"]:
        own = lambda n: "engine" if paths.site_file(n) == os.path.join(paths.ENGINE, "site", n) else "instance"
        print({"home": paths.HOME, **{n: own(n) for n in list(NAMES.values()) + ["reply_snippet.html"]},
               "theme.css": "instance" if theme_css() else "none"})
        return
    if len(args) < 2 or args[0] != "template" or args[1] not in NAMES:
        sys.exit(__doc__)
    html = render(args[1])
    opt = lambda k: args[args.index(k) + 1] if k in args else None
    if opt("--body"):
        html = with_body(html, open(opt("--body"), encoding="utf-8").read())
    if opt("--json"):
        html = with_week(html, json.load(open(opt("--json"), encoding="utf-8")))
    if opt("--date"):
        html = with_date(html, opt("--date"))
    html = fill(html)
    if "--out" in args:
        dst = args[args.index("--out") + 1]
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        open(dst, "w", encoding="utf-8").write(html)
        print(dst)
    else:
        sys.stdout.write(html)


if __name__ == "__main__":
    main(sys.argv[1:])
