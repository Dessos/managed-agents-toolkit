"""``cma project`` — init + verify a consumer-project config.

Two subcommands:

* ``cma project init`` — both-modes scaffold for a consumer project.
  Interactive when stdin is a TTY, flag-driven otherwise. Substitutes
  project name + workspace root into ``project.yaml`` at copy time;
  optionally materializes the bundled starter example
  (``--with-example``) and/or a project-root ``.mcp.json``
  (``--with-mcp-json``).
* ``cma project verify`` — loads + validates an existing config; reports
  errors with file:line context where possible.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

import typer
from rich.console import Console

from cma.core.config import load_project

app = typer.Typer(help="Initialise or verify a consumer-project config.")
console = Console()


# ---------------------------------------------------------------------------
# Template plumbing
# ---------------------------------------------------------------------------


_PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_][A-Z0-9_]*\}\}")


def _copy_template(
    name: str,
    destination: Path,
    *,
    substitutions: dict[str, str] | None = None,
    placeholder_style: str = "double-brace",
) -> None:
    """Copy a named template file to *destination*, applying substitutions.

    ``substitutions`` keys are placeholder names (without delimiters);
    values are the replacements. Two placeholder styles are supported:

    * ``"double-brace"`` (default): ``{{KEY}}``. After substitution, any
      remaining ``{{...}}`` causes a ValueError — a safety net against
      future templates with placeholders we forgot to substitute.
    * ``"angle"``: ``<KEY>``. Used by ``.mcp.json.example`` which uses
      XML-style placeholders. No safety net (the ``<...>`` form is too
      ambiguous to scan for leftovers without false positives).

    Uses ``importlib.resources`` so the lookup works identically across
    editable installs, wheel installs, and zipapp/PEX deployments. The
    alternative — ``Path(__file__).parent.parent / 'templates'`` — works
    in editable mode but is fragile across packaging formats.
    """
    source = resources.files("cma.templates").joinpath(name)
    content = source.read_text(encoding="utf-8")

    if substitutions:
        if placeholder_style == "double-brace":
            for key, value in substitutions.items():
                content = content.replace(f"{{{{{key}}}}}", value)
        elif placeholder_style == "angle":
            for key, value in substitutions.items():
                content = content.replace(f"<{key}>", value)
        else:
            raise ValueError(f"Unknown placeholder_style: {placeholder_style!r}")

    if placeholder_style == "double-brace":
        leftover = _PLACEHOLDER_RE.findall(content)
        if leftover:
            raise ValueError(
                f"Template {name!r} has unsubstituted placeholders: "
                f"{sorted(set(leftover))}. Update the substitutions dict "
                f"or add defaults in cma.cli.project."
            )

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


def _iter_starter_files(starter: Traversable) -> Iterable[tuple[Path, Traversable]]:
    """Yield (relative_path, traversable) for every file under ``starter``,
    recursively. README.md is skipped — it's docs for the starter dir
    itself, not config the consumer needs in their project tree.
    """

    def walk(current: Traversable, prefix: Path) -> Iterable[tuple[Path, Traversable]]:
        for item in current.iterdir():
            name = item.name
            rel = prefix / name
            if item.is_dir():
                yield from walk(item, rel)
            else:
                if rel.parts == ("README.md",):
                    continue
                yield rel, item

    return list(walk(starter, Path()))


def _copy_starter(target_cma_dir: Path) -> list[Path]:
    """Copy every starter file (except README.md) into ``target_cma_dir``.

    Returns the list of written paths (for the success message). The
    starter files are bundled at ``cma.templates.starter`` and ship with
    the wheel via the ``templates/**/*`` package-data glob.
    """
    starter = resources.files("cma.templates").joinpath("starter")
    written: list[Path] = []
    for rel, item in _iter_starter_files(starter):
        dst = target_cma_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(item.read_bytes())
        written.append(dst)
    return written


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


def _stdin_is_tty() -> bool:
    """Indirection over ``sys.stdin.isatty()`` so tests can monkeypatch
    it without fighting CliRunner's stdin replacement."""
    return sys.stdin.isatty()


