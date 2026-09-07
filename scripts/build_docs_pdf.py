"""Render the project documentation to a print-quality PDF.

Built from the same Markdown the repository serves, so the PDF cannot
drift from the README a reviewer might read on GitHub instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import mistune
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve()
REPO = Path("c:/dev/Hunar.ai")
OUT = REPO / "docs" / "Hunar-Recruiting-Documentation.pdf"

SECTIONS = [
    ("README.md", None),
    ("docs/attendance-without-apps.md", "Appendix: Attendance without smartphones"),
]

CSS = """
@page {
  size: A4;
  margin: 20mm 18mm 18mm 18mm;
}

:root {
  --ink: #16181d;
  --muted: #5d6470;
  --rule: #e3e6ea;
  --accent: #1c2530;
  --code-bg: #f6f7f9;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  color: var(--ink);
  font-family: "Charter", "Georgia", "Times New Roman", serif;
  font-size: 10.5pt;
  line-height: 1.62;
  -webkit-font-smoothing: antialiased;
}

/* ── title page ─────────────────────────────────────── */
.cover {
  page-break-after: always;
  /* Deliberately left to flow rather than stretched to the printable
     height. Pinning the footer to the bottom of a fixed 246mm box
     overflowed once the PDF's own footer template took its share, and
     split the title page in two: the eyebrow alone on page one, the
     title and links on page two. Trailing space under a title page is
     normal; a title page in two halves is not. */
  padding-top: 30mm;
}
.cover .eyebrow {
  font-family: "Inter", "Segoe UI", system-ui, sans-serif;
  font-size: 8.5pt;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
}
.cover h1 {
  /* The global h1 rule forces a page break before every section title,
     and the cover's own title inherited it — so the title page broke
     immediately after the eyebrow, leaving the name and the links on
     page two. This is the whole reason the cover looked half empty. */
  page-break-before: avoid;
  font-family: "Inter", "Segoe UI", system-ui, sans-serif;
  font-size: 30pt;
  line-height: 1.12;
  letter-spacing: -0.02em;
  margin: 10px 0 0;
  border: 0;
  padding: 0;
}
.cover .sub {
  font-size: 12pt;
  color: var(--muted);
  margin-top: 14px;
  max-width: 118mm;
  line-height: 1.5;
}
.cover .rule {
  height: 3px;
  width: 54mm;
  background: var(--accent);
  margin: 26px 0 26px;
}
.cover dl {
  display: grid;
  grid-template-columns: 30mm 1fr;
  gap: 7px 14px;
  font-family: "Inter", "Segoe UI", system-ui, sans-serif;
  font-size: 9.5pt;
  margin: 0;
}
.cover dt { color: var(--muted); }
.cover dd { margin: 0; word-break: break-all; }
.cover .foot {
  margin-top: 26mm;
  font-family: "Inter", "Segoe UI", system-ui, sans-serif;
  font-size: 8.5pt;
  color: var(--muted);
  border-top: 1px solid var(--rule);
  padding-top: 10px;
}

/* ── headings ───────────────────────────────────────── */
h1, h2, h3, h4 {
  font-family: "Inter", "Segoe UI", system-ui, sans-serif;
  letter-spacing: -0.011em;
  color: var(--accent);
  page-break-after: avoid;
}
h1 {
  font-size: 19pt;
  margin: 0 0 4px;
  padding-bottom: 8px;
  border-bottom: 2px solid var(--accent);
  page-break-before: always;
}
/* Keep a heading with the text that follows it, so a page never ends on
   a title alone. */
h2 + *, h3 + *, h1 + * { page-break-before: avoid; }
h1.first { page-break-before: avoid; }
h2 {
  font-size: 13.5pt;
  margin: 26px 0 8px;
  padding-bottom: 5px;
  border-bottom: 1px solid var(--rule);
}
h3 { font-size: 11pt; margin: 18px 0 6px; }
h4 { font-size: 10pt; margin: 14px 0 4px; }

p { margin: 0 0 9px; orphans: 3; widows: 3; }
strong { font-weight: 650; }
em { color: var(--muted); }

ul, ol { margin: 0 0 10px; padding-left: 20px; }
li { margin-bottom: 5px; }
li > p { margin-bottom: 4px; }

