"""``cma vault`` subcommands.

Surface (plan v4.1):

- ``cma vault new-decision <slug>`` / ``new-learning`` / ``new-evaluation`` / ``new-incident``
- ``cma vault lint [PATH]``
- ``cma vault index``
- ``cma vault refresh-knowledge`` (wraps :mod:`cma.vault.scrape_anthropic_docs`)

Heavy logic lives in the per-module files under :mod:`cma.vault`; this
module is the Typer glue.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from cma.vault import index_gen, lint, new_note, scrape_anthropic_docs

app = typer.Typer(help="Manage docs/vault/ — knowledge vault for cma + consumers.")
console = Console()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _vault_root(workspace_root: Path | None = None) -> Path:
    """Return the vault directory under the workspace."""
    root = workspace_root or Path.cwd()
    return root / "docs" / "vault"


# ---------------------------------------------------------------------------
# new-*
# ---------------------------------------------------------------------------


def _make_new_command(kind: str) -> Callable[..., None]:
    """Build a parameterized 'new-<kind>' command (4 of these get registered).

    Returns the inner Typer command function (a callable that Typer wraps
    via its command decorator).
    """

    def _cmd(
        slug: str = typer.Argument(..., help=f"kebab-slug for the new {kind} (e.g., 'my-thing')"),
        title: str = typer.Option(None, "--title", help="Override the auto-generated title."),
        source: str = typer.Option("", "--source", help="Provenance string for the source: field."),
        force: bool = typer.Option(False, "--force", help="Overwrite if target file exists."),
        no_editor: bool = typer.Option(False, "--no-editor", help="Skip opening $EDITOR."),
        workspace_root: Path = typer.Option(
            None, "--workspace-root", help="Path to the project root (default: CWD)."
        ),
    ) -> None:
        try:
            result = new_note.make_note(
                kind=kind,
                slug=slug,
                vault_root=_vault_root(workspace_root),
                title=title,
                source=source,
                force=force,
                open_editor=not no_editor,
            )
        except (FileExistsError, FileNotFoundError, ValueError) as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from exc
        console.print(f"[green]ok[/green] Created: {result.path}")
        if result.opened_editor:
            console.print("[dim]Opened in $EDITOR.[/dim]")
        elif not no_editor:
            console.print("[dim]$EDITOR not set — open the file manually.[/dim]")

    _cmd.__name__ = f"new_{kind}"
    return _cmd


# Register the four new-* commands.
for _kind in ("decision", "learning", "evaluation", "incident"):
    app.command(f"new-{_kind}", help=f"Create a new {_kind} note from the template.")(
        _make_new_command(_kind)
    )


# ---------------------------------------------------------------------------
# lint
# ---------------------------------------------------------------------------


@app.command("lint")
def lint_cmd(
    target: Path = typer.Argument(
        None,
        help="File or directory to lint (default: docs/vault/ under CWD).",
    ),
    workspace_root: Path = typer.Option(
        None, "--workspace-root", help="Project root if not CWD."
    ),
) -> None:
    """Validate vault frontmatter. Exit code = ERROR count (capped at 125)."""
    if target is None:
        target = _vault_root(workspace_root)
    if not target.exists():
        console.print(f"[red]No such path:[/red] {target}")
        raise typer.Exit(2)

    report = lint.lint_path(target)

    if not report.findings:
        console.print(f"[green]ok[/green] {report.files_checked} file(s) clean.")
        raise typer.Exit(0)

    table = Table(title=f"Vault lint findings ({target})")
    table.add_column("Rule", style="cyan", no_wrap=True)
    table.add_column("Severity", style="bold")
    table.add_column("File", style="dim")
    table.add_column("Message", style="white")
    for f in report.findings:
        color = {
            "ERROR": "red",
            "WARNING": "yellow",
            "INFO": "blue",
        }[f.severity.value]
        table.add_row(
            f.rule,
            f"[{color}]{f.severity.value}[/{color}]",
            str(f.path.relative_to(Path.cwd())) if f.path.is_relative_to(Path.cwd()) else str(f.path),
            f.message,
        )
    console.print(table)
    console.print(
        f"Summary: {report.errors} error(s), {report.warnings} warning(s) "
        f"across {report.files_checked} file(s)."
    )
    raise typer.Exit(min(report.errors, 125))


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------


@app.command("index")
def index_cmd(
    workspace_root: Path = typer.Option(
        None, "--workspace-root", help="Project root if not CWD."
    ),
) -> None:
    """Regenerate `_INDEX.md` files under docs/vault/."""
    vault = _vault_root(workspace_root)
    if not vault.is_dir():
        console.print(f"[red]No vault at:[/red] {vault}")
        raise typer.Exit(2)
    counts = index_gen.regenerate_all(vault, now=datetime.now(UTC))
    if not counts:
        console.print("[yellow]No indexable folders found.[/yellow]")
    else:
        for folder, n in counts.items():
            console.print(f"  [green]ok[/green] {folder}/_INDEX.md ({n} entries)")


# ---------------------------------------------------------------------------
# refresh-knowledge
# ---------------------------------------------------------------------------


@app.command("refresh-knowledge")
def refresh_knowledge_cmd(
    target_dir: Path = typer.Option(
        None, "--target-dir", help="Output dir (default: docs/vault/knowledge/anthropic-docs)."
    ),
    all_pages: bool = typer.Option(False, "--all", help="Scrape all 134 pages, not just the curated 15."),
    page: str = typer.Option(None, "--page", help="Single page slug to refresh."),
    refresh_older_than: str = typer.Option("30d", "--refresh-older-than", help="Refresh window (e.g., '7d')."),
    force: bool = typer.Option(False, "--force", help="Re-fetch even when cache is fresh."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show plan without writing."),
    workspace_root: Path = typer.Option(None, "--workspace-root"),
) -> None:
    """Scrape Anthropic Claude Code docs into docs/vault/knowledge/anthropic-docs/."""
    if target_dir is None:
        target_dir = _vault_root(workspace_root) / "knowledge" / "anthropic-docs"

    # Reuse the existing argparse-based main(); delegate via direct call.
    argv: list[str] = ["--target-dir", str(target_dir), "--refresh-older-than", refresh_older_than]
    if all_pages:
        argv.append("--all")
    if page:
        argv.extend(["--page", page])
    if force:
        argv.append("--force")
    if dry_run:
        argv.append("--dry-run")

    rc = scrape_anthropic_docs.main(argv)
    if rc != 0:
        raise typer.Exit(rc)


if __name__ == "__main__":  # pragma: no cover
    app()
