"""Tests for cma.core.lint — rule behavior + severity policy + CLI exit codes."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cma.cli.agent import app as agent_app
from cma.core.agent_spec import AgentSpec
from cma.core.lint import (
    ALL_RULES,
    CACHE_MIN_TOKENS,
    RULE_SEVERITY,
    Finding,
    LintLevel,
    LintResult,
    lint_agent_spec,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(**overrides: object) -> AgentSpec:
    """Build a minimal AgentSpec with overridable fields."""
    base: dict[str, object] = {
        "name": "example",
        "model": "claude-opus-4-7",
        "description": "Reviews code changes for quality, security, and style.",
        "system": "x" * (CACHE_MIN_TOKENS["claude-opus-"] * 4 + 10),  # above threshold
    }
    base.update(overrides)
    return AgentSpec.model_validate(base)


def _has_rule(result: LintResult, rule_id: str) -> bool:
    return any(f.rule_id == rule_id for f in result.findings)


def _findings_for(result: LintResult, rule_id: str) -> list[Finding]:
    return [f for f in result.findings if f.rule_id == rule_id]


# ---------------------------------------------------------------------------
# Per-rule behavior
# ---------------------------------------------------------------------------


class TestR001DescriptionPresent:
    def test_missing_description_flags(self) -> None:
        result = lint_agent_spec(_spec(description=""))
        assert _has_rule(result, "R001")

    def test_present_description_clean(self) -> None:
        result = lint_agent_spec(_spec())
        assert not _has_rule(result, "R001")

    def test_whitespace_only_flags(self) -> None:
        result = lint_agent_spec(_spec(description="   \n  "))
        assert _has_rule(result, "R001")


class TestR002DescriptionTooShort:
    def test_too_short_flags(self) -> None:
        result = lint_agent_spec(_spec(description="too short"))
        assert _has_rule(result, "R002")

    def test_exactly_thirty_clean(self) -> None:
        result = lint_agent_spec(_spec(description="a" * 30))
        assert not _has_rule(result, "R002")

    def test_empty_does_not_double_report(self) -> None:
        # R001 covers empty; R002 only kicks in for 1..30 char range.
        result = lint_agent_spec(_spec(description=""))
        assert not _has_rule(result, "R002")


class TestR003DescriptionEqualsName:
    def test_identical_flags(self) -> None:
        result = lint_agent_spec(_spec(name="reviewer", description="reviewer"))
        assert _has_rule(result, "R003")

    def test_case_insensitive_match_flags(self) -> None:
        result = lint_agent_spec(_spec(name="reviewer", description="REVIEWER"))
        assert _has_rule(result, "R003")

    def test_different_descriptions_clean(self) -> None:
        result = lint_agent_spec(_spec(description="Reviews PRs for issues."))
        assert not _has_rule(result, "R003")


class TestR004DescriptionLacksVerb:
    def test_noun_first_flags(self) -> None:
        result = lint_agent_spec(_spec(description="Tool that does PR review for code quality."))
        assert _has_rule(result, "R004")

    def test_verb_first_clean(self) -> None:
        result = lint_agent_spec(_spec(description="Reviews PRs for code quality and security."))
        assert not _has_rule(result, "R004")


class TestR005SystemSubThreshold:
    def test_well_below_threshold_flags(self) -> None:
        result = lint_agent_spec(_spec(model="claude-opus-4-7", system="short"))
        findings = _findings_for(result, "R005")
        assert findings, "expected R005 finding"
        assert "well below" in findings[0].message

    def test_near_threshold_flags(self) -> None:
        # 80% of the threshold — should trigger the "pad past threshold" branch.
        target = CACHE_MIN_TOKENS["claude-opus-"]
        result = lint_agent_spec(
            _spec(model="claude-opus-4-7", system="x" * int(target * 4 * 0.8))
        )
        findings = _findings_for(result, "R005")
        assert findings, "expected R005 finding"
        assert "Pad past" in findings[0].message

    def test_above_threshold_clean(self) -> None:
        result = lint_agent_spec(_spec(model="claude-opus-4-7"))  # default is above threshold
        assert not _has_rule(result, "R005")

    def test_empty_system_does_not_flag(self) -> None:
        # An empty system prompt is a separate decision the linter doesn't
        # second-guess (though the operator should usually fill it in).
        result = lint_agent_spec(_spec(system=""))
        assert not _has_rule(result, "R005")

    def test_sonnet_threshold_lower(self) -> None:
        # A prompt that's sub-threshold for Opus may still be above Sonnet's.
        sonnet_threshold = CACHE_MIN_TOKENS["claude-sonnet-"]
        prompt = "x" * (sonnet_threshold * 4 + 50)
        result = lint_agent_spec(_spec(model="claude-sonnet-4-6", system=prompt))
        assert not _has_rule(result, "R005")


class TestR006ModelUnknown:
    def test_unknown_model_flags(self) -> None:
        result = lint_agent_spec(_spec(model="claude-opus-9-9"))
        assert _has_rule(result, "R006")

    def test_known_model_clean(self) -> None:
        result = lint_agent_spec(_spec(model="claude-opus-4-7"))
        assert not _has_rule(result, "R006")


class TestR007CustomToolDescriptionShort:
    def test_short_description_flags(self) -> None:
        result = lint_agent_spec(_spec(tools=[{
            "type": "custom",
            "name": "fetch_x",
            "description": "Too short.",
        }]))
        assert _has_rule(result, "R007")

    def test_long_description_clean(self) -> None:
        result = lint_agent_spec(_spec(tools=[{
            "type": "custom",
            "name": "fetch_x",
            "description": "Fetches an object by ID from the workspace registry and returns its metadata.",
        }]))
        assert not _has_rule(result, "R007")

    def test_builtin_toolset_skipped(self) -> None:
        result = lint_agent_spec(_spec(tools=[{"type": "agent_toolset_20260401"}]))
        assert not _has_rule(result, "R007")


class TestR008CustomToolNameNoNamespace:
    def test_bare_name_flags(self) -> None:
        result = lint_agent_spec(_spec(tools=[{
            "type": "custom",
            "name": "fetch",
            "description": "Fetches an object by ID and returns its full metadata blob.",
        }]))
        assert _has_rule(result, "R008")

    def test_namespaced_name_clean(self) -> None:
        result = lint_agent_spec(_spec(tools=[{
            "type": "custom",
            "name": "yourproj_fetch",
            "description": "Fetches an object by ID and returns its full metadata blob.",
        }]))
        assert not _has_rule(result, "R008")


class TestR009CustomToolHasTimestamp:
    def test_iso_date_flags(self) -> None:
        result = lint_agent_spec(_spec(tools=[{
            "type": "custom",
            "name": "yourproj_fetch",
            "description": "Fetches an object as of 2026-05-19 from the workspace registry today.",
        }]))
        assert _has_rule(result, "R009")

    def test_now_call_flags(self) -> None:
        result = lint_agent_spec(_spec(tools=[{
            "type": "custom",
            "name": "yourproj_fetch",
            "description": "Fetches an object dynamically computed via datetime.now() at call time.",
        }]))
        assert _has_rule(result, "R009")

    def test_stable_description_clean(self) -> None:
        result = lint_agent_spec(_spec(tools=[{
            "type": "custom",
            "name": "yourproj_fetch",
            "description": "Fetches an object by ID and returns its full metadata blob.",
        }]))
        assert not _has_rule(result, "R009")


class TestR010TemplateVersionMissing:
    def test_template_without_version_flags(self) -> None:
        result = lint_agent_spec(_spec(metadata={"cma_template": "code-reviewer"}))
        assert _has_rule(result, "R010")

    def test_template_with_version_clean(self) -> None:
        result = lint_agent_spec(_spec(metadata={
            "cma_template": "code-reviewer",
            "cma_template_version": 1,
        }))
        assert not _has_rule(result, "R010")

    def test_no_template_metadata_clean(self) -> None:
        result = lint_agent_spec(_spec(metadata={}))
        assert not _has_rule(result, "R010")


# ---------------------------------------------------------------------------
# Severity policy + registry
# ---------------------------------------------------------------------------


class TestRuleRegistry:
    def test_all_rules_have_severity(self) -> None:
        """Every registered rule must appear in RULE_SEVERITY or rule gain bias."""
        missing = set(ALL_RULES) - set(RULE_SEVERITY)
        assert not missing, f"Rules missing severity entries: {missing}"

    def test_severity_application(self) -> None:
        """RULE_SEVERITY rewrites the level rules emit naturally.

        We pick R002 (default WARNING) and a spec that triggers it, then
        confirm the effective level matches the policy. This test exists so
        the operator's TODO edit in lint.py keeps the lint engine honest.
        """
        result = lint_agent_spec(_spec(description="too short"))
        r002 = _findings_for(result, "R002")
        assert r002
        assert r002[0].level is RULE_SEVERITY["R002"]


class TestLintResult:
    def test_ok_when_only_warnings(self) -> None:
        # Description is short (R002 warning) but no errors otherwise.
        result = lint_agent_spec(_spec(description="abc"))
        # R002 is WARNING by default; if the operator made it ERROR, ok==False.
        expected_ok = RULE_SEVERITY["R002"] is not LintLevel.ERROR
        assert result.ok is expected_ok

    def test_not_ok_when_errors(self) -> None:
        result = lint_agent_spec(_spec(description=""))  # triggers R001 (ERROR)
        assert not result.ok
        assert result.errors


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestCliLint:
    def test_clean_file_exits_zero(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "clean.yaml"
        yaml_path.write_text(
            "name: example\n"
            "model: claude-opus-4-7\n"
            "description: Reviews PRs for code quality, security, and style issues.\n"
            f"system: |\n  {'x' * (CACHE_MIN_TOKENS['claude-opus-'] * 4 + 100)}\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(agent_app, ["lint", str(yaml_path)])
        assert result.exit_code == 0, result.output

    def test_dirty_file_exits_nonzero(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "dirty.yaml"
        # Missing description -> R001 ERROR by default policy.
        yaml_path.write_text(
            "name: example\nmodel: claude-opus-4-7\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(agent_app, ["lint", str(yaml_path)])
        assert result.exit_code >= 1

    def test_directory_target_lints_all(self, tmp_path: Path) -> None:
        d = tmp_path / "agents"
        d.mkdir()
        (d / "a.yaml").write_text(
            "name: a\nmodel: claude-opus-4-7\ndescription: Reviews stuff thoroughly and carefully.\n"
            f"system: {'x' * (CACHE_MIN_TOKENS['claude-opus-'] * 4 + 100)}\n",
            encoding="utf-8",
        )
        (d / "b.yaml").write_text(
            "name: b\nmodel: claude-opus-4-7\n",  # missing description
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(agent_app, ["lint", str(d)])
        assert "2 file(s) checked" in result.output

    def test_missing_path_exits_two(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(agent_app, ["lint", str(tmp_path / "nope")])
        assert result.exit_code == 2

    def test_list_empty_workspace(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(agent_app, ["list", "--workspace-root", str(tmp_path)])
        # No project agents + no bundled templates -> just a notice + exit 0.
        assert result.exit_code == 0

    def test_list_shows_project_agent(self, tmp_path: Path) -> None:
        d = tmp_path / ".managed-agents" / "agents"
        d.mkdir(parents=True)
        (d / "reviewer.yaml").write_text(
            "name: reviewer\nmodel: claude-opus-4-7\ndescription: Reviews code carefully.\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(agent_app, ["list", "--workspace-root", str(tmp_path)])
        assert result.exit_code == 0
        assert "reviewer" in result.output


# ---------------------------------------------------------------------------
# Sanity: full lint run produces no unexpected crashes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", list(ALL_RULES))
def test_every_rule_callable_returns_list(rule_id: str) -> None:
    spec = _spec()
    findings = ALL_RULES[rule_id](spec)
    assert isinstance(findings, list)
    for f in findings:
        assert f.rule_id == rule_id
