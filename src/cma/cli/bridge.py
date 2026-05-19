"""``cma bridge`` subcommands.

* ``cma bridge rotate-token`` — generate a new executor bearer token.
* ``cma bridge lint`` — validate ``local_mcp_bridge.yaml`` schema shape via Pydantic.
* ``cma bridge probe`` — deep-validate by actually connecting to each
  bridged MCP server's transport and listing its tools.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from cma.executor.auth import rotate_token
from cma.executor.bridge_config import load_bridge_config
from cma.executor.bridge_probe import probe_bridge

app = typer.Typer(help="Rotate executor bearer + lint/probe MCP bridge YAML.")
console = Console()


# ---------------------------------------------------------------------------
# rotate-token (unchanged)
# ---------------------------------------------------------------------------


@app.command("rotate-token")
def rotate(
    workspace_root: Path = typer.Option(
        Path("."),
        "--workspace-root",
        help="Consumer project root.",
    ),
) -> None:
    """Generate a fresh executor bearer token and print it ONCE.

    The new token is persisted to ``.managed-agents/.state/executor_token``.
    Update your cloud-side vault credential to match — old token will fail
    auth on the next request.
    """
    workspace_root = workspace_root.resolve()
    token = rotate_token(workspace_root)
    console.print("[green]New executor bearer token (shown ONCE):[/green]")
    console.print(token)
    console.print(
        f"\n[dim]Stored at:[/dim] {workspace_root}/.managed-agents/.state/executor_token"
    )
    console.print(
        "[dim]Next: rotate the matching vault credential on the Anthropic side "
        "(static_bearer for your MCP bridge URL).[/dim]"
    )


# ---------------------------------------------------------------------------
# lint — schema shape (now Pydantic-backed)
# ---------------------------------------------------------------------------


@app.command("lint")
def lint(
    config: Path = typer.Option(
        Path(".managed-agents/local_mcp_bridge.yaml"),
        "--config",
        help="Bridge config file to validate.",
    ),
) -> None:
    """Validate the local_mcp_bridge.yaml schema.

    Checks the Pydantic-defined shape: top-level ``bridge:`` is a list,
    each entry has ``mcp:`` and ``expose:`` (``"all"`` or list of tool
    names), optional ``deny:``, ``filters:``, and ``transport:`` blocks
    conform to their respective schemas.

    For DEEPER validation (actually connecting to the named servers and
    verifying tool names against the YAML's ``expose``), use
    ``cma bridge probe`` instead.
    """
    config = config.resolve()
    try:
        bridge_config = load_bridge_config(config)
    except FileNotFoundError as exc:
        console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        console.print(f"[red]Bridge config invalid:[/red] {type(exc).__name__}")
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    console.print(
        f"[green]OK[/green] Bridge config valid. "
        f"{len(bridge_config.bridge)} entry/entries declared."
    )
    if not bridge_config.bridge:
        return

    table = Table(title="Declared entries", show_header=True, header_style="bold")
    table.add_column("MCP", style="cyan")
    table.add_column("Expose")
    table.add_column("Transport")
    table.add_column("Filters")
    for entry in bridge_config.bridge:
        expose_str = (
            "all" if entry.expose == "all" else ", ".join(entry.expose)
        )
        if entry.deny:
            expose_str += f" (deny: {', '.join(entry.deny)})"
        transport_str = (
            f"{entry.transport.type}" if entry.transport else "[dim]none[/dim]"
        )
        filter_str = (
            f"deny {len(entry.filters.paths_denylist)} path(s)"
            if entry.filters.paths_denylist
            else "[dim]none[/dim]"
        )
        table.add_row(entry.mcp, expose_str, transport_str, filter_str)
    console.print(table)


# ---------------------------------------------------------------------------
# probe — deep validation by connecting to each transport
# ---------------------------------------------------------------------------


@app.command("probe")
def probe(
    config: Path = typer.Option(
        Path(".managed-agents/local_mcp_bridge.yaml"),
        "--config",
        help="Bridge config file to probe.",
    ),
) -> None:
    """Deep-validate the bridge by connecting to each entry's transport.

    For every entry that has a ``transport:`` block, this command:

    1. Spawns the stdio command OR opens the HTTP endpoint.
    2. Runs the MCP initialize handshake.
    3. Calls ``tools/list`` to enumerate the server's actual tools.
    4. Compares against the YAML's ``expose`` declaration.
    5. Reports drift:
       - ``listed_but_absent``: tool in ``expose`` doesn't exist on server.
       - ``present_but_unlisted``: server tool not in ``expose`` (only
         flagged for explicit lists, not ``expose: all``).

    Exit code 0 when every probed entry succeeded AND has no drift; 1 when
    any entry was unreachable; 2 when drift was detected.

    Entries without ``transport:`` are skipped with a ``no_transport``
    outcome (they pass schema lint but can't be probed).
    """
    config = config.resolve()
    try:
        bridge_config = load_bridge_config(config)
    except FileNotFoundError as exc:
        console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        console.print(f"[red]Bridge config invalid:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    report = asyncio.run(probe_bridge(bridge_config))

    table = Table(title="Bridge probe", show_header=True, header_style="bold")
    table.add_column("MCP", style="cyan")
    table.add_column("Outcome")
    table.add_column("Detail")
    table.add_column("Drift")
    for entry_result in report.entries:
        color = {
            "ok": "green",
            "unreachable": "red",
            "no_transport": "yellow",
        }.get(entry_result.outcome, "white")
        outcome_str = f"[{color}]{entry_result.outcome}[/{color}]"
        drift_str = "[dim]none[/dim]"
        if entry_result.has_drift:
            parts: list[str] = []
            if entry_result.listed_but_absent:
                parts.append(
                    f"absent: {', '.join(entry_result.listed_but_absent)}"
                )
            if entry_result.present_but_unlisted:
                parts.append(
                    f"unlisted: {', '.join(entry_result.present_but_unlisted)}"
                )
            drift_str = f"[yellow]{' | '.join(parts)}[/yellow]"
        table.add_row(entry_result.mcp, outcome_str, entry_result.detail, drift_str)
    console.print(table)

    any_unreachable = any(r.outcome == "unreachable" for r in report.entries)
    any_drift = any(r.has_drift for r in report.entries)

    if any_unreachable:
        console.print("[red]One or more entries unreachable.[/red]")
        raise typer.Exit(code=1)
    if any_drift:
        console.print(
            "[yellow]Drift detected. Review the 'absent' / 'unlisted' columns above.[/yellow]"
        )
        raise typer.Exit(code=2)
    console.print("[green]All probed entries are clean.[/green]")
