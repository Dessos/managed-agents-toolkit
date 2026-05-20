"""Create a new vault note from a template with frontmatter substitution.

Used by ``cma vault new-decision <slug>`` and siblings
(``new-learning``, ``new-evaluation``, ``new-incident``).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

#: Per-type defaults: template filename + target subfolder.
NOTE_KINDS: dict[str, tuple[str, str]] = {
    "decision": ("decision.md", "decisions"),
    "learning": ("learning.md", "learnings"),
    "evaluation": ("evaluation.md", "evaluations"),
    "incident": ("incident.md", "incidents"),
}


@dataclass
class NewNoteResult:
    """Result of a new-note operation."""

    path: Path
    created: bool
    opened_editor: bool


def _slugify(text: str) -> str:
    """Conservative slug — lowercase, alnum + dash only."""
    out: list[str] = []
    prev_dash = False
    for ch in text.lower().strip():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif ch in (" ", "-", "_", "."):
            if not prev_dash:
                out.append("-")
                prev_dash = True
    return "".join(out).strip("-")


def make_note(
    *,
    kind: str,
    slug: str,
    vault_root: Path,
    title: str | None = None,
    source: str = "",
    force: bool = False,
    open_editor: bool = True,
    now: datetime | None = None,
) -> NewNoteResult:
    """Create ``<kind>s/<YYYY-MM-DD>-<slug>.md`` from the template.

    Raises ``ValueError`` for unknown ``kind`` or invalid slug, and
    ``FileExistsError`` when the target exists and ``force=False``.
    """
    if kind not in NOTE_KINDS:
        raise ValueError(f"unknown note kind {kind!r}; choose from {sorted(NOTE_KINDS)}")

    clean_slug = _slugify(slug)
    if not clean_slug:
        raise ValueError(f"slug {slug!r} reduced to empty after normalization")

    now = now or datetime.now(UTC)
    template_name, subfolder = NOTE_KINDS[kind]
    template_path = vault_root / "_templates" / template_name
    if not template_path.is_file():
        raise FileNotFoundError(f"template not found: {template_path}")

    target_dir = vault_root / subfolder
    target_dir.mkdir(parents=True, exist_ok=True)
    target_filename = f"{now.strftime('%Y-%m-%d')}-{clean_slug}.md"
    target_path = target_dir / target_filename

    if target_path.exists() and not force:
        raise FileExistsError(
            f"already exists: {target_path}\n"
            f"Pass force=True (CLI: --force) to overwrite."
        )

    template_text = template_path.read_text(encoding="utf-8")
    substituted = (
        template_text.replace("{{CREATED}}", now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        .replace("{{UPDATED}}", now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        .replace("{{TITLE}}", title or clean_slug.replace("-", " ").title())
        .replace("{{SOURCE}}", source or f"conversation date {now.strftime('%Y-%m-%d')}")
        .replace("{{REVIEW_DATE}}", (now.replace(year=now.year + 1)).strftime("%Y-%m-%d"))
    )

    target_path.write_text(substituted, encoding="utf-8")

    opened = False
    if open_editor:
        opened = _maybe_open_editor(target_path)

    return NewNoteResult(path=target_path, created=True, opened_editor=opened)


def _maybe_open_editor(path: Path) -> bool:
    """Open ``$EDITOR`` if set; return True on success, False otherwise.

    Non-blocking on failure — this is a convenience, not a contract.
    """
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor:
        return False
    try:
        subprocess.Popen([editor, str(path)])
    except (FileNotFoundError, OSError):
        return False
    return True
