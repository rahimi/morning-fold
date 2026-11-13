"""Structural check of an issue page before it is published.

    python3 tools/check_issue.py <page.html> [YYYY-MM-DD]
    python3 tools/check_issue.py --week <page.html> [YYYY-MM-DD]   the week-ahead page

Exits 1 with the problems listed when the page would render broken: content
outside the book (left over from the template), unbalanced sections, pages that
are not direct children of the book, a missing or duplicated reply block, a
wrong edition date, the template's sample pages (data-sample) or unfilled
{{PLACEHOLDERS}} still in the page.
"""
import re
import sys
from html.parser import HTMLParser

VOID = {"meta", "link", "br", "img", "input", "hr", "source", "wbr", "area", "base", "col", "embed", "track"}
WATCH = {"main", "section", "div", "ul", "ol", "li", "nav", "p"}


class Walk(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.problems = [], []
        self.mains = self.pages = self.navs = 0
        self.closed_main = False
        self.after_main = []          # (tag or text) seen between </main> and <nav>
        self.edition = None

    def where(self):
        return "line %d" % self.getpos()[0]

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("name") == "fold:edition":
            self.edition = a.get("content")
        if self.closed_main and not self.navs and tag not in ("nav", "script", "style"):
            self.after_main.append("<%s> at %s" % (tag, self.where()))
        if tag == "main":
            self.mains += 1
        if tag == "nav":
            self.navs += 1
        if tag == "section" and "page" in (a.get("class") or "").split():
            self.pages += 1
            parent = self.stack[-1][0] if self.stack else None
            if parent is None:
                self.problems.append("page section %s at %s is outside the book: the main element with class book "
                                     "was closed early or never opened (rebuild with site.py template issue --body)"
                                     % (a.get("id"), self.where()))
            elif parent != "main":
                self.problems.append("page section %s at %s is inside <%s>, not directly in the book (an earlier "
                                     "element is not closed)" % (a.get("id"), self.where(), parent))
        if tag in WATCH:
            self.stack.append((tag, self.where()))

    def handle_endtag(self, tag):
        if tag in VOID or tag not in WATCH:
            return
        if not self.stack or self.stack[-1][0] != tag:
            open_ = self.stack[-1] if self.stack else None
            self.problems.append("</%s> at %s does not close the open %s"
                                 % (tag, self.where(), "<%s> from %s" % open_ if open_ else "nothing"))
            # recover: pop to the matching tag if there is one
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    del self.stack[i:]
                    break
        else:
            self.stack.pop()
        if tag == "main":
            self.closed_main = True

    def handle_data(self, data):
        if self.closed_main and not self.navs and data.strip():
            self.after_main.append("text %r at %s" % (data.strip()[:40], self.where()))


def check(html, date=None):
    w = Walk()
    w.feed(html)
    w.close()
    p = list(w.problems)
    if w.mains != 1:
        p.append("%d <main> elements, expected exactly 1" % w.mains)
    if w.after_main:
        p.append("content between </main> and the page nav (left over from the template?): "
                 + "; ".join(w.after_main[:5]))
    for tag, at in w.stack:
        p.append("<%s> opened at %s is never closed" % (tag, at))
    if w.pages < 3:
        p.append("only %d pages" % w.pages)
    if w.navs != 1:
        p.append("%d page navs, expected 1" % w.navs)
    for m in ("<!-- REPLY:start -->", "<!-- REPLY:end -->"):
        if html.count(m) != 1:
            p.append("%s appears %d times, expected once" % (m, html.count(m)))
    samples = re.findall(r"<[a-z]+\b[^>]*\sdata-sample\b", html)
    if samples:
        p.append("%d sample element(s) from the template survive (data-sample): replace every sample page"
                 % len(samples))
    left = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", html)))
    if left:
        p.append("unfilled placeholders %s: build the page from `site.py template issue`" % ", ".join(left))
    if date and w.edition != date:
        p.append("fold:edition is %r, expected %r" % (w.edition, date))
    return p, w.pages


def check_week(html, date=None):
    """The week-ahead page: one parseable #week JSON block without the sample flag, seven days, the reply block once,
    no placeholders, the right edition date."""
    import json
    p = []
    m = re.findall(r'<script type="application/json" id="week">(.*?)</script>', html, re.S)
    if len(m) != 1:
        p.append("%d #week JSON blocks, expected 1" % len(m))
    else:
        try:
            w = json.loads(m[0])
            if w.get("sample"):
                p.append('the #week JSON is still the template sample ("sample": true)')
            if len(w.get("days") or []) != 7:
                p.append("days has %d entries, expected 7" % len(w.get("days") or []))
            for k in ("issued", "week_label", "theses"):
                if k not in w:
                    p.append("#week JSON lacks %r" % k)
        except ValueError as e:
            p.append("#week JSON does not parse: %s" % e)
    for mk in ("<!-- REPLY:start -->", "<!-- REPLY:end -->"):
        if html.count(mk) != 1:
            p.append("%s appears %d times, expected once" % (mk, html.count(mk)))
    left = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", html)))
    if left:
        p.append("unfilled placeholders %s: build the page from `site.py template week`" % ", ".join(left))
    ed = re.search(r'<meta name="fold:edition" content="([^"]*)"', html)
    if date and (not ed or ed.group(1) != date):
        p.append("fold:edition is %r, expected %r" % (ed and ed.group(1), date))
    return p, 1


if __name__ == "__main__":
    args = sys.argv[1:]
    week = "--week" in args
    args = [a for a in args if a != "--week"]
    if not args:
        sys.exit(__doc__)
    problems, pages = (check_week if week else check)(open(args[0], encoding="utf-8").read(), args[1] if len(args) > 1 else None)
    if problems:
        print("NOT OK, do not publish:")
        for x in problems:
            print(" -", x)
        sys.exit(1)
    print("ok: week page" if week else "ok: %d pages, one book, nothing outside it" % pages)
