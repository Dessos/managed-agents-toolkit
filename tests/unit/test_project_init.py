"""Tests for the both-modes `cma project init` scaffold.

Covers:

* Backward-compatible non-interactive invocation (no flags, no prompts) —
  the previously-working CI/script pattern still produces a valid
  project.yaml with sane defaults.
* Explicit --project-name + --workspace-root substitution.
* --with-example copies the bundled starter (job_specs + adapter, but NOT
  the starter README).
* --with-mcp-json emits a substituted .mcp.json at the target root.
* Substitution safety net: refuses to write a template that has any
  leftover {{...}} placeholder after substitution.
* --force overwrites an existing .managed-agents/; no --force exits 2.
* Interactive prompts fire when stdin is a TTY and the relevant flags
  are omitted.
* TTY auto-detection: when stdin is NOT a TTY, the command treats itself
  as non-interactive automatically (no hanging waiting for prompts).

These tests use typer's CliRunner so we exercise the real Typer wiring
(parameter parsing, prompt handling) without spawning subprocesses.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cma.cli.project import _copy_template, app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Backward compatibility — minimum-effort invocation
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_no_flags_non_interactive_succeeds(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """`cma project init --target <tmp> --non-interactive` with no other
        flags must produce a valid scaffold — that's how CI scripts
        called it before this slice."""
        result = runner.invoke(
            app, ["init", "--target", str(tmp_path), "--non-interactive"]
        )
        assert result.exit_code == 0, result.output

        # Always-written files
        assert (tmp_path / ".managed-agents" / "project.yaml").is_file()
        assert (tmp_path / ".managed-agents" / "local_mcp_bridge.yaml").is_file()
        assert (tmp_path / ".managed-agents" / ".gitignore").is_file()

        # Optional extras DEFAULT TO OFF in non-interactive
        assert not (tmp_path / ".managed-agents" / "adapters").exists()
        assert not (tmp_path / ".mcp.json").exists()

    def test_no_project_name_uses_target_dir_name(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Backward-compat: without --project-name in non-interactive mode,
        the project slug falls back to the target dir name (never exit 2)."""
        result = runner.invoke(
            app, ["init", "--target", str(tmp_path), "--non-interactive"]
        )
        assert result.exit_code == 0
        text = (tmp_path / ".managed-agents" / "project.yaml").read_text(
            encoding="utf-8"
        )
        # tmp_path.name is something like 'test_no_project_name_uses_target_dir_name0'
        assert f"name: {tmp_path.name}" in text


# ---------------------------------------------------------------------------
# Substitution
# ---------------------------------------------------------------------------


