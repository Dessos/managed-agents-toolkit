"""Frontmatter linter for vault markdown files.

Rules (each fires per-file):

- **V001** missing required frontmatter block — ERROR
- **V002** missing required field (``type`` / ``status`` / ``created`` /
  ``updated`` / ``tags`` / ``confidence`` / ``source``) — ERROR
- **V003** invalid ``type`` value (must be in the allowed enum) — ERROR
- **V004** invalid ``status`` value — ERROR
- **V005** invalid ``confidence`` value — ERROR
- **V006** malformed ``created`` / ``updated`` ISO timestamp — WARNING
- **V007** ``status: superseded`` without ``superseded_by:`` — ERROR
- **V008** ``type: knowledge`` with HTTP ``source:`` but no ``fetched_at:`` — ERROR
- **V009** ``type: decision`` and ``created:`` >90d ago without ``reviewed_on:`` — WARNING
- **V010** dropped legacy field present (``scope:`` / ``pillar:`` / ``body_path:``) — WARNING
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from cma.vault.scrape_anthropic_docs import FRONTMATTER_RE  # reuse

VALID_TYPES = frozenset(
    {"decision", "learning", "evaluation", "incident", "context", "governance", "knowledge", "meta"}
)
VALID_STATUSES = frozenset({"active", "superseded", "archived", "draft"})
VALID_CONFIDENCES = frozenset({"high", "medium", "low"})
REQUIRED_FIELDS = ("type", "status", "created", "updated", "tags", "confidence", "source")
LEGACY_FIELDS = ("scope", "pillar", "body_path")
ADR_REVIEW_AGE = timedelta(days=90)


class Severity(StrEnum):
    """Per-rule severity. Operator-tunable via :data:`RULE_SEVERITY`."""

    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


#: Default severity per rule. Operators / consumer projects can override.
RULE_SEVERITY: dict[str, Severity] = {
    "V001": Severity.ERROR,
    "V002": Severity.ERROR,
    "V003": Severity.ERROR,
    "V004": Severity.ERROR,
    "V005": Severity.ERROR,
    "V006": Severity.WARNING,
    "V007": Severity.ERROR,
    "V008": Severity.ERROR,
    "V009": Severity.WARNING,
    "V010": Severity.WARNING,
}


@dataclass(frozen=True)
class LintFinding:
    """One linter finding."""

    path: Path
    rule: str
    severity: Severity
    message: str


@dataclass
class LintReport:
    """Summary of a lint run."""

    findings: list[LintFinding] = field(default_factory=list)
    files_checked: int = 0

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity is Severity.ERROR)

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity is Severity.WARNING)


# --------------------------------------------------------------------------- #
# Frontmatter parsing
# --------------------------------------------------------------------------- #


_FIELD_RE = re.compile(r"^([a-zA-Z_][\w_]*)\s*:\s*(.*)$")


def parse_frontmatter(text: str) -> tuple[dict[str, str], bool]:
    """Return ``(field_map, had_frontmatter)``. Tolerant — only top-level scalars.

    The returned dict maps field name → raw value string (no YAML type
    coercion; the linter only checks shape, not deep structure).
    """
    fm = FRONTMATTER_RE.match(text)
    if not fm:
        return {}, False
    fields: dict[str, str] = {}
    for line in fm.group(1).splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        m = _FIELD_RE.match(line)
        if not m:
            continue
        fields[m.group(1)] = m.group(2).strip()
    return fields, True


def _parse_iso(raw: str) -> datetime | None:
    raw = raw.strip().strip('"').rstrip("Z")
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=UTC)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Rule checks
# --------------------------------------------------------------------------- #


def lint_file(path: Path, *, now: datetime | None = None) -> list[LintFinding]:
    """Lint one vault markdown file. Returns 0+ findings (empty = clean)."""
    findings: list[LintFinding] = []
    now = now or datetime.now(UTC)

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [_finding(path, "V001", f"unreadable: {exc}")]

    fields, had_fm = parse_frontmatter(text)
    if not had_fm:
        findings.append(_finding(path, "V001", "missing YAML frontmatter block"))
        return findings  # nothing else to check

    # V002 — required fields present
    for req in REQUIRED_FIELDS:
        if req not in fields:
            findings.append(_finding(path, "V002", f"missing required field '{req}'"))

    # V003 — type enum
    t = fields.get("type", "")
    if t and t not in VALID_TYPES:
        findings.append(_finding(path, "V003", f"invalid type {t!r} (must be one of: {sorted(VALID_TYPES)})"))

    # V004 — status enum
    s = fields.get("status", "")
    if s and s not in VALID_STATUSES:
        findings.append(_finding(path, "V004", f"invalid status {s!r} (must be one of: {sorted(VALID_STATUSES)})"))

    # V005 — confidence enum
    c = fields.get("confidence", "")
    if c and c not in VALID_CONFIDENCES:
        findings.append(_finding(path, "V005", f"invalid confidence {c!r} (must be one of: {sorted(VALID_CONFIDENCES)})"))

    # V006 — ISO timestamps parse-able
    for date_field in ("created", "updated"):
        raw = fields.get(date_field, "")
        if raw and _parse_iso(raw) is None:
            findings.append(_finding(path, "V006", f"{date_field!r} value {raw!r} does not parse as ISO 8601"))

    # V007 — superseded → must have superseded_by
    if fields.get("status") == "superseded" and not fields.get("superseded_by"):
        findings.append(_finding(path, "V007", "status: superseded requires superseded_by:"))

    # V008 — knowledge + HTTP source → must have fetched_at
    src = fields.get("source", "").strip('"').strip("'")
    if (
        fields.get("type") == "knowledge"
        and src.startswith(("http://", "https://"))
        and not fields.get("fetched_at")
    ):
        findings.append(_finding(path, "V008", "type: knowledge with HTTP source requires fetched_at:"))

    # V009 — old decision without reviewed_on
    if fields.get("type") == "decision":
        created = _parse_iso(fields.get("created", ""))
        if created is not None and (now - created) > ADR_REVIEW_AGE and not fields.get("reviewed_on"):
            findings.append(
                _finding(
                    path,
                    "V009",
                    f"decision is {(now - created).days}d old (>{ADR_REVIEW_AGE.days}d) — set reviewed_on: or status: superseded/archived",
                )
            )

    # V010 — legacy fields
    for legacy in LEGACY_FIELDS:
        if legacy in fields:
            findings.append(_finding(path, "V010", f"legacy field {legacy!r} present (dropped in plan v4)"))

    return findings


def _finding(path: Path, rule: str, message: str) -> LintFinding:
    sev = RULE_SEVERITY.get(rule, Severity.WARNING)
    return LintFinding(path=path, rule=rule, severity=sev, message=message)


# --------------------------------------------------------------------------- #
# Discovery + entry point
# --------------------------------------------------------------------------- #


def discover_vault_markdown(target: Path) -> list[Path]:
    """Return every ``.md`` under ``target`` (recursively).

    Skips ``_templates/`` (templates have placeholder syntax that doesn't lint
    cleanly by design — they're meant to be substituted before becoming notes).
    """
    if target.is_file():
        return [target]
    if not target.is_dir():
        return []
    out: list[Path] = []
    for p in target.rglob("*.md"):
        # Skip templates: they contain {{...}} placeholders, not real content.
        if "_templates" in p.parts:
            continue
        out.append(p)
    return sorted(out)


def lint_path(target: Path, *, now: datetime | None = None) -> LintReport:
    """Lint a file or directory. Returns a :class:`LintReport`."""
    report = LintReport()
    for md in discover_vault_markdown(target):
        report.files_checked += 1
        for f in lint_file(md, now=now):
            report.findings.append(f)
    return report
