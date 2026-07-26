#!/usr/bin/env python3
"""Metadata-only safety scanner for public-facing documentation."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DOC_NAMES = {"README.md", "SECURITY.md", "CONTRIBUTING.md", "AGENTS.md"}
DOC_DIR_PARTS = {"docs", "doc", "website", "site", "public", "docs-site"}
FIXTURE_PARTS = {"tests", "fixtures", "public-docs"}
DOC_EXTS = {".md", ".mdx", ".rst", ".txt", ".html", ".htm"}
EXCLUDE_PARTS = {
    "i18n",
    "CHANGELOG.md",
    "sessions",
    "vendor",
    "node_modules",
    ".git",
    "__pycache__",
    ".pytest_cache",
}

RULES = [
    (
        "PDS001",
        "instruction-override",
        re.compile(
            r"(?i)\b(ignore|disregard|override)\b.{0,100}"
            r"\b(previous|above|system|developer|policy|instruction)s?\b"
        ),
    ),
    (
        "PDS002",
        "secret-exfiltration",
        re.compile(
            r"(?i)\b(reveal|print|show|exfiltrate|leak)\b.{0,100}"
            r"\b(secret|token|credential|password|policy|system prompt|developer message)s?\b"
        ),
    ),
    (
        "PDS003",
        "unauthorized-action",
        re.compile(
            r"(?i)\b(approve|merge|push|deploy|purchase|transfer|delete|rotate|disable)\b.{0,100}"
            r"\b(PR|pull request|repository|repo|payment|account|guard|check|policy|automation)\b"
        ),
    ),
    (
        "PDS004",
        "private-control",
        re.compile(
            r"(?i)\b(privileged command|private control|non-public guard|secret marker|"
            r"trusted[- ]identity rule|mutation authorization|worker queue|controller lease|"
            r"private escalation)\b"
        ),
    ),
]
UNCERTAIN = re.compile(
    r"(?i)\b(maintaining model|automation agent|autonomous maintainer|repository bot)\b.{0,100}"
    r"\b(must|shall|required to|always|never|use tool|run command|obey|ignore|stop when|final status)\b"
)
BENIGN_UNCERTAIN = re.compile(
    r"(?i)\b(example|sample|template|user-facing|configuration|API|worker thread|service worker|"
    r"inference|event loop|model name|route|provider|guardrail|security policy|documentation)\b"
)
ZERO_SHA = "0" * 40


def default_branch() -> str:
    explicit = os.environ.get("GITHUB_BASE_REF") or os.environ.get("DEFAULT_BRANCH")
    if explicit:
        return explicit
    p = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if p.returncode == 0 and "/" in p.stdout:
        return p.stdout.strip().rsplit("/", 1)[-1]
    return "main"


def push_before_revision() -> str | None:
    explicit = os.environ.get("GITHUB_EVENT_BEFORE", "").strip()
    if explicit and explicit != ZERO_SHA:
        return explicit

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    try:
        before = str(json.loads(Path(event_path).read_text(encoding="utf-8")).get("before", "")).strip()
    except (OSError, ValueError, TypeError):
        return None
    return before if before and before != ZERO_SHA else None


def comparison_range() -> str:
    base_ref = os.environ.get("GITHUB_BASE_REF", "").strip()
    if base_ref:
        return f"origin/{base_ref}...HEAD"

    before = push_before_revision()
    if before:
        return f"{before}..HEAD"

    if os.environ.get("GITHUB_EVENT_NAME") == "push":
        raise RuntimeError("push event has no usable pre-push revision")

    return f"origin/{default_branch()}...HEAD"


def is_public_doc(path: str, include_fixtures: bool = False) -> bool:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return False
    parts = set(p.parts)
    if parts & EXCLUDE_PARTS:
        return False
    if include_fixtures and FIXTURE_PARTS <= parts and p.suffix.lower() in DOC_EXTS:
        return True
    return p.name in DOC_NAMES or (
        p.suffix.lower() in DOC_EXTS and bool(parts & DOC_DIR_PARTS)
    )


def changed_files() -> list[str]:
    diff_range = comparison_range()
    p = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMRT", diff_range],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if p.returncode == 0:
        return p.stdout.splitlines()

    if os.environ.get("GITHUB_EVENT_NAME"):
        raise RuntimeError("unable to calculate GitHub event comparison")

    p = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMRT", "--cached"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if p.returncode == 0:
        return p.stdout.splitlines()
    return []


def changed_added_lines(files: list[str]) -> dict[str, set[int]] | None:
    if not files:
        return {}
    p = subprocess.run(
        [
            "git",
            "diff",
            "--unified=0",
            "--diff-filter=ACMRT",
            comparison_range(),
            "--",
            *files,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if p.returncode != 0:
        if os.environ.get("GITHUB_EVENT_NAME"):
            raise RuntimeError("unable to calculate added lines for GitHub event")
        return None

    out: dict[str, set[int]] = {}
    cur = None
    new_line = None
    for line in p.stdout.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
            out.setdefault(cur, set())
        elif line.startswith("@@") and cur:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                new_line = int(m.group(1))
        elif cur and new_line is not None:
            if line.startswith("+") and not line.startswith("+++"):
                out.setdefault(cur, set()).add(new_line)
                new_line += 1
            elif not line.startswith("-"):
                new_line += 1
    return out


def classify_text(text: str) -> list[tuple[str, str]]:
    matches = []
    for rule_id, category, rx in RULES:
        if rx.search(text):
            matches.append((rule_id, category))

    human_guidance = bool(
        re.search(
            r"(?i)(contributor'?s? (PR|pull request)|merge via (github|the )|"
            r"so they get credit|always merge|never close a contributor)",
            text,
        )
    )
    if (
        UNCERTAIN.search(text)
        and not BENIGN_UNCERTAIN.search(text)
        and not human_guidance
    ):
        matches.append(("PDS005", "automation-instruction"))
    return matches


def scan_file(path: str, line_numbers) -> list[tuple[str, int, str, str]]:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return [(path, 1, "PDS_READ_ERROR", "read-error")]

    selected = {i for i in line_numbers if 1 <= i <= len(lines)}
    findings: set[tuple[str, int, str, str]] = set()
    single_matches: dict[int, set[tuple[str, str]]] = {}

    for i in sorted(selected):
        matches = set(classify_text(lines[i - 1]))
        single_matches[i] = matches
        for rule_id, category in matches:
            findings.add((path, i, rule_id, category))

    pair_starts = set()
    for i in selected:
        if i > 1:
            pair_starts.add(i - 1)
        if i < len(lines):
            pair_starts.add(i)

    for start in sorted(pair_starts):
        end = start + 1
        selected_in_pair = sorted(selected & {start, end})
        if not selected_in_pair:
            continue
        text = f"{lines[start - 1]} {lines[end - 1]}"
        for rule_id, category in classify_text(text):
            if (rule_id, category) in single_matches.get(start, set()):
                continue
            if (rule_id, category) in single_matches.get(end, set()):
                continue
            findings.add((path, selected_in_pair[0], rule_id, category))

    return sorted(findings, key=lambda item: (item[0], item[1], item[2], item[3]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--include-test-fixtures", action="store_true")
    args = ap.parse_args()

    try:
        include_fixtures = args.include_test_fixtures or args.all
        candidates = (
            [str(x) for x in Path(".").rglob("*") if x.is_file()]
            if args.all
            else changed_files()
        )
        files = [f for f in candidates if is_public_doc(f, include_fixtures)]
        added = None if args.all else changed_added_lines(files)
    except RuntimeError:
        print("public-docs-safety: ERROR")
        print(".:0:PDS_COMPARE_ERROR:comparison")
        return 2

    findings = []
    for f in files:
        if added is None:
            try:
                line_numbers = range(
                    1,
                    len(
                        Path(f)
                        .read_text(encoding="utf-8", errors="ignore")
                        .splitlines()
                    )
                    + 1,
                )
            except Exception:
                line_numbers = [1]
        else:
            line_numbers = sorted(added.get(f, set()))
        findings.extend(scan_file(f, line_numbers))

    if findings:
        print("public-docs-safety: FAIL")
        for f, i, rule_id, category in findings:
            print(f"{f}:{i}:{rule_id}:{category}")
        return 1
    print("public-docs-safety: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
