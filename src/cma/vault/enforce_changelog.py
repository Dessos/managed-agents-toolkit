"""Hook 2 — block ``git commit`` if CHANGELOG adds a 'Decisions baked in' bullet without a parallel ADR file.

Wired as ``PreToolUse[Bash if Bash(git commit *)]`` in ``.claude/settings.json``.

Spec (from plan v4.1):

1. Compute the staged diff for ``CHANGELOG.md``. If this is a ``--amend``,
   use ``HEAD~1`` as the baseline; else ``HEAD``.
2. Parse for new bullet entries under any ``### Decisions baked in`` heading.
   A "new bullet" is a line matching ``^\\+- \\*\\*<title>\\*\\*`` whose bolded
   title is absent from the pre-commit CHANGELOG (so edits / rewordings
   don't count as new).
3. Aggregate across multiple ``### Decisions baked in`` sections.
4. Reverts (removing bullets without adding any) are unconditionally allowed.
5. For each new-bullet decision, require ≥ 1 staged file matching
   ``docs/vault/decisions/*.md``. One ADR may cover multiple bullets if its
   body cites each title explicitly (we don't enforce 1:1 by parsing the
   ADR — that would be over-fitting; the operator's discipline is to make
   the bullet ↔ ADR mapping clear).
6. **Exit code 2 + stderr** when violated (this blocks the commit per the
   Claude Code hooks contract).
7. Bypass via ``CMA_VAULT_BYPASS=1`` (master) or ``CMA_VAULT_COMMIT_BYPASS=1``
   (this hook only). Every bypass emits a telemetry JSONL line.

The hook is invoked as ``python -m cma.vault.enforce_changelog`` from
``.claude/settings.json``. It receives the standard PreToolUse JSON envelope
on stdin (``session_id``, ``tool_input.command``, etc.) but for our purposes
only needs the working directory (which Claude Code sets to ``cwd``).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from cma.vault import diff_parser
from cma.vault._telemetry import log_event

BYPASS_MASTER_ENV = "CMA_VAULT_BYPASS"
BYPASS_COMMIT_ENV = "CMA_VAULT_COMMIT_BYPASS"

ADR_DIR = "docs/vault/decisions"
CHANGELOG_PATH = "CHANGELOG.md"


def _read_hook_input() -> dict:
    """Read the PreToolUse JSON envelope from stdin. Tolerates empty input."""
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _bypass_active() -> str | None:
    """Return the name of the bypass env var that's active, or None."""
    for var in (BYPASS_MASTER_ENV, BYPASS_COMMIT_ENV):
        if os.environ.get(var):
            return var
    return None


def _staged_adr_paths(cwd: Path) -> list[str]:
    """All staged files under ``docs/vault/decisions/`` ending in ``.md``."""
    all_staged = diff_parser.staged_files(cwd=cwd)
    return [
        p
        for p in all_staged
        if p.replace("\\", "/").startswith(f"{ADR_DIR}/") and p.endswith(".md")
        and Path(p).name not in ("_README.md", "_INDEX.md")
    ]


def evaluate(cwd: Path) -> tuple[int, str]:
    """Return ``(exit_code, stderr_message)``.

    Exit code 0 → allow commit. Exit code 2 → block. Other codes reserved.
    """
    # Compute the diff. Amend handling: GIT_REFLOG_ACTION contains 'amend'.
    is_amend = diff_parser.detect_is_amend(cwd=cwd)
    against_ref = "HEAD~1" if is_amend else "HEAD"

    diff_text = diff_parser.staged_changelog_diff(
        changelog_path=CHANGELOG_PATH, cwd=cwd, against_ref=against_ref
    )
    if not diff_text:
        # Either CHANGELOG isn't staged or there's no git here. Either way,
        # we have nothing to enforce; allow.
        return 0, ""

    baseline = diff_parser.file_content_at_ref(CHANGELOG_PATH, ref=against_ref, cwd=cwd)
    result = diff_parser.parse_changelog_diff(diff_text, baseline_text=baseline)

    # Reverts are unconditionally allowed.
    if result.is_revert:
        return 0, ""

    # No new decisions = nothing to enforce.
    if not result.new_bullets:
        return 0, ""

    # New decisions present — require at least one staged ADR.
    staged_adrs = _staged_adr_paths(cwd)
    if staged_adrs:
        return 0, ""

    # Violation. Build a clear error message.
    bullets_listed = "\n".join(f"  • {b.title}" for b in result.new_bullets)
    msg = (
        "Blocked by cma.vault.enforce_changelog (Hook 2 — anti-decay).\n\n"
        f"This commit adds {len(result.new_bullets)} new "
        f"'### Decisions baked in' bullet(s) to CHANGELOG.md but does NOT\n"
        f"stage a parallel ADR file under {ADR_DIR}/.\n\n"
        f"New decisions detected:\n{bullets_listed}\n\n"
        "Fix one of these ways:\n"
        f"  1. Write the ADR:  cma vault new-decision <kebab-slug>\n"
        "     (then stage it and re-commit)\n"
        "  2. Remove the bullet(s) from CHANGELOG if they don't warrant ADRs\n"
        "  3. Emergency bypass (logged):\n"
        f"     $env:{BYPASS_COMMIT_ENV} = '1'  # PowerShell\n"
        f"     export {BYPASS_COMMIT_ENV}=1     # bash/zsh\n"
        f"     # or master bypass: {BYPASS_MASTER_ENV}=1\n"
    )
    return 2, msg


def main() -> int:
    """Entry point invoked by the PreToolUse hook."""
    _ = _read_hook_input()  # input not used for decisions but read to drain stdin

    # Bypass gate FIRST — even bypass usage is logged so we can see frequency.
    bypass_var = _bypass_active()
    cwd = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))

    if bypass_var:
        log_event(
            hook="enforce_changelog",
            outcome="bypass",
            bypass_var=bypass_var,
            cwd=str(cwd),
        )
        return 0

    exit_code, msg = evaluate(cwd)

    log_event(
        hook="enforce_changelog",
        outcome="block" if exit_code == 2 else "allow",
        cwd=str(cwd),
    )

    if exit_code == 2:
        print(msg, file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