a { color: #1a4f8a; text-decoration: none; word-break: break-word; }

/* ── tables ─────────────────────────────────────────── */
table {
  width: 100%;
  border-collapse: collapse;
  margin: 12px 0 16px;
  font-family: "Inter", "Segoe UI", system-ui, sans-serif;
  font-size: 8.8pt;
}
/* Rows stay whole, the table itself may split, and the header repeats on
   each page it continues onto. Forbidding the table from splitting meant
   any long one jumped wholesale to the next page and left the remainder
   of the previous one empty. */
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th {
  text-align: left;
  font-weight: 600;
  background: var(--code-bg);
  border-bottom: 1.5px solid #cfd4da;
  padding: 6px 8px;
  color: var(--accent);
}
td {
  border-bottom: 1px solid var(--rule);
  padding: 6px 8px;
  vertical-align: top;
  line-height: 1.45;
}
tr:last-child td { border-bottom: none; }

/* ── code ───────────────────────────────────────────── */
code {
  font-family: "JetBrains Mono", "Cascadia Mono", "Consolas", monospace;
  font-size: 0.86em;
  background: var(--code-bg);
  padding: 1px 4px;
  border-radius: 3px;
}
pre {
  background: var(--code-bg);
  border: 1px solid var(--rule);
  border-left: 3px solid #9aa4b0;
  border-radius: 4px;
  padding: 10px 12px;
  overflow: visible;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 10px 0 14px;
}
pre code {
  background: none;
  padding: 0;
  font-size: 8.2pt;
  line-height: 1.5;
}

blockquote {
  margin: 12px 0;
  padding: 2px 0 2px 14px;
  border-left: 3px solid var(--rule);
  color: var(--muted);
}

hr { border: 0; border-top: 1px solid var(--rule); margin: 22px 0; }
"""


def render(md: str) -> str:
    return str(mistune.create_markdown(plugins=["table", "strikethrough", "url"])(md))


def build_html() -> str:
    parts: list[str] = []
    first = True
    for relative, override_title in SECTIONS:
        text = (REPO / relative).read_text(encoding="utf-8")

        if override_title:
            # Demote the file's own H1 and give the appendix its own title.
            text = re.sub(r"^# .*$", f"# {override_title}", text, count=1, flags=re.M)

        if first:
            # The cover already carries the links, so the README's own
            # borderless table of them is dropped rather than printed twice.
            lines = text.splitlines()
            kept, skipping = [], False
            for line in lines:
                if line.strip() == "| | |":
                    skipping = True
                    continue
                if skipping:
                    if line.startswith("|"):
                        continue
                    skipping = False
                kept.append(line)
            text = "\n".join(kept)
        body = render(text)
        if first:
            # The cover carries the title, so drop the duplicate H1.
            body = re.sub(r"<h1>.*?</h1>", "", body, count=1, flags=re.S)
            body = f'<h1 class="first">Overview</h1>{body}'
            first = False
        parts.append(body)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Hunar Recruiting — Documentation</title>
<style>{CSS}</style></head><body>

<section class="cover">
  <div class="eyebrow">Technical assignment &middot; Hunar.ai</div>
  <h1>Hunar Recruiting</h1>
  <div class="sub">
    Two recruiting applications sharing one voice pipeline: AI phone screening
    of applicants, and consent-gated outreach to people who never applied.
  </div>
  <div class="rule"></div>
  <dl>
    <dt>Live demo</dt><dd>https://hunar-aryan-s-projectsss1.vercel.app</dd>
    <dt>Password</dt><dd><code>ledger-copper-tundra-20</code></dd>
    <dt>API schema</dt><dd>https://hunar-rvf8.onrender.com/docs</dd>
    <dt>Repository</dt><dd>https://github.com/Rn-sripathi/Hunar</dd>
    <dt>Author</dt><dd>Rishwanth Aryan Sripathi</dd>
  </dl>
  <div class="foot">
    Backend FastAPI on Python 3.12 &middot; frontend Next.js and TypeScript with
    shadcn/ui &middot; Postgres on Neon &middot; 498 tests, ruff and mypy strict clean
  </div>
</section>

{"".join(parts)}
</body></html>"""


def main() -> None:
    html_path = Path(
        "C:/Users/RISHWA~1/AppData/Local/Temp/claude/c--dev-Hunar-ai/"
        "82f51a10-e230-4f94-86ae-d0ef28f6fe19/scratchpad/docs.html"
    )
    html_path.write_text(build_html(), encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.as_uri(), wait_until="load")
        page.wait_for_timeout(700)
        page.pdf(
            path=str(OUT),
            format="A4",
            print_background=True,
            display_header_footer=True,
            header_template="<div></div>",
            footer_template=(
                '<div style="width:100%;font-family:Segoe UI,sans-serif;font-size:7.5pt;'
                'color:#8a919b;padding:0 18mm;display:flex;justify-content:space-between;">'
                "<span>Hunar Recruiting &mdash; technical assignment</span>"
                '<span class="pageNumber"></span>'
                "</div>"
            ),
            margin={"top": "18mm", "bottom": "16mm", "left": "18mm", "right": "18mm"},
        )
        browser.close()

    print(f"wrote {OUT}")
    print(f"  {OUT.stat().st_size / 1024:.0f} KB")


main()