class TestSubstitution:
    def test_explicit_project_name_substituted(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            app,
            [
                "init",
                "--target", str(tmp_path),
                "--project-name", "my-app",
                "--non-interactive",
            ],
        )
        assert result.exit_code == 0
        text = (tmp_path / ".managed-agents" / "project.yaml").read_text(
            encoding="utf-8"
        )
        assert "name: my-app" in text
        assert "{{PROJECT_NAME}}" not in text  # safety net would have caught this

    def test_workspace_root_defaults_to_resolved_target(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            app, ["init", "--target", str(tmp_path), "--non-interactive"]
        )
        assert result.exit_code == 0
        text = (tmp_path / ".managed-agents" / "project.yaml").read_text(
            encoding="utf-8"
        )
        # The resolved target absolute path lands in workspace_root.
        assert f"workspace_root: {tmp_path.resolve()}" in text

    def test_explicit_workspace_root_substituted(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        custom_root = tmp_path / "somewhere" / "else"
        custom_root.mkdir(parents=True)
        result = runner.invoke(
            app,
            [
                "init",
                "--target", str(tmp_path),
                "--workspace-root", str(custom_root),
                "--non-interactive",
            ],
        )
        assert result.exit_code == 0
        text = (tmp_path / ".managed-agents" / "project.yaml").read_text(
            encoding="utf-8"
        )
        assert f"workspace_root: {custom_root.resolve()}" in text

    def test_safety_net_rejects_leftover_double_brace(self, tmp_path: Path) -> None:
        """_copy_template refuses to write a file with any remaining
        {{PLACEHOLDER}} after substitution. Catches future-template drift."""
        # Stage a fake template that the substitutions dict won't cover.
        # We monkeypatch importlib.resources.files to a fake traversable
        # that returns a buffer with an unsubstituted placeholder.
        # Simpler: write a deliberately broken template into a tmpdir and
        # call _copy_template against the real cma.templates resource. We
        # can't easily inject a fake template into the package; instead
        # exercise the safety net at the regex level by calling
        # _copy_template with a substitutions dict that DOES NOT cover
        # all placeholders in the real project.yaml template — which has
        # {{PROJECT_NAME}} and {{WORKSPACE_ROOT}}. Omit one of them.
        destination = tmp_path / "out.yaml"
        with pytest.raises(ValueError, match=r"unsubstituted placeholders"):
            _copy_template(
                "project.yaml",
                destination,
                substitutions={"PROJECT_NAME": "x"},  # missing WORKSPACE_ROOT
            )
        # Safety net fires BEFORE write; destination must not exist.
        assert not destination.exists()


# ---------------------------------------------------------------------------
# --with-example
# ---------------------------------------------------------------------------


class TestWithExample:
    def test_copies_starter_files(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "init",
                "--target", str(tmp_path),
                "--project-name", "foo",
                "--with-example",
                "--non-interactive",
            ],
        )
        assert result.exit_code == 0, result.output
        cma = tmp_path / ".managed-agents"
        # job_specs from the starter
        assert (cma / "job_specs" / "compute-stats.yaml").is_file()
        assert (cma / "job_specs" / "example-long-job.yaml").is_file()
        # adapter from the starter
        assert (cma / "adapters" / "local_executor.py").is_file()

    def test_does_not_copy_starter_readme(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """The starter dir has README.md but it's docs for the dir
        itself, not config the consumer needs. Skip it."""
        result = runner.invoke(
            app,
            [
                "init",
                "--target", str(tmp_path),
                "--with-example",
                "--non-interactive",
            ],
        )
        assert result.exit_code == 0
        assert not (tmp_path / ".managed-agents" / "README.md").exists()


# ---------------------------------------------------------------------------
# --with-mcp-json
# ---------------------------------------------------------------------------


class TestWithMcpJson:
    def test_emits_mcp_json_with_substitutions(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            app,
            [
                "init",
                "--target", str(tmp_path),
                "--project-name", "alpha-app",
                "--workspace-root", str(tmp_path),
                "--with-mcp-json",
                "--non-interactive",
            ],
        )
        assert result.exit_code == 0, result.output
        mcp_path = tmp_path / ".mcp.json"
        assert mcp_path.is_file()
        text = mcp_path.read_text(encoding="utf-8")
        # angle-style placeholders replaced
        assert "alpha-app" in text
        assert str(tmp_path.resolve()) in text
        # leftover angle-style placeholders are NOT checked by the safety
        # net (angle style is too ambiguous to scan), but the two known
        # ones must be gone:
        assert "<YOUR_PROJECT_SLUG>" not in text
        assert "<ABSOLUTE_PATH_TO_PROJECT>" not in text


# ---------------------------------------------------------------------------
# --force / no-force
# ---------------------------------------------------------------------------


class TestForce:
    def test_existing_dir_without_force_exits_2(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        (tmp_path / ".managed-agents").mkdir()
        result = runner.invoke(
            app, ["init", "--target", str(tmp_path), "--non-interactive"]
        )
        assert result.exit_code == 2

    def test_existing_dir_with_force_overwrites(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        cma = tmp_path / ".managed-agents"
        cma.mkdir()
        _write_file(cma / "stale.yaml", "stale: content\n")
        result = runner.invoke(
            app,
            [
                "init",
                "--target", str(tmp_path),
                "--project-name", "renamed",
                "--non-interactive",
                "--force",
            ],
        )
        assert result.exit_code == 0
        # The fresh project.yaml lands with substituted name.
        text = (cma / "project.yaml").read_text(encoding="utf-8")
        assert "name: renamed" in text


# ---------------------------------------------------------------------------
# Interactive prompts + TTY detection
# ---------------------------------------------------------------------------


class TestInteractive:
    def test_no_tty_auto_non_interactive(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """When stdin is not a TTY (always the case under CliRunner with
        no `input` provided), the command behaves as non-interactive even
        without the explicit --non-interactive flag — no hanging on prompts."""
        # No --non-interactive flag, no input — would hang on typer.prompt
        # if TTY detection weren't wired.
        result = runner.invoke(
            app, ["init", "--target", str(tmp_path), "--project-name", "auto"]
        )
        assert result.exit_code == 0, result.output
        # Optional flags defaulted to False (non-interactive default).
        assert not (tmp_path / ".managed-agents" / "adapters").exists()
        assert not (tmp_path / ".mcp.json").exists()

    def test_prompts_fire_when_tty_and_flags_omitted(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """When sys.stdin.isatty() returns True and the flags are omitted,
        typer.prompt + typer.confirm get called.

        We monkeypatch isatty rather than rigging a real TTY. Input:
        - project_name prompt → 'pretend-tty-app'
        - with_example prompt → 'n' (the default is yes, but we override)
        - with_mcp_json prompt → 'n'
        """
        with patch("cma.cli.project._stdin_is_tty", return_value=True):
            result = runner.invoke(
                app,
                ["init", "--target", str(tmp_path)],
                input="pretend-tty-app\nn\nn\n",
            )
        assert result.exit_code == 0, result.output
        text = (tmp_path / ".managed-agents" / "project.yaml").read_text(
            encoding="utf-8"
        )
        assert "name: pretend-tty-app" in text
        # User answered 'n' to both optional prompts:
        assert not (tmp_path / ".managed-agents" / "adapters").exists()
        assert not (tmp_path / ".mcp.json").exists()
