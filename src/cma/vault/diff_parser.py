"""Shared CHANGELOG-diff parser for the vault enforcement hooks.

The PreToolUse hook on ``git commit`` (``cma.vault.enforce_changelog``) needs
to know which "Decisions baked in" bullets are *new* in the staged diff vs.
already-present in the prior CHANGELOG. The PostToolUse nudge hook
(``cma.vault.check_adr_section_drift``) needs the inverse: which ADR files were
added/modified in the just-completed commit, and what frontmatter tags they
carry.

This module centralizes those parsing primitives so both hooks (and their
tests) draw from one well-tested place.

Pure stdlib. No git invocation here — callers supply the diff text + (optional)
baseline text. That makes unit testing trivial (no tmp git repos required).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# Regexes
# --------------------------------------------------------------------------- #

#: Matches the start of a ``### Decisions baked in`` heading line.
#: Tolerates trailing whitespace; the marker is verbatim, the bar is uniform.
DECISIONS_HEADING_RE = re.compile(r"^### Decisions baked in\s*$")

#: Matches a "decision bullet": ``- **<bolded title>** ...``. The bolded title
#: is what we use to identify a decision; the prose body after `**` may span
#: lines but doesn't matter for identification.
DECISION_BULLET_RE = re.compile(r"^- \*\*([^*]+)\*\*")

#: Diff line starting with ``+`` (added) but NOT ``+++`` (file header).
ADDED_LINE_RE = re.compile(r"^\+(?!\+\+)(.*)$")

#: Diff line starting with ``-`` (removed) but NOT ``---`` (file header).
REMOVED_LINE_RE = re.compile(r"^-(?!--)(.*)$")

#: Diff line starting with `@@` (hunk header). Used to detect file boundaries
#: when parsing multi-file diffs.
HUNK_HEADER_RE = re.compile(r"^@@ ")

#: ``diff --git a/<path> b/<path>`` line — start of a per-file section.
DIFF_FILE_HEADER_RE = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)\s*$")


# --------------------------------------------------------------------------- #
# Public dataclasses
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DecisionBullet:
    """One decision bullet from a CHANGELOG ``### Decisions baked in`` section."""

    #: The bolded title (between ``**``).
    title: str
    #: The full first line of the bullet (including the ``- ** ... **`` prefix).
    first_line: str


@dataclass(frozen=True)
class ChangelogDiffResult:
    """What ``parse_changelog_diff`` decided about a staged diff.

    ``new_bullets`` is the list that Hook 2 enforces against — every entry
    requires a parallel ADR file. ``removed_bullets`` lets Hook 2 allow
    revert-style commits unconditionally (removing bullets is never gated).
    """

    new_bullets: list[DecisionBullet]
    removed_bullets: list[DecisionBullet]

    @property
    def is_revert(self) -> bool:
        """True if the diff removes bullets without adding any.

        Reverts are unconditionally allowed by Hook 2 — they undo prior
        decisions and shouldn't be blocked on adding new ADRs.
        """
        return not self.new_bullets and bool(self.removed_bullets)


# --------------------------------------------------------------------------- #
# CHANGELOG bullet parsing (from raw markdown, not from a diff)
# --------------------------------------------------------------------------- #


def extract_decision_bullets(text: str) -> list[DecisionBullet]:
    """Return every decision bullet found under ANY ``### Decisions baked in``.

    Tolerates multiple ``### Decisions baked in`` sections in one file (which
    happens in CHANGELOG: each release can have its own). Stops collecting at
    the next ``###`` or ``##`` heading (any sibling/parent section ends the
    bullet list).
    """
    bullets: list[DecisionBullet] = []
    in_section = False
    for line in text.splitlines():
        if DECISIONS_HEADING_RE.match(line):
            in_section = True
            continue
        if in_section and re.match(r"^##+ ", line):
            # Next heading at any level closes the bullet block.
            in_section = False
            continue
        if not in_section:
            continue
        m = DECISION_BULLET_RE.match(line)
        if m:
            bullets.append(DecisionBullet(title=m.group(1).strip(), first_line=line))
    return bullets


# --------------------------------------------------------------------------- #
# CHANGELOG diff parsing
# --------------------------------------------------------------------------- #


