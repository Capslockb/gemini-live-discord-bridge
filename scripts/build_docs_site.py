#!/usr/bin/env python3
"""Build the static docs site from `docs/*.md` into `docs-site/*.html`.

Single source of truth: the .md files. The output is one .html per .md,
plus a hand-written landing `index.html` (which lives outside the .md
source so the marketing is separate from the docs).

Usage:
    python3 scripts/build_docs_site.py
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
SITE_DIR = ROOT / "docs-site"
LICENSE_ISSUE_URL = (
    "https://github.com/Capslockb/gemini-live-discord-bridge/issues/7"
)

# nav order matters; matches the landing sidebar
NAV = [
    ("getting-started", "Getting started", [
        ("index.html", "Overview"),
        ("quickstart.html", "Quick start"),
        ("architecture.html", "Architecture"),
    ]),
    ("core-systems", "Core systems", [
        ("personality.html", "Conversational behavior"),
        ("fallback-chain.html", "Fallback chain"),
        ("notification.html", "Notifications"),
        ("email-brief.html", "Email brief"),
        ("sfx-library.html", "SFX library"),
        ("sfx-credits.html", "SFX credits"),
        ("webhooks.html", "Webhooks"),
        ("video.html", "Video feeder"),
    ]),
    ("reference", "Reference", [
        ("env-vars.html", "Environment variables"),
        ("troubleshooting.html", "Troubleshooting"),
        ("changelog.html", "Changelog"),
    ]),
]

# title overrides per .md (so the page title is human, not the file name)
PAGE_TITLES = {
    "architecture": "Architecture — Hermes Live",
    "personality": "Conversational behavior — Hermes Live",
    "fallback-chain": "Fallback chain — Hermes Live",
    "notification": "Notification system — Hermes Live",
    "email-brief": "Email brief — Hermes Live",
    "sfx-library": "SFX library — Hermes Live",
    "sfx-credits": "SFX credits — Hermes Live",
    "webhooks": "Webhooks — Hermes Live",
    "video": "Video frame feeder — Hermes Live",
    "env-vars": "Environment variables — Hermes Live",
    "troubleshooting": "Troubleshooting — Hermes Live",
    "changelog": "Changelog — Hermes Live",
    "quickstart": "Quick start — Hermes Live",
}

# Pager order = the same as NAV flattened, but with explicit prev/next.
ORDER = [
    "index.html",
    "quickstart.html",
    "architecture.html",
    "personality.html",
    "fallback-chain.html",
    "notification.html",
    "email-brief.html",
    "sfx-library.html",
    "sfx-credits.html",
    "webhooks.html",
    "video.html",
    "env-vars.html",
    "troubleshooting.html",
    "changelog.html",
]

# Page description for the <meta name="description"> tag.
# Keep descriptions public-facing: do not summarize private prompt text,
# identity strings, control grammar, authorization rules, or session data.
META_DESC = {
    "architecture": (
        "End-to-end audio path, threading model, and lifecycle of the "
        "Hermes Live Discord voice bridge."
    ),
    "personality": (
        "Public-safe conversational behavior, configuration guidance, and "
        "prompt-disclosure boundaries for Hermes Live."
    ),
    "fallback-chain": (
        "Delegation fallback behavior, health handling, portability limits, "
        "and rate-limit caveats."
    ),
    "notification": (
        "Notification delivery, scheduling, storage, and current sidecar "
        "limitations for Hermes Live."
    ),
    "email-brief": (
        "Email brief configuration and the current backend, delivery-state, "
        "recipient-routing, and privacy limitations."
    ),
    "sfx-library": (
        "Optional sound-effect slots, configuration, and runtime behavior for "
        "Hermes Live."
    ),
    "sfx-credits": (
        "Recorded provenance and unresolved redistribution-rights status for "
        "the bundled sound-effect files."
    ),
    "webhooks": "Webhook event classes, throttling, payload shape, and configuration.",
    "video": (
        "Frame-input design and the current feeder startup and authentication "
        "limitations."
    ),
    "env-vars": (
        "Hermes Live environment variables, defaults, and known path or "
        "runtime limitations."
    ),
    "troubleshooting": (
        "Operational diagnostics that distinguish current blockers from "
        "post-fix procedures."
    ),
    "changelog": (
        "Hermes Live repository history; current code and open issues remain "
        "the source of truth for present behavior."
    ),
    "quickstart": (
        "Installation and first-session setup with current installer, "
        "security, privacy, and licensing warnings."
    ),
}


# ─────────────────────────── markdown → HTML (small, GFM-ish) ────────────

def md_to_html(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    in_code = False
    code_buf: list[str] = []
    code_lang = ""

    def flush_code() -> None:
        nonlocal code_buf, code_lang
        if code_buf:
            body = html.escape("\n".join(code_buf))
            out.append(
                f'<pre><code class="lang-{html.escape(code_lang)}">{body}</code></pre>'
            )
        code_buf = []
        code_lang = ""

    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                in_code = True
                code_lang = line[3:].strip()
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        # tables (GFM: | a | b | followed by | - | - |)
        if (
            "|" in line
            and i + 1 < len(lines)
            and re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1])
        ):
            tbl: list[str] = [line]
            j = i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                tbl.append(lines[j])
                j += 1
            out.append(_render_table(tbl))
            i = j
            continue

        # headings
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            text = _inline(m.group(2).strip())
            out.append(f"<h{level}>{text}</h{level}>")
            i += 1
            continue

        # blockquote (collect contiguous > lines)
        if line.startswith(">"):
            block = []
            while i < len(lines) and lines[i].startswith(">"):
                block.append(lines[i].lstrip(">").lstrip())
                i += 1
            inner = _inline(" ".join(block))
            out.append(f"<blockquote><p>{inner}</p></blockquote>")
            continue

        # hr
        if re.match(r"^\s*---+\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        # unordered list
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(_inline(re.sub(r"^\s*[-*]\s+", "", lines[i])))
                i += 1
            out.append("<ul>" + "".join(f"<li>{it}</li>" for it in items) + "</ul>")
            continue

        # ordered list
        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(_inline(re.sub(r"^\s*\d+\.\s+", "", lines[i])))
                i += 1
            out.append("<ol>" + "".join(f"<li>{it}</li>" for it in items) + "</ol>")
            continue

        # blank
        if not line.strip():
            out.append("")
            i += 1
            continue

        # paragraph (collect contiguous non-empty, non-special lines)
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not _is_block(lines[i]):
            para.append(lines[i])
            i += 1
        out.append(f"<p>{_inline(' '.join(para))}</p>")

    if in_code:
        flush_code()
    return "\n".join(out)


def _is_block(line: str) -> bool:
    s = line.lstrip()
    return bool(
        s.startswith("#")
        or s.startswith("```")
        or s.startswith(">")
        or s.startswith("|")
        or s.startswith("---")
        or re.match(r"^[-*]\s+", s)
        or re.match(r"^\d+\.\s+", s)
    )


def _render_table(rows: list[str]) -> str:
    def split(row: str) -> list[str]:
        s = row.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip() for c in s.split("|")]

    header = split(rows[0])
    body = [split(r) for r in rows[1:]]
    h = "".join(f"<th>{_inline(c)}</th>" for c in header)
    b = "".join(
        "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
        for r in body
    )
    return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"


def _inline(s: str) -> str:
    # code first (to protect its content)
    s = re.sub(r"`([^`]+)`", lambda m: f"<code>{html.escape(m.group(1))}</code>", s)
    # bold **x**
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    # italic *x* or _x_
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])", r"<em>\1</em>", s)
    # links [text](url)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


# ─────────────────────────── page assembly ────────────────────────────

def render_page(slug: str, source_md_path: Path) -> str:
    md = source_md_path.read_text(encoding="utf-8")
    body = md_to_html(md)
    title = PAGE_TITLES.get(slug, f"{slug.title()} — Hermes Live")
    desc = META_DESC.get(slug, "Hermes Live — Discord voice bridge documentation.")

    prev, nxt = _pager_for(slug)

    sidebar = _render_sidebar(current=slug + ".html")
    topbar = _render_topbar(current=slug)
    pager = _render_pager(prev, nxt)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">
<link rel="stylesheet" href="style.css">
</head>
<body>
<div class="shell">

  <aside class="sidebar">
{sidebar}
  </aside>

  <main class="main">
{topbar}

    <article class="content">
{body}

{pager}

      <div class="foot">
        Hermes Live v0.3.4.2 · License pending owner decision · <a href="{LICENSE_ISSUE_URL}">Issue #7</a> · <a href="https://github.com/Capslockb/gemini-live-discord-bridge">github.com/Capslockb/gemini-live-discord-bridge</a>
      </div>

    </article>

    <aside class="toc" id="toc" aria-label="On this page"></aside>
  </main>

</div>
<script src="nav.js"></script>
</body>
</html>
"""


