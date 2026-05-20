"""Unit tests for ``cma.vault.lint`` — frontmatter validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from cma.vault import lint


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# parse_frontmatter
# --------------------------------------------------------------------------- #


class TestParseFrontmatter:
    def test_well_formed(self) -> None:
        text = "---\ntype: decision\nstatus: active\n---\nbody\n"
        fields, had = lint.parse_frontmatter(text)
        assert had is True
        assert fields == {"type": "decision", "status": "active"}

    def test_no_frontmatter(self) -> None:
        fields, had = lint.parse_frontmatter("just body\n")
        assert had is False
        assert fields == {}

    def test_tolerates_comments_in_frontmatter(self) -> None:
        text = "---\ntype: decision\n# this is a comment\nstatus: active\n---\n"
        fields, _ = lint.parse_frontmatter(text)
        assert "type" in fields and "status" in fields


# --------------------------------------------------------------------------- #
# Rule firing
# --------------------------------------------------------------------------- #


class TestRules:
    def test_v001_no_frontmatter(self, tmp_path: Path) -> None:
        p = _write(tmp_path / "x.md", "no frontmatter")
        findings = lint.lint_file(p)
        rules = {f.rule for f in findings}
        assert "V001" in rules

    def test_v002_missing_fields(self, tmp_path: Path) -> None:
        p = _write(tmp_path / "x.md", "---\ntype: decision\n---\nbody\n")
        findings = lint.lint_file(p)
        # Should report missing status, created, updated, tags, confidence, source.
        v002 = [f for f in findings if f.rule == "V002"]
        assert len(v002) >= 5

    def test_v003_invalid_type(self, tmp_path: Path) -> None:
        p = _write(tmp_path / "x.md", _full_frontmatter(type_="invalid-type"))
        rules = {f.rule for f in lint.lint_file(p)}
        assert "V003" in rules

    def test_v004_invalid_status(self, tmp_path: Path) -> None:
        p = _write(tmp_path / "x.md", _full_frontmatter(status="bogus"))
        rules = {f.rule for f in lint.lint_file(p)}
        assert "V004" in rules

    def test_v005_invalid_confidence(self, tmp_path: Path) -> None:
        p = _write(tmp_path / "x.md", _full_frontmatter(confidence="very-high"))
        rules = {f.rule for f in lint.lint_file(p)}
        assert "V005" in rules

    def test_v006_malformed_date(self, tmp_path: Path) -> None:
        p = _write(tmp_path / "x.md", _full_frontmatter(created="yesterday"))
        rules = {f.rule for f in lint.lint_file(p)}
        assert "V006" in rules

    def test_v007_superseded_without_superseded_by(self, tmp_path: Path) -> None:
        p = _write(tmp_path / "x.md", _full_frontmatter(status="superseded"))
        rules = {f.rule for f in lint.lint_file(p)}
        assert "V007" in rules

    def test_v007_clean_when_pair_present(self, tmp_path: Path) -> None:
        extra = "superseded_by: other.md\n"
        p = _write(
            tmp_path / "x.md", _full_frontmatter(status="superseded", extra=extra)
        )
        rules = {f.rule for f in lint.lint_file(p)}
        assert "V007" not in rules

    def test_v008_knowledge_http_without_fetched_at(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path / "x.md",
            _full_frontmatter(type_="knowledge", source='"https://example.com/x.md"'),
        )
        rules = {f.rule for f in lint.lint_file(p)}
        assert "V008" in rules

    def test_v008_clean_when_fetched_at_present(self, tmp_path: Path) -> None:
        extra = "fetched_at: 2026-05-20T00:00:00Z\n"
        p = _write(
            tmp_path / "x.md",
            _full_frontmatter(
                type_="knowledge",
                source='"https://example.com/x.md"',
                extra=extra,
            ),
        )
        rules = {f.rule for f in lint.lint_file(p)}
        assert "V008" not in rules

    def test_v009_old_decision_warns(self, tmp_path: Path) -> None:
        old_date = (datetime.now(UTC) - timedelta(days=120)).strftime("%Y-%m-%d")
        p = _write(
            tmp_path / "x.md", _full_frontmatter(type_="decision", created=old_date)
        )
        rules = {f.rule for f in lint.lint_file(p)}
        assert "V009" in rules

    def test_v009_recent_decision_clean(self, tmp_path: Path) -> None:
        recent = datetime.now(UTC).strftime("%Y-%m-%d")
        p = _write(
            tmp_path / "x.md", _full_frontmatter(type_="decision", created=recent)
        )
        rules = {f.rule for f in lint.lint_file(p)}
        assert "V009" not in rules

    def test_v010_legacy_field_present(self, tmp_path: Path) -> None:
        extra = "scope: spec\n"
        p = _write(tmp_path / "x.md", _full_frontmatter(extra=extra))
        rules = {f.rule for f in lint.lint_file(p)}
        assert "V010" in rules


# --------------------------------------------------------------------------- #
# lint_path / discover
# --------------------------------------------------------------------------- #


class TestDiscoverAndLintPath:
    def test_discover_skips_templates(self, tmp_path: Path) -> None:
        _write(tmp_path / "decisions" / "a.md", _full_frontmatter())
        _write(tmp_path / "_templates" / "decision.md", "---\n{{CREATED}}\n---\n")
        files = lint.discover_vault_markdown(tmp_path)
        # Check `parent.name` (not full path str) — pytest's tmp dir name can
        # contain '_templates' as a substring (e.g., test_discover_skips_templates0).
        assert any(f.parent.name == "decisions" for f in files)
        assert not any(f.parent.name == "_templates" for f in files)

    def test_lint_path_aggregates(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.md", _full_frontmatter())
        _write(tmp_path / "b.md", "no frontmatter")
        report = lint.lint_path(tmp_path)
        assert report.files_checked == 2
        assert report.errors >= 1

    def test_clean_report_has_no_findings(self, tmp_path: Path) -> None:
        _write(tmp_path / "clean.md", _full_frontmatter())
        report = lint.lint_path(tmp_path)
        assert report.findings == []
        assert report.files_checked == 1


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _full_frontmatter(
    *,
    type_: str = "decision",
    status: str = "active",
    confidence: str = "high",
    created: str = "2026-05-20T00:00:00Z",
    updated: str = "2026-05-20T00:00:00Z",
    source: str = '"file://x.md"',
    extra: str = "",
) -> str:
    """Build a frontmatter block with all required fields."""
    return (
        "---\n"
        f"type: {type_}\n"
        f"status: {status}\n"
        f"created: {created}\n"
        f"updated: {updated}\n"
        f"tags: [test]\n"
        f"confidence: {confidence}\n"
        f"source: {source}\n"
        f"{extra}"
        "---\n\nbody\n"
    )