def parse_changelog_diff(
    diff_text: str, *, baseline_text: str | None = None
) -> ChangelogDiffResult:
    """Identify new + removed ``### Decisions baked in`` bullets in a diff.

    Args:
        diff_text: Output of ``git diff --cached CHANGELOG.md`` (or
            ``git diff HEAD~1 CHANGELOG.md`` for amends). Must be in unified
            diff format.
        baseline_text: Optional pre-diff content of CHANGELOG (HEAD or HEAD~1).
            If supplied, we deduplicate against pre-existing bullets — a
            "new" added bullet whose title already exists in the baseline is
            treated as an edit (not new). If omitted, every added-bullet line
            in the diff counts as new.

    Returns:
        :class:`ChangelogDiffResult` with new + removed bullet lists.

    Notes:
        The unified-diff format means an "edit" to a bullet appears as a
        removed line + an added line. Without baseline_text we can't
        distinguish edit (- A; + A') from add-after-remove of an unrelated
        line. WITH baseline_text we check: is the added bullet's title
        present in the baseline? If yes → edit (not new). If no → new.
    """
    # Step 1: enumerate added + removed lines that look like decision bullets
    # AND fall under a ``### Decisions baked in`` heading. The diff's text is
    # interleaved (additions + removals + context); we have to reconstruct
    # heading-context as we walk.
    added_bullets: list[DecisionBullet] = []
    removed_bullets: list[DecisionBullet] = []

    in_decisions_section = False
    for line in diff_text.splitlines():
        if line.startswith("diff --git ") or line.startswith("@@ "):
            # File header or hunk header — reset section context. Heading
            # detection within the hunk is what matters; cross-hunk state
            # would mis-fire on multi-section diffs.
            in_decisions_section = False
            continue
        if line.startswith("---") or line.startswith("+++"):
            continue

        # Strip the diff prefix (`+`, `-`, ` `) to get the "content" line.
        # File headers (``---`` / ``+++``) were already skipped above; here we
        # only need to distinguish single-char diff prefixes from content. A
        # legitimate removed line CAN start with ``-`` in its content (e.g.,
        # a markdown bullet ``- **X**``), so we MUST NOT exclude those — only
        # exclude exact 3-char file headers, which we already did.
        if line.startswith("+"):
            content = line[1:]
            is_added = True
            is_removed = False
        elif line.startswith("-"):
            content = line[1:]
            is_added = False
            is_removed = True
        else:
            # Context line — but for context detection we still want to
            # follow heading transitions inside the section.
            content = line[1:] if line.startswith(" ") else line
            is_added = False
            is_removed = False

        # Update the section flag based on EITHER context lines or added/removed
        # lines (the heading itself can be either, when CHANGELOG is being
        # introduced for the first time or restructured).
        if DECISIONS_HEADING_RE.match(content):
            in_decisions_section = True
            continue
        if re.match(r"^##+ ", content) and not DECISIONS_HEADING_RE.match(content):
            in_decisions_section = False
            continue

        if not in_decisions_section:
            continue

        m = DECISION_BULLET_RE.match(content)
        if not m:
            continue
        bullet = DecisionBullet(title=m.group(1).strip(), first_line=content)
        if is_added:
            added_bullets.append(bullet)
        elif is_removed:
            removed_bullets.append(bullet)

    # Step 2: deduplicate "edited bullets" if we have a baseline. An added
    # bullet whose title is already in the baseline is treated as an edit.
    if baseline_text is not None:
        baseline_titles = {b.title for b in extract_decision_bullets(baseline_text)}
        new_bullets = [b for b in added_bullets if b.title not in baseline_titles]
    else:
        new_bullets = list(added_bullets)

    return ChangelogDiffResult(new_bullets=new_bullets, removed_bullets=removed_bullets)


# --------------------------------------------------------------------------- #
# Staged-files helpers (these DO invoke git; isolated for easy mocking)
# --------------------------------------------------------------------------- #


def staged_files(
    *,
    cwd: Path | None = None,
    head_ref: str = "HEAD",
) -> list[str]:
    """Return paths (relative to repo root) that are staged for the next commit.

    Equivalent to ``git diff --cached --name-only``. Returns an empty list on
    git failure or if the cwd is not a git repo (caller decides what to do).
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.decode("utf-8", "replace").splitlines() if line]


def staged_changelog_diff(
    *,
    changelog_path: str = "CHANGELOG.md",
    cwd: Path | None = None,
    against_ref: str = "HEAD",
) -> str:
    """Return ``git diff <against_ref> -- CHANGELOG.md`` as a string.

    For a fresh commit this should be invoked with ``against_ref="HEAD"`` and
    ``--cached`` semantics. For an ``--amend`` commit, callers pass
    ``against_ref="HEAD~1"`` (the pre-amend baseline).
    """
    args = ["git", "diff", "--cached", against_ref, "--", changelog_path]
    if against_ref == "HEAD":
        # Default: --cached against HEAD is the standard "what's staged".
        args = ["git", "diff", "--cached", "--", changelog_path]
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", "replace")


def file_content_at_ref(
    path: str, *, ref: str = "HEAD", cwd: Path | None = None
) -> str | None:
    """Return the content of ``path`` at git ``ref``, or None if not present.

    Used to fetch the pre-commit baseline of CHANGELOG.md for
    :func:`parse_changelog_diff`'s ``baseline_text`` argument.
    """
    try:
        result = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", "replace")


def detect_is_amend(*, cwd: Path | None = None) -> bool:
    """True if the staged commit appears to be a ``--amend`` of HEAD.

    Heuristic: a true amend's staged tree differs from HEAD~1's tree more
    than from HEAD's tree. The cheap probe is: is the staged commit being
    composed on top of HEAD's parent (i.e., is HEAD itself going to be
    replaced)?

    Claude Code's git tooling does not surface ``--amend`` directly in the
    hook input JSON, so we infer from environment. The most reliable signal
    is the ``GIT_REFLOG_ACTION`` env var (set by ``git commit --amend`` to
    a string containing ``amend``); we fall back to detecting an empty
    diff between HEAD~1 and HEAD (the prior commit was tiny / empty).
    """
    import os

    # Fallback: no reliable env signal beyond GIT_REFLOG_ACTION. The hook
    # script can pass the staged message via stdin if Claude Code starts
    # surfacing --amend explicitly in a future API version.
    return "amend" in os.environ.get("GIT_REFLOG_ACTION", "").lower()