def _resolve_string(
    arg: str | None,
    *,
    prompt_text: str,
    default: str,
    non_interactive: bool,
) -> str:
    """Return ``arg`` if set, else prompt (interactive) or ``default`` (non-int).

    Non-interactive mode never raises — backward-compatible with the
    pre-change ``cma project init`` invocation (no args, no flags).
    """
    if arg is not None:
        return arg
    if non_interactive:
        return default
    # typer.prompt is typed as Any in some versions; cast back to str.
    return str(typer.prompt(prompt_text, default=default))


def _resolve_bool(
    arg: bool | None,
    *,
    prompt_text: str,
    default_interactive: bool,
    non_interactive: bool,
) -> bool:
    """Return ``arg`` if set, else prompt (interactive) or False (non-int).

    Additive flags default to ``False`` in non-interactive mode — the
    safe / minimum-effect default.
    """
    if arg is not None:
        return arg
    if non_interactive:
        return False
    return typer.confirm(prompt_text, default=default_interactive)


# ---------------------------------------------------------------------------
# `cma project init`
# ---------------------------------------------------------------------------


@app.command("init")
def init(
    target: Path = typer.Option(
        Path("."),
        "--target",
        help="Directory to initialise. Will create .managed-agents/ here.",
    ),
    project_name: str | None = typer.Option(
        None,
        "--project-name",
        "-n",
        help=(
            "Project slug written into project.yaml. Prompted in interactive "
            "mode; defaults to the target directory name in non-interactive."
        ),
    ),
    workspace_root: Path | None = typer.Option(
        None,
        "--workspace-root",
        "-w",
        help=(
            "Absolute path to the project root (written into project.yaml + "
            ".mcp.json). Defaults to the resolved --target."
        ),
    ),
    with_example: bool | None = typer.Option(
        None,
        "--with-example/--no-example",
        help=(
            "Copy the bundled starter job_specs + adapter into "
            ".managed-agents/. Interactive prompt when not given; defaults "
            "to False in non-interactive mode."
        ),
    ),
    with_mcp_json: bool | None = typer.Option(
        None,
        "--with-mcp-json/--no-mcp-json",
        help=(
            "Emit .mcp.json at the project root with substituted "
            "workspace_root + project_name. Interactive prompt when not "
            "given; defaults to False in non-interactive mode."
        ),
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help=(
            "Disable interactive prompts. Auto-enabled when stdin is not a "
            "TTY (pipelines, CI, agent-driven invocations)."
        ),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite existing .managed-agents/ if present.",
    ),
) -> None:
    """Scaffold a consumer-project config.

    Always writes:

    - ``.managed-agents/project.yaml`` (with substituted name + workspace_root)
    - ``.managed-agents/local_mcp_bridge.yaml`` (operator-authored, see docs)
    - ``.managed-agents/.gitignore`` (excludes runtime state + outputs)

    Optionally writes (interactive prompt or flag-driven):

    - ``.managed-agents/job_specs/*.yaml`` + ``.managed-agents/adapters/local_executor.py``
      (when ``--with-example``)
    - ``.mcp.json`` at the project root (when ``--with-mcp-json``)
    """
    # TTY auto-detection: pipelines, CI, and agent-driven invocations get
    # non-interactive behavior without an explicit flag. The flag remains
    # as an escape hatch when stdin is a TTY but the caller wants
    # non-interactive behavior anyway.
    if not _stdin_is_tty():
        non_interactive = True

    target = target.resolve()
    cma_dir = target / ".managed-agents"
    if cma_dir.exists() and not force:
        console.print(
            f"[red]{cma_dir} already exists. Pass --force to overwrite.[/red]"
        )
        raise typer.Exit(code=2)

    # Resolve required values.
    name_default = target.name if target.name and target.name != "." else "my-project"
    resolved_name = _resolve_string(
        project_name,
        prompt_text="Project slug",
        default=name_default,
        non_interactive=non_interactive,
    )
    resolved_workspace = (
        workspace_root.resolve() if workspace_root is not None else target
    )

    # Resolve optional extras.
    do_example = _resolve_bool(
        with_example,
        prompt_text="Include the starter example (job_specs + adapter)?",
        default_interactive=True,
        non_interactive=non_interactive,
    )
    do_mcp_json = _resolve_bool(
        with_mcp_json,
        prompt_text="Emit .mcp.json at the project root for Claude Code?",
        default_interactive=True,
        non_interactive=non_interactive,
    )

    cma_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    # project.yaml — substituted
    project_yaml = cma_dir / "project.yaml"
    _copy_template(
        "project.yaml",
        project_yaml,
        substitutions={
            "PROJECT_NAME": resolved_name,
            "WORKSPACE_ROOT": str(resolved_workspace),
        },
    )
    written.append(project_yaml)

    # local_mcp_bridge.yaml — no substitutions
    bridge_yaml = cma_dir / "local_mcp_bridge.yaml"
    _copy_template("local_mcp_bridge.yaml", bridge_yaml)
    written.append(bridge_yaml)

    # .gitignore
    gitignore = cma_dir / ".gitignore"
    gitignore.write_text(
        "# Runtime state\n.state/\n*.db\n*.jsonl\n# Outputs\noutputs/\n",
        encoding="utf-8",
    )
    written.append(gitignore)

    # --with-example: copy starter files
    starter_files: list[Path] = []
    if do_example:
        starter_files = _copy_starter(cma_dir)
        written.extend(starter_files)

    # --with-mcp-json: emit substituted .mcp.json at project root
    mcp_json_path: Path | None = None
    if do_mcp_json:
        mcp_json_path = target / ".mcp.json"
        _copy_template(
            ".mcp.json.example",
            mcp_json_path,
            substitutions={
                "ABSOLUTE_PATH_TO_PROJECT": str(resolved_workspace),
                "YOUR_PROJECT_SLUG": resolved_name,
            },
            placeholder_style="angle",
        )
        written.append(mcp_json_path)

    # ---- Success output ---------------------------------------------------
    console.print("\n[green]✓ Project initialised.[/green]\n")
    console.print("[bold]Files written:[/bold]")
    for path in written:
        console.print(
            f"  [cyan]{path.relative_to(target)}[/cyan]"
        )

    console.print(
        f"\n[dim](project_name=[bold]{resolved_name}[/bold], "
        f"workspace_root=[bold]{resolved_workspace}[/bold])[/dim]\n"
    )

    console.print("[bold]Next steps:[/bold]")
    step = 1
    console.print(
        f"  {step}. Edit [cyan].managed-agents/local_mcp_bridge.yaml[/cyan] "
        f"to declare which local MCP servers the cloud agent can reach."
    )
    step += 1
    if do_example:
        console.print(
            f"  {step}. Edit [cyan].managed-agents/adapters/local_executor.py[/cyan] "
            f"and replace the TODO sections with your real handlers."
        )
        step += 1
    console.print(
        f"  {step}. Run [cyan]cma project verify[/cyan] to validate the config."
    )
    step += 1
    if do_mcp_json:
        console.print(
            f"  {step}. Restart Claude Code to pick up the new "
            f"[cyan].mcp.json[/cyan]."
        )


# ---------------------------------------------------------------------------
# `cma project verify`
# ---------------------------------------------------------------------------


@app.command("verify")
def verify(
    target: Path = typer.Option(
        Path("."),
        "--target",
        help="Project root containing .managed-agents/project.yaml.",
    ),
) -> None:
    """Load + validate the project config; print errors with context."""
    target = target.resolve()
    try:
        config = load_project(target)
    except FileNotFoundError as exc:
        console.print(f"[red]Config not found:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        console.print(f"[red]Config invalid:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=2) from exc
    console.print(
        f"[green]✓ Config valid.[/green] "
        f"project=[bold]{config.project.name}[/bold] "
        f"workspace_root=[cyan]{config.project.workspace_root}[/cyan] "
        f"agents={len(config.agents)} environments={len(config.environments)} "
        f"workflows={len(config.workflows)}"
    )
