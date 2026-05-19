"""``cma executor`` subcommands — serve the local executor + list jobs.

Two subcommands:

* ``cma executor serve --bind 127.0.0.1:8780`` — starts the FastMCP daemon
  with bearer auth middleware. Long-running. Exposes the executor's tools
  at ``/mcp`` for cloud agents to call (typically routed via Cloudflare
  Tunnel for public reachability).
* ``cma executor jobs`` — read the local state DB and render the job
  list. Useful for the operator to inspect what cloud agents are running.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from cma.executor.auth import verify_bearer

app = typer.Typer(help="Run the local executor daemon or inspect jobs.")
console = Console()


@app.command("stdio")
def stdio(
    workspace_root: Path = typer.Option(
        Path("."),
        "--workspace-root",
        help="Consumer project's repo root (contains .managed-agents/).",
    ),
    project_name: str = typer.Option(
        "default",
        "--project-name",
        help="Project slug for telemetry + audit log naming.",
    ),
) -> None:
    """Run the executor over MCP stdio transport (for Claude Code, etc.).

    stdio is the right transport when the MCP CLIENT is on the SAME machine
    (e.g. Claude Code). No port, no auth, no Cloudflare needed — the
    client spawns this command as a subprocess and pipes JSON-RPC over
    stdin/stdout.

    Use this for: Claude Code integration, local development, single-machine
    use. Use ``cma executor serve`` instead when the client is remote
    (Anthropic Managed Agents cloud agents over Cloudflare Tunnel).

    The stdio server has NO authentication: anyone with stdio access has
    full executor capabilities. The implicit trust is that the MCP client
    is on the same machine as the operator.
    """
    from cma.executor.notifications import FastMCPSessionNotifier
    from cma.executor.server import ExecutorServer

    workspace_root = workspace_root.resolve()
    try:
        server = ExecutorServer.make(
            workspace_root=workspace_root,
            project_name=project_name,
            notifier=FastMCPSessionNotifier(),
        )
    except FileNotFoundError as exc:
        # stderr only — stdio MUST be reserved for the JSON-RPC stream.
        import sys

        print(f"Setup error: {exc}", file=sys.stderr)
        raise typer.Exit(code=2) from exc

    # FastMCP's stdio runner blocks until stdin closes. The client is
    # responsible for tearing this down by closing the pipe.
    server.mcp.run(transport="stdio")


@app.command("serve")
def serve(
    workspace_root: Path = typer.Option(
        Path("."),
        "--workspace-root",
        help="Consumer project's repo root (contains .managed-agents/).",
    ),
    project_name: str = typer.Option(
        "default",
        "--project-name",
        help="Project slug for telemetry + audit log naming.",
    ),
    bind: str = typer.Option(
        "127.0.0.1:8780",
        "--bind",
        help="host:port to bind. Stay on 127.0.0.1; Cloudflare Tunnel handles public exposure.",
    ),
) -> None:
    """Start the executor MCP daemon (long-running).

    The daemon binds to ``--bind`` and refuses any request without a valid
    bearer token. Generate the token with ``cma bridge rotate-token`` and
    register it in the cloud-side vault as a ``static_bearer`` credential
    for ``mcp_server_url=https://<your-tunnel-hostname>/mcp``.
    """
    # Imports here to keep `cma --help` fast (no FastMCP/uvicorn startup
    # cost unless we're actually serving).
    import uvicorn
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    from cma.executor.auth import extract_bearer_from_header
    from cma.executor.notifications import FastMCPSessionNotifier
    from cma.executor.server import ExecutorServer

    workspace_root = workspace_root.resolve()
    try:
        server = ExecutorServer.make(
            workspace_root=workspace_root,
            project_name=project_name,
            notifier=FastMCPSessionNotifier(),
        )
    except FileNotFoundError as exc:
        console.print(f"[red]Setup error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    # ---------- Auth middleware --------------------------------------
    workspace_for_auth = workspace_root

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
            token = extract_bearer_from_header(
                request.headers.get("authorization")
            )
            if not verify_bearer(token, workspace_root=workspace_for_auth):
                return JSONResponse(
                    {"error": "unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return await call_next(request)

    # ---------- App composition --------------------------------------
    underlying_app = server.mcp.streamable_http_app()
    # Wrap in auth middleware. Starlette supports re-wrapping.
    underlying_app.user_middleware.insert(0, Middleware(BearerAuthMiddleware))
    underlying_app.middleware_stack = underlying_app.build_middleware_stack()

    host, _, port_str = bind.partition(":")
    if not port_str:
        console.print(f"[red]Invalid --bind {bind!r}; expected host:port[/red]")
        raise typer.Exit(code=2)
    port = int(port_str)

    console.print(
        f"[green]cma-local-executor[/green] starting at "
        f"[cyan]http://{host}:{port}/mcp[/cyan] "
        f"(workspace={workspace_root}, project={project_name})"
    )
    console.print(
        "[dim]Requires bearer auth — generate with: cma bridge rotate-token[/dim]"
    )
    uvicorn.run(underlying_app, host=host, port=port, log_level="info")


@app.command("jobs")
def jobs(
    workspace_root: Path = typer.Option(
        Path("."),
        "--workspace-root",
        help="Consumer project's repo root (contains .managed-agents/).",
    ),
    status: str | None = typer.Option(
        None,
        "--status",
        help="Filter by status: queued, running, succeeded, failed, cancelled, timed_out.",
    ),
    job_type: str | None = typer.Option(
        None,
        "--job-type",
        help="Filter by job_type (e.g. 'backtest').",
    ),
    limit: int = typer.Option(50, "--limit", min=1, max=200),
) -> None:
    """List jobs in the local executor's state DB."""
    from cma.executor.job import JobStatus
    from cma.executor.store import JobStore

    db_path = workspace_root.resolve() / ".managed-agents" / ".state" / "executor.db"
    if not db_path.is_file():
        console.print(
            f"[yellow]No executor state at {db_path} — has 'cma executor serve' run yet?[/yellow]"
        )
        raise typer.Exit(code=0)

    store = JobStore(db_path)
    status_enum = JobStatus(status) if status else None
    rows = store.list_jobs(status=status_enum, job_type=job_type, limit=limit)

    table = Table(title=f"cma executor jobs ({len(rows)} shown)")
    table.add_column("id", style="cyan")
    table.add_column("type", style="dim")
    table.add_column("status", style="bold")
    table.add_column("submitted")
    table.add_column("finished")
    for j in rows:
        color = {
            "succeeded": "green",
            "failed": "red",
            "cancelled": "yellow",
            "timed_out": "red",
            "running": "blue",
            "queued": "dim",
        }.get(j.status.value, "white")
        table.add_row(
            j.id,
            j.job_type,
            f"[{color}]{j.status.value}[/{color}]",
            j.submitted_at,
            j.finished_at or "-",
        )
    console.print(table)