def _pager_for(slug: str) -> tuple[tuple[str, str] | None, tuple[str, str] | None]:
    fname = slug + ".html"
    if fname not in ORDER:
        return (None, None)
    idx = ORDER.index(fname)
    prev = ORDER[idx - 1] if idx > 0 else None
    nxt = ORDER[idx + 1] if idx < len(ORDER) - 1 else None

    def label(f: str) -> str:
        if f == "index.html":
            return "Overview"
        return PAGE_TITLES.get(
            f.replace(".html", ""),
            f.replace(".html", "").replace("-", " ").title(),
        )

    return ((prev, label(prev)) if prev else None, (nxt, label(nxt)) if nxt else None)


def _render_pager(prev, nxt) -> str:
    def cell(side, item):
        if not item:
            return f'<a class="{side}" style="visibility:hidden"></a>'
        f, lbl = item
        arrow = "←" if side == "prev" else "→"
        anchor = "Prev" if side == "prev" else "Next"
        return f'''<a class="{side}" href="{f}">
          <span class="label">{anchor} {arrow}</span>
          <span class="title">{html.escape(lbl)}</span>
        </a>'''

    return '<div class="pager">' + cell("prev", prev) + cell("next", nxt) + "</div>"


def _render_sidebar(current: str) -> str:
    out = ['''    <div class="brand">
      <div class="brand-mark">H</div>
      <div>
        <div class="brand-text">Hermes Live</div>
        <div class="brand-sub">v0.3.4 · VOPI build</div>
      </div>
    </div>

    <div class="search">
      <input id="search" type="search" placeholder="Search docs…" autocomplete="off">
    </div>

    <nav class="nav">''']
    for _, section, items in NAV:
        out.append(f'      <div class="nav-section">{html.escape(section)}</div>')
        for href, label in items:
            cls = ' class="active"' if href == current else ""
            out.append(f'      <a href="{href}"{cls}>{html.escape(label)}</a>')
    out.append(f'''    </nav>

    <div class="sidebar-foot">
      <a href="https://github.com/Capslockb/gemini-live-discord-bridge">GitHub →</a><br>
      License pending owner decision · <a href="{LICENSE_ISSUE_URL}">Issue #7</a>
    </div>''')
    return "\n".join(out)


