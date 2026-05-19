"""``cma webhook`` — receive Anthropic webhook deliveries.

Two subcommands:

* ``cma webhook serve`` — run the FastAPI receiver via uvicorn.
* ``cma webhook verify`` — local-only dry-run: pipe a saved delivery body
  + headers from disk and check the signature without binding a port.
  Useful for testing rotation of the signing secret.

Both require the ``[webhook]`` extra (FastAPI + uvicorn). Importing this
module without the extra installed will still work; only the subcommand
bodies fail with a clear message.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from cma.core.config import load_project

app = typer.Typer(help="Run / test the Anthropic webhook receiver.")
console = Console()


def _require_fastapi() -> None:
    """Fail with a clear message if the [webhook] extra isn't installed."""
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError as exc:
        console.print(
            "[red]webhook subcommands require the [webhook] extra:[/red]\n"
            "  pip install -e '.[webhook]'"
        )
        raise typer.Exit(code=2) from exc


@app.command("serve")
def serve(
    workspace_root: Path = typer.Option(
        Path("."),
        "--workspace-root",
        help="Consumer project root (contains .managed-agents/project.yaml).",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Bind host. Leave on loopback; expose publicly via Cloudflare Tunnel.",
    ),
    port: int = typer.Option(
        8788,
        "--port",
        help="Bind port.",
    ),
) -> None:
    """Run the webhook receiver for the project at ``--workspace-root``.

    The receiver binds 127.0.0.1 by default. Anthropic's webhooks reach
    it via a public HTTPS tunnel (Cloudflare Tunnel recommended) pointed
    at this port. NEVER bind 0.0.0.0 — the signature verification is the
    only authentication; loopback + tunnel is the defense in depth.

    Refuses to start if the project lacks a ``webhook:`` block or if the
    operator hasn't implemented :func:`cma.webhook.policy.kill_switch_policy`.
    """
    _require_fastapi()

    project = load_project(workspace_root.resolve())
    if project.webhook is None:
        console.print(
            "[red]project.yaml has no webhook: block — add one and retry.[/red]"
        )
        raise typer.Exit(code=2)

    # Force the policy stub to surface NOW (not on first delivery) so the
    # operator notices before pointing real traffic at the receiver.
    from cma.webhook.policy import (
        BudgetState,
        kill_switch_policy,
    )

    probe_state = BudgetState(
        session_id="probe",
        project=project.project.name,
        session_cost_usd=0.0,
        session_total_tokens=0,
        daily_spend_usd=0.0,
        daily_cap_usd=project.budget.daily_usd_cap,
        per_session_token_cap=project.budget.per_session_token_cap,
        over_session_cap=False,
        over_daily_cap=False,
    )
    try:
        kill_switch_policy(probe_state, project.budget)
    except NotImplementedError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    from cma.core.budget import BudgetLedger
    from cma.webhook.receiver import build_app

    ledger_path = (
        workspace_root.resolve() / ".managed-agents" / ".state" / "cma.db"
    )
    ledger = BudgetLedger(ledger_path)
    fastapi_app = build_app(project, ledger=ledger)

    import uvicorn

    console.print(
        f"[green]cma-webhook serving[/green] http://{host}:{port}/webhook "
        f"(project={project.project.name})"
    )
    uvicorn.run(fastapi_app, host=host, port=port, log_level="info")


@app.command("verify")
def verify(
    body_path: Path = typer.Argument(
        ..., help="Path to a file containing the raw delivery body bytes."
    ),
    headers_path: Path = typer.Argument(
        ..., help="Path to a JSON file with webhook-id / webhook-timestamp / webhook-signature."
    ),
    secret_env: str = typer.Option(
        "ANTHROPIC_WEBHOOK_SIGNING_KEY",
        "--secret-env",
        help="Env var holding the whsec_-prefixed signing secret.",
    ),
    replay_window_seconds: int = typer.Option(
        60 * 60 * 24 * 365,
        "--replay-window-seconds",
        help="Override the replay window — large by default so old captures verify.",
    ),
) -> None:
    """Dry-run signature verification against a saved delivery on disk.

    Captures the bytes-on-the-wire workflow: in prod the receiver reads
    the raw body and the three headers, then calls ``verify_signature``.
    This subcommand exercises the same path against files so the operator
    can confirm a rotated secret matches a known-good captured delivery.
    """
    import os

    from cma.webhook.signature import (
        WebhookHeaders,
        WebhookSignatureError,
        verify_signature,
    )

    secret = os.environ.get(secret_env, "").strip()
    if not secret:
        console.print(f"[red]{secret_env} is unset/empty.[/red]")
        raise typer.Exit(code=2)

    body = body_path.read_bytes()
    headers_dict = json.loads(headers_path.read_text(encoding="utf-8"))
    try:
        headers = WebhookHeaders.from_mapping(headers_dict)
        verify_signature(
            body=body,
            headers=headers,
            secret=secret,
            replay_window_seconds=replay_window_seconds,
        )
    except WebhookSignatureError as exc:
        console.print(f"[red]verification failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print("[green]signature OK[/green]")


if __name__ == "__main__":  # pragma: no cover
    app()
