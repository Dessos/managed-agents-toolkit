"""``cma agent`` subcommands.

* ``cma agent lint [PATH]`` — validate one or more agent YAMLs.
* ``cma agent list [--workspace-root <path>]`` — discover all agent YAMLs
  reachable from the current workspace (toolkit templates + project overrides).

The lint command exits with the count of ERROR-level findings as its exit
code (capped at 125 so it stays in shell-recognized territory). This makes
it easy to drop into CI.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from cma.core.agent_spec import AgentSpec, load_agent_spec
from cma.core.lint import LintLevel, LintResult, lint_agent_spec

app = typer.Typer(help="Validate and inspect agent YAML definitions.")
console = Console()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _discover_agent_files(target: Path | None, workspace_root: Path) -> list[Path]:
    """Return the list of agent YAMLs to act on.

    Resolution order:

    * If *target* is a file: just that file.
    * If *target* is a directory: every ``*.yaml`` underneath, recursively.
    * If *target* is ``None``: project's ``.managed-agents/agents/`` if it
      exists, otherwise the bundled toolkit templates.
    """
    if target is not None:
        target = target.resolve()
        if target.is_file():
            return [target]
        if target.is_dir():
            return sorted(target.rglob("*.yaml"))
        raise FileNotFoundError(f"No such file or directory: {target}")

    project_agents_dir = (workspace_root / ".managed-agents" / "agents").resolve()
    if project_agents_dir.is_dir():
        return sorted(project_agents_dir.rglob("*.yaml"))

    # Fall back to packaged templates.
    return _bundled_template_files()


def _bundled_template_files() -> list[Path]:
    """Return paths to agent YAMLs shipped under ``cma/templates/agents/``.

    Works whether or not ``cma.templates.agents`` exists as a Python
    subpackage — we anchor on ``cma.templates`` (which always exists) and
    walk into ``agents/`` as a directory.
    """
    try:
        templates_root = resources.files("cma.templates")
    except (ModuleNotFoundError, FileNotFoundError):
        return []
    agents_dir = templates_root / "agents"
    if not agents_dir.is_dir():
        return []
    files: list[Path] = []
    for entry in agents_dir.iterdir():
        if entry.is_file() and entry.name.endswith(".yaml"):
            with resources.as_file(entry) as real_path:
                files.append(Path(real_path))
    return sorted(files)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


_LEVEL_STYLE = {
    LintLevel.ERROR: "[red]error[/red]",
    LintLevel.WARNING: "[yellow]warn[/yellow]",
    LintLevel.INFO: "[dim]info[/dim]",
}


def _render_result(path: Path, result: LintResult) -> None:
    """Print a per-file lint result as a rich table."""
    header = f"[bold]{path.name}[/bold]  ({result.spec_name})"
    if not result.findings:
        console.print(f"{header}  [green]✓ clean[/green]")
        return

    table = Table(title=header, show_lines=False, expand=False)
    table.add_column("Rule", style="bold", no_wrap=True)
    table.add_column("Level", no_wrap=True)
    table.add_column("Location", no_wrap=True)
    table.add_column("Message")
    for finding in result.findings:
        table.add_row(
            finding.rule_id,
            _LEVEL_STYLE[finding.level],
            finding.location,
            finding.message,
        )
    console.print(table)
    # Suggestions printed below the table so they don't blow out column widths.
    for finding in result.findings:
        if finding.suggestion:
            console.print(f"  [dim]{finding.rule_id} suggestion:[/dim] {finding.suggestion}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("lint")
def lint(
    path: Path | None = typer.Argument(
        None,
        help="File or directory to lint. Defaults to .managed-agents/agents/ then toolkit templates.",
    ),
    workspace_root: Path = typer.Option(
        Path("."),
        "--workspace-root",
        help="Consumer project root (used when PATH is omitted).",
    ),
) -> None:
    """Validate agent YAMLs against the lint rules in cma.core.lint."""
    workspace_root = workspace_root.resolve()
    try:
        files = _discover_agent_files(path, workspace_root)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    if not files:
        console.print(
            "[yellow]No agent YAMLs found.[/yellow] "
            "Create one under .managed-agents/agents/ or pass an explicit path."
        )
        raise typer.Exit(code=1)

    total_errors = 0
    total_warnings = 0
    for path_i in files:
        try:
            spec: AgentSpec = load_agent_spec(path_i)
        except ValidationError as exc:
            console.print(f"[red]{path_i.name}: schema validation failed[/red]")
            console.print(str(exc))
            total_errors += 1
            continue
        except (ValueError, FileNotFoundError) as exc:
            console.print(f"[red]{path_i.name}: {exc}[/red]")
            total_errors += 1
            continue
        result = lint_agent_spec(spec)
        _render_result(path_i, result)
        total_errors += len(result.errors)
        total_warnings += len(result.warnings)

    summary = (
        f"\n[bold]{len(files)} file(s) checked[/bold] — "
        f"[red]{total_errors} error(s)[/red], "
        f"[yellow]{total_warnings} warning(s)[/yellow]."
    )
    console.print(summary)
    # Cap at 125 so we stay inside POSIX's "user-defined exit code" range.
    raise typer.Exit(code=min(total_errors, 125))


@app.command("list")
def list_agents(
    workspace_root: Path = typer.Option(
        Path("."),
        "--workspace-root",
        help="Consumer project root.",
    ),
) -> None:
    """List discovered agent YAMLs (toolkit templates + project overrides).

    This is a YAML-discovery view; it does not query the Anthropic API for
    registered agents. The remote-registry view will live under
    ``cma agent registered`` once the resource registry is built.
    """
    workspace_root = workspace_root.resolve()

    # Query each source independently so the two sections stay distinct in
    # the output even when one is empty.
    project_agents_dir = workspace_root / ".managed-agents" / "agents"
    project_files = sorted(project_agents_dir.rglob("*.yaml")) if project_agents_dir.is_dir() else []
    template_files = _bundled_template_files()

    table = Table(title="Agents", show_lines=False)
    table.add_column("Source", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Model")
    table.add_column("Description")
    rows_added = 0
    for source_label, files in (("project", project_files), ("template", template_files)):
        for path_i in files:
            try:
                spec = load_agent_spec(path_i)
            except Exception as exc:
                table.add_row(source_label, path_i.stem, "?", f"[red]parse error: {exc}[/red]")
                rows_added += 1
                continue
            desc = spec.description.strip()
            table.add_row(source_label, spec.name, spec.model, desc or "[dim](no description)[/dim]")
            rows_added += 1
    if rows_added == 0:
        console.print("[yellow]No agent YAMLs discovered.[/yellow]")
    else:
        console.print(table)