def _render_topbar(current: str) -> str:
    crumb = current.replace("-", " ").replace(".html", "").title()
    if current == "index":
        crumb = "Overview"
    elif current == "personality":
        crumb = "Conversational behavior"
    return f'''    <div class="topbar">
      <span class="crumb">{html.escape(crumb)}</span>
      <span class="sep">/</span>
      <span>docs-site/</span>
      <div class="right">
        <span class="pill good">v0.3.4.2</span>
        <span class="pill warn">VOPI build</span>
      </div>
    </div>'''


# ─────────────────────────── entry ────────────────────────────

def main() -> int:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    (SITE_DIR / "style.css").write_text(
        (ROOT / "docs-site" / "style.css").read_text(), encoding="utf-8"
    )
    (SITE_DIR / "nav.js").write_text(
        (ROOT / "docs-site" / "nav.js").read_text(), encoding="utf-8"
    )

    slug_to_md = {
        "quickstart": "quickstart.md",
        "architecture": "architecture.md",
        "personality": "personality.md",
        "fallback-chain": "fallback-chain.md",
        "notification": "notification.md",
        "email-brief": "email-brief.md",
        "sfx-library": "sfx-library.md",
        "sfx-credits": "sfx-credits.md",
        "webhooks": "webhooks.md",
        "video": "video.md",
        "env-vars": "env-vars.md",
        "troubleshooting": "troubleshooting.md",
        "changelog": "../CHANGELOG.md",
    }

    # ensure quickstart source exists; create a conservative stub if not
    quickstart_src = DOCS_DIR / "quickstart.md"
    if not quickstart_src.exists():
        quickstart_src.write_text(_quickstart_stub(), encoding="utf-8")
        print("  + created stub: docs/quickstart.md", file=sys.stderr)

    for slug, md_name in slug_to_md.items():
        src = DOCS_DIR / md_name
        if not src.exists():
            print(f"  ! missing source: {src}", file=sys.stderr)
            continue
        out = SITE_DIR / f"{slug}.html"
        out.write_text(render_page(slug, src), encoding="utf-8")
        print(f"  + {out.relative_to(ROOT)}  ←  docs/{md_name}", file=sys.stderr)

    print("done.", file=sys.stderr)
    return 0


def _quickstart_stub() -> str:
    return """# Quick start

Review the current open issues and security/privacy limitations before installation.

## Install

```bash
# 1. Clone
git clone https://github.com/Capslockb/gemini-live-discord-bridge.git
cd gemini-live-discord-bridge

# 2. Review README.md, docs/README.md, and the open issues

# 3. Install
./install.sh

# 4. Restart the gateway
systemctl --user restart hermes-gateway
```

## Verify

```bash
curl -s http://127.0.0.1:18943/health | python3 -m json.tool
```

`/health` is anonymous and read-only. Keep the sidecar loopback-only. Mutating routes remain unavailable until Issue #5 is fixed and validated; `/notes` exposes stored note/transcript data without authentication.

## Current limitations

- Installer rerun and unattended-mode behavior is tracked in Issue #11.
- Mutating sidecar authentication is tracked in Issue #5.
- Bundled frame delivery is tracked in Issue #9.
- Email brief delivery and privacy behavior is tracked in Issue #10.
- Repository licensing is unresolved under Issue #7, and bundled media rights are separate under Issue #12.

## Next

- [Architecture](architecture.html)
- [Environment variables](env-vars.html)
- [Troubleshooting](troubleshooting.html)
"""


if __name__ == "__main__":
    raise SystemExit(main())
