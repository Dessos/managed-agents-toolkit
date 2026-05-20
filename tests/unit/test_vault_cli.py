"""End-to-end smoke tests for ``cma vault`` CLI commands via Typer's CliRunner."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cma.cli import __main__ as cli_main
from cma.cli.vault import app


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    """A minimal vault with templates so new-* commands work."""
    v = tmp_path / "docs" / "vault" / "_templates"
    v.mkdir(parents=True)
    for kind in ("decision", "learning", "evaluation", "incident"):
        (v / f"{kind}.md").write_text(
            f"---\ntype: {kind}\ncreated: {{{{CREATED}}}}\nstatus: active\n"
            f"updated: {{{{UPDATED}}}}\ntags: [test]\nconfidence: high\n"
            f"source: \"{{{{SOURCE}}}}\"\n---\n# {{{{TITLE}}}}\n",
            encoding="utf-8",
        )
    return tmp_path


class TestVaultRegistered:
    def test_vault_subcommand_appears_in_main_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli_main.app, ["--help"])
        assert result.exit_code == 0
        assert "vault" in result.stdout

    def test_vault_help_lists_all_subcommands(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for cmd in ("new-decision", "new-learning", "lint", "index", "refresh-knowledge"):
            assert cmd in result.stdout


class TestNewCommands:
    def test_new_decision_creates_file(self, vault_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["new-decision", "smoke", "--no-editor", "--workspace-root", str(vault_dir)],
        )
        assert result.exit_code == 0
        # File should exist somewhere under decisions/.
        decisions = vault_dir / "docs" / "vault" / "decisions"
        assert decisions.is_dir()
        files = list(decisions.glob("*.md"))
        assert len(files) == 1
        assert "smoke" in files[0].name

    def test_new_decision_refuses_existing(self, vault_dir: Path) -> None:
        runner = CliRunner()
        args = ["new-decision", "dup", "--no-editor", "--workspace-root", str(vault_dir)]
        runner.invoke(app, args)  # first run
        result = runner.invoke(app, args)  # second
        assert result.exit_code == 1
        assert "already exists" in result.stdout

    def test_new_decision_force_overwrites(self, vault_dir: Path) -> None:
        runner = CliRunner()
        args = ["new-decision", "ow", "--no-editor", "--workspace-root", str(vault_dir)]
        runner.invoke(app, args)
        result = runner.invoke(app, [*args, "--force"])
        assert result.exit_code == 0


class TestLintCommand:
    def test_lint_clean(self, tmp_path: Path) -> None:
        # Build a vault with all-clean files.
        vault = tmp_path / "docs" / "vault"
        decisions = vault / "decisions"
        decisions.mkdir(parents=True)
        (decisions / "x.md").write_text(
            "---\ntype: decision\nstatus: active\ncreated: 2026-05-20T00:00:00Z\n"
            "updated: 2026-05-20T00:00:00Z\ntags: [adr]\nconfidence: high\n"
            "source: \"test\"\n---\n# X\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(app, ["lint", "--workspace-root", str(tmp_path)])
        assert result.exit_code == 0
        assert "clean" in result.stdout.lower()

    def test_lint_reports_errors(self, tmp_path: Path) -> None:
        vault = tmp_path / "docs" / "vault" / "decisions"
        vault.mkdir(parents=True)
        (vault / "broken.md").write_text("no frontmatter\n", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(app, ["lint", "--workspace-root", str(tmp_path)])
        assert result.exit_code >= 1
        assert "V001" in result.stdout

    def test_lint_missing_target(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["lint", str(tmp_path / "nonexistent")])
        assert result.exit_code == 2


class TestIndexCommand:
    def test_index_regenerates(self, tmp_path: Path) -> None:
        vault = tmp_path / "docs" / "vault" / "decisions"
        vault.mkdir(parents=True)
        (vault / "x.md").write_text(
            "---\ntype: decision\nstatus: active\ncreated: 2026-05-20T00:00:00Z\n"
            "updated: 2026-05-20T00:00:00Z\ntags: [adr]\nconfidence: high\n"
            "source: \"test\"\n---\n# X\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(app, ["index", "--workspace-root", str(tmp_path)])
        assert result.exit_code == 0
        assert (vault / "_INDEX.md").is_file()

    def test_index_no_vault(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["index", "--workspace-root", str(tmp_path)])
        assert result.exit_code == 2
