"""Hook 1 — load vault session brief into Claude's context at SessionStart.

Wired as ``SessionStart`` in ``.claude/settings.json``.

Per the Claude Code hooks reference (``hooks.md``), anything a SessionStart
hook writes to stdout on exit 0 is added to Claude's context. We use this to
inject:

1. The content of ``docs/vault/context/ai-session-brief.md`` (operator-authored).
2. A short index of the most-recent ADRs (last 5 by ``created:``) — so the
   model knows what decisions are live without having to grep.

We also emit a soft warning to stderr if ``ai-session-brief.md`` looks stale
(``updated:`` field >14 days ago). Stderr from a SessionStart hook is shown
to the user but doesn't block the session.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cma.vault._telemetry import log_event

VAULT_ROOT = "docs/vault"
SESSION_BRIEF_REL = "context/ai-session-brief.md"
DECISIONS_REL = "decisions"
STALENESS_THRESHOLD = timedelta(days=14)
RECENT_ADR_LIMIT = 5

UPDATED_RE = re.compile(r"^updated:\s*(\S+)\s*$", re.MULTILINE)
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
CREATED_RE = re.compile(r"^created:\s*(\S+)\s*$", re.MULTILINE)
TITLE_RE = re.compile(r"^# (.+)$", re.MULTILINE)


def _parse_iso(s: str) -> datetime | None:
    """Parse an ISO date or datetime. Returns None on failure."""
    s = s.strip().strip('"').rstrip("Z")
    try:
        return datetime.fromisoformat(s).replace(tzinfo=UTC)
    except ValueError:
        return None


def _read_brief(path: Path) -> tuple[str, datetime | None]:
    """Return ``(content, updated_dt)``. Empty + None on read error."""
    if not path.is_file():
        return "", None
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", None
    updated = None
    fm = FRONTMATTER_RE.match(content)
    if fm:
        m = UPDATED_RE.search(fm.group(1))
        if m:
            updated = _parse_iso(m.group(1))
    return content, updated


def _recent_adrs(decisions_dir: Path, limit: int = RECENT_ADR_LIMIT) -> list[tuple[Path, str]]:
    """Return up to ``limit`` ADRs sorted by ``created:`` descending.

    Each entry is ``(path, title)`` where title is the first ``# ...`` heading.
    Skips ``_README.md`` and ``_INDEX.md``.
    """
    if not decisions_dir.is_dir():
        return []
    adrs: list[tuple[datetime, Path, str]] = []
    for md in decisions_dir.glob("*.md"):
        if md.name in ("_README.md", "_INDEX.md"):
            continue
        try:
            content = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm = FRONTMATTER_RE.match(content)
        if not fm:
            continue
        created_match = CREATED_RE.search(fm.group(1))
        created = _parse_iso(created_match.group(1)) if created_match else None
        if created is None:
            continue
        title_match = TITLE_RE.search(content[fm.end():])
        title = title_match.group(1).strip() if title_match else md.stem
        adrs.append((created, md, title))
    adrs.sort(key=lambda t: t[0], reverse=True)
    return [(p, title) for _, p, title in adrs[:limit]]


def build_context(cwd: Path) -> tuple[str, str | None]:
    """Build the ``(stdout_context, optional_stderr_warning)`` pair."""
    vault = cwd / VAULT_ROOT
    brief_path = vault / SESSION_BRIEF_REL
    decisions_dir = vault / DECISIONS_REL

    brief_text, updated = _read_brief(brief_path)
    if not brief_text:
        # Vault not set up — no context to inject. Silent.
        return "", None

    parts: list[str] = []
    parts.append("=" * 70)
    parts.append("VAULT SESSION BRIEF (auto-injected by SessionStart hook)")
    parts.append("=" * 70)
    parts.append("")
    parts.append(f"Source: {VAULT_ROOT}/{SESSION_BRIEF_REL}")
    parts.append("")
    parts.append(brief_text)
    parts.append("")
    parts.append("-" * 70)
    parts.append(f"RECENT ADRs (last {RECENT_ADR_LIMIT} by `created:`)")
    parts.append("-" * 70)
    parts.append("")

    recent = _recent_adrs(decisions_dir)
    if recent:
        for path, title in recent:
            parts.append(f"  • {VAULT_ROOT}/{DECISIONS_REL}/{path.name} — {title}")
    else:
        parts.append("  (no ADRs yet)")
    parts.append("")
    parts.append("=" * 70)

    # Staleness warning.
    warning: str | None = None
    if updated is not None:
        age = datetime.now(UTC) - updated
        if age > STALENESS_THRESHOLD:
            days = age.days
            warning = (
                f"[cma.vault] WARNING: {VAULT_ROOT}/{SESSION_BRIEF_REL} "
                f"`updated:` is {days} days old "
                f"(>{STALENESS_THRESHOLD.days}d threshold). "
                f"Operator: refresh when sprint focus changes."
            )

    return "\n".join(parts), warning


def main() -> int:
    import contextlib

    cwd = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    with contextlib.suppress(OSError):
        # Drain stdin (SessionStart envelope; we don't need its fields here).
        sys.stdin.read()

    context_text, warning = build_context(cwd)
    if context_text:
        print(context_text)
    if warning:
        print(warning, file=sys.stderr)
    log_event(
        hook="session_brief",
        outcome="emit" if context_text else "skip",
        stale=bool(warning),
        cwd=str(cwd),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
