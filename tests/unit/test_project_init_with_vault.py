"""Tests for ``cma project init --with-vault`` scaffolding."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cma.cli.project import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


class TestWithVaultHappyPath:
    def test_scaffold_creates_full_vault_tree(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "init",
                "--target", str(tmp_path),
                "--project-name", "consumer-x",
                "--workspace-root", str(tmp_path),
                "--no-example",
                "--no-mcp-json",
                "--with-vault",
                "--non-interactive",
            ],
        )
        assert result.exit_code == 0, result.stdout

        vault = tmp_path / "docs" / "vault"
        # All key folders + files exist.
        assert (vault / "README.md").is_file()
        assert (vault / "CLAUDE.md").is_file()
        for tmpl in ("decision", "learning", "evaluation", "incident"):
            assert (vault / "_templates" / f"{tmpl}.md").is_file()
        assert (vault / "context" / "ai-session-brief.md").is_file()
        assert (vault / "context" / "current-priorities.md").is_file()
        assert (vault / "decisions" / "_README.md").is_file()
        assert (vault / "decisions" / "_INDEX.md").is_file()
        assert (vault / "learnings" / "_README.md").is_file()
        assert (vault / "learnings" / "_INDEX.md").is_file()
        assert (vault / "governance" / "_README.md").is_file()
        assert (vault / "knowledge" / "_README.md").is_file()
        assert (vault / "knowledge" / "anthropic-docs" / "_README.md").is_file()

    def test_substitutes_project_name(self, runner: CliRunner, tmp_path: Path) -> None:
        runner.invoke(
            app,
            [
                "init",
                "--target", str(tmp_path),
                "--project-name", "alpha-cluster",
                "--workspace-root", str(tmp_path),
                "--no-example",
                "--no-mcp-json",
                "--with-vault",
                "--non-interactive",
            ],
        )
        readme = (tmp_path / "docs" / "vault" / "README.md").read_text(encoding="utf-8")
        brief = (tmp_path / "docs" / "vault" / "context" / "ai-session-brief.md").read_text(
            encoding="utf-8"
        )
        assert "alpha-cluster" in readme
        assert "alpha-cluster" in brief
        # No leftover placeholders in substituted files.
        assert "{{PROJECT_NAME}}" not in readme
        assert "{{PROJECT_NAME}}" not in brief

    def test_template_placeholders_preserved(self, runner: CliRunner, tmp_path: Path) -> None:
        """`_templates/*.md` should KEEP their {{CREATED}}, {{TITLE}}, ... placeholders.

        Those get substituted later by `cma vault new-*`, not at scaffold time.
        """
        runner.invoke(
            app,
            [
                "init",
                "--target", str(tmp_path),
                "--project-name", "x",
                "--workspace-root", str(tmp_path),
                "--no-example",
                "--no-mcp-json",
                "--with-vault",
                "--non-interactive",
            ],
        )
        decision_tmpl = (tmp_path / "docs" / "vault" / "_templates" / "decision.md").read_text(
            encoding="utf-8"
        )
        assert "{{CREATED}}" in decision_tmpl
        assert "{{TITLE}}" in decision_tmpl
        assert "{{SOURCE}}" in decision_tmpl

    def test_writes_settings_json(self, runner: CliRunner, tmp_path: Path) -> None:
        runner.invoke(
            app,
            [
                "init",
                "--target", str(tmp_path),
                "--project-name", "x",
                "--workspace-root", str(tmp_path),
                "--no-example",
                "--no-mcp-json",
                "--with-vault",
                "--non-interactive",
            ],
        )
        settings = tmp_path / ".claude" / "settings.json"
        assert settings.is_file()
        # Valid JSON.
        data = json.loads(settings.read_text(encoding="utf-8"))
        # Has the 4 hooks we wired.
        assert "hooks" in data
        for hook_event in ("SessionStart", "PreToolUse", "PostToolUse", "Stop"):
            assert hook_event in data["hooks"], f"missing {hook_event}"


# --------------------------------------------------------------------------- #
# Flag interactions + safety
# --------------------------------------------------------------------------- #


class TestFlagInteractions:
    def test_no_vault_flag_skips_scaffold(self, runner: CliRunner, tmp_path: Path) -> None:
        runner.invoke(
            app,
            [
                "init",
                "--target", str(tmp_path),
                "--project-name", "x",
                "--workspace-root", str(tmp_path),
                "--no-example",
                "--no-mcp-json",
                "--no-vault",
                "--non-interactive",
            ],
        )
        assert not (tmp_path / "docs" / "vault").exists()
        assert not (tmp_path / ".claude" / "settings.json").exists()

    def test_combines_with_other_flags(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "init",
                "--target", str(tmp_path),
                "--project-name", "combined",
                "--workspace-root", str(tmp_path),
                "--with-example",
                "--with-mcp-json",
                "--with-vault",
                "--non-interactive",
            ],
        )
        assert result.exit_code == 0
        # Vault scaffold present
        assert (tmp_path / "docs" / "vault" / "README.md").is_file()
        # mcp.json present
        assert (tmp_path / ".mcp.json").is_file()
        # Starter present
        assert (tmp_path / ".managed-agents" / "job_specs").is_dir()

    def test_settings_json_not_clobbered_if_existing(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """If `.claude/settings.json` already exists, don't overwrite it."""
        (tmp_path / ".claude").mkdir()
        prior = (tmp_path / ".claude" / "settings.json")
        prior.write_text('{"my": "settings"}', encoding="utf-8")

        runner.invoke(
            app,
            [
                "init",
                "--target", str(tmp_path),
                "--project-name", "x",
                "--workspace-root", str(tmp_path),
                "--no-example",
                "--no-mcp-json",
                "--with-vault",
                "--non-interactive",
            ],
        )
        # Existing file preserved.
        assert prior.read_text(encoding="utf-8") == '{"my": "settings"}'
