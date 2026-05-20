"""Hook 3 — nudge to update ``manage_adr`` when committed ADR touches a section.

Wired as ``PostToolUse[Bash if Bash(git commit *)]`` in ``.claude/settings.json``.

This hook is a SOFT NUDGE — it never blocks. PostToolUse hooks fire after the
tool succeeds; nothing can be undone. The right response is a clear stderr
reminder so the operator (or agent in a follow-up turn) updates the
``manage_adr`` summary when warranted.

Spec:
1. Inspect ``git diff HEAD~1`` for added/modified ``docs/vault/decisions/*.md``.
2. For each touched ADR, parse frontmatter ``tags:`` for one of the
   manage_adr canonical section names: ``architecture``, ``stack``,
   ``patterns``, ``tradeoffs``, ``philosophy``, ``purpose``.
3. If any match, write one line to stderr per touched ADR naming the
   section(s) and the suggested ``manage_adr`` invocation.
4. Never blocks; never returns non-zero.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
from pathlib import Path

from cma.vault._telemetry import log_event

ADR_DIR_PREFIX = "docs/vault/decisions/"

#: Tag-to-section mapping. ``manage_adr`` has 6 canonical sections; any of
#: these tags on an ADR's frontmatter signals it may shift the summary.
TAG_TO_SECTION: dict[str, str] = {
    "architecture": "ARCHITECTURE",
    "stack": "STACK",
    "patterns": "PATTERNS",
    "tradeoffs": "TRADEOFFS",
    "trade-off": "TRADEOFFS",
    "trade-offs": "TRADEOFFS",
    "philosophy": "PHILOSOPHY",
    "purpose": "PURPOSE",
}

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
TAGS_RE = re.compile(r"^tags:\s*\[(.*?)\]", re.MULTILINE)


def committed_adrs(cwd: Path) -> list[str]:
    """Return ADR paths added/modified in the most recent commit (HEAD~1..HEAD)."""
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD~1", "HEAD", "--name-only", "--diff-filter=AM"],
            cwd=str(cwd),
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    files = result.stdout.decode("utf-8", "replace").splitlines()
    return [
        f
        for f in files
        if f.replace("\\", "/").startswith(ADR_DIR_PREFIX) and f.endswith(".md")
        and Path(f).name not in ("_README.md", "_INDEX.md")
    ]


def adr_sections_touched(adr_path: Path) -> set[str]:
    """Read an ADR's frontmatter and return the manage_adr sections it may touch."""
    if not adr_path.is_file():
        return set()
    try:
        content = adr_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    fm = FRONTMATTER_RE.match(content)
    if not fm:
        return set()
    tag_match = TAGS_RE.search(fm.group(1))
    if not tag_match:
        return set()
    tags = [t.strip().lower() for t in tag_match.group(1).split(",")]
    sections: set[str] = set()
    for tag in tags:
        if tag in TAG_TO_SECTION:
            sections.add(TAG_TO_SECTION[tag])
    return sections


def main() -> int:
    """Entry point. Reads + discards stdin (PostToolUse envelope; not needed)."""
    with contextlib.suppress(OSError):
        sys.stdin.read()

    cwd = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    adrs = committed_adrs(cwd)
    if not adrs:
        log_event(hook="check_adr_section_drift", outcome="skip", reason="no_adrs_touched")
        return 0

    nudged: list[tuple[str, set[str]]] = []
    for adr in adrs:
        sections = adr_sections_touched(cwd / adr)
        if sections:
            nudged.append((adr, sections))

    if not nudged:
        log_event(
            hook="check_adr_section_drift",
            outcome="skip",
            reason="no_section_tags",
            adrs_count=len(adrs),
        )
        return 0

    print(
        f"[cma.vault] {len(nudged)} ADR(s) committed may shift manage_adr summary:",
        file=sys.stderr,
    )
    for adr, sections in nudged:
        sects = ", ".join(sorted(sections))
        print(f"  • {adr} → consider updating manage_adr section(s): {sects}", file=sys.stderr)
    print(
        "  Use: mcp__codebase-memory-mcp__manage_adr mode='update' sections={...}",
        file=sys.stderr,
    )
    log_event(
        hook="check_adr_section_drift",
        outcome="nudge",
        nudged_count=len(nudged),
        adrs=[a for a, _ in nudged],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
