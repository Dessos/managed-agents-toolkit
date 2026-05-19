"""``cma doctor`` — beta access probe + local environment diagnostic.

Two modes:

* ``cma doctor`` (default): local-only checks. Inspects env vars, Python
  version, dependency versions. No API calls.
* ``cma doctor --probe-beta``: makes 1-4 API calls against ``ANTHROPIC_API_KEY``
  to determine whether the operator's account has:

  - Base Managed Agents access (gated by ``managed-agents-2026-04-01`` beta)
  - Multi-agent research-preview access (gated by acceptance of the
    https://claude.com/form/claude-managed-agents form for multi-agent)
  - Outcomes research-preview access (same form)

  Each probe creates a throwaway resource and immediately archives/deletes
  it. Token cost is effectively zero; container cost (for the outcomes probe)
  is on the order of seconds of provisioning.

Output is written both to stdout (Rich-formatted) and to
``docs/beta-access-state.md`` for durable reference.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from cma import __version__
from cma.api.client import (
    DEFAULT_BETAS,
    get_client,
)
from cma.telemetry import emit

app = typer.Typer(help="Probe beta access state and local environment.")
console = Console()


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ProbeResult:
    """Outcome of one probe."""

    name: str
    granted: bool | None  # None == not probed
    detail: str
    raw_error: str | None = None


@dataclass(slots=True)
class DoctorReport:
    """Full doctor output, serializable to JSON + Markdown."""

    ts: str
    cma_version: str
    python_version: str
    api_key_present: bool
    api_key_workspace: str | None
    env_vars_present: dict[str, bool] = field(default_factory=dict)
    beta_headers_applied: list[str] = field(default_factory=list)
    probes: list[ProbeResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Local checks
# ---------------------------------------------------------------------------


_OPTIONAL_ENV_VARS = (
    "ANTHROPIC_WEBHOOK_SIGNING_KEY",
    "CMA_WEBHOOK_ENDPOINT",
    "CMA_MCP_BRIDGE_ENDPOINT",
    "CMA_MCP_BRIDGE_TOKEN",
    "CMA_TELEMETRY_PATH",
    "PROMETHEUS_PUSHGATEWAY_URL",
)


def _local_diagnostic() -> DoctorReport:
    """Inspect env + Python, no API calls."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    return DoctorReport(
        ts=datetime.now(UTC).isoformat(timespec="seconds"),
        cma_version=__version__,
        python_version=sys.version.split()[0],
        api_key_present=bool(api_key),
        api_key_workspace=None,  # Filled in after first API call if reachable.
        env_vars_present={name: name in os.environ for name in _OPTIONAL_ENV_VARS},
        beta_headers_applied=list(DEFAULT_BETAS),
    )


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


def _safe_call(fn: Any, *args: Any, **kwargs: Any) -> tuple[Any, str | None]:
    """Call *fn*; return (result, None) or (None, error_repr).

    Captures the SDK's error type + body so the probe report shows EXACTLY
    what Anthropic returned. Important because some 'denied' responses are
    400 with a feature-flag message, not 403.
    """
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc!s}"
        # Preserve body if it's an Anthropic API error.
        for attr in ("body", "response"):
            if hasattr(exc, attr):
                val = getattr(exc, attr)
                if val is not None:
                    err += f" | {attr}={val!r}"
        return None, err


def _probe_base() -> ProbeResult:
    """Probe base Managed Agents access by creating + archiving an agent.

    Cost: 2 API calls, 0 model tokens, 0 container provisioning.
    """
    client = get_client()
    agent, err = _safe_call(
        client.beta.agents.create,
        name="cma-doctor-probe-base",
        model="claude-sonnet-4-6",  # Cheap; we never invoke it.
        system="probe",
        tools=[{"type": "agent_toolset_20260401"}],
    )
    if err is not None:
        return ProbeResult(
            name="base",
            granted=False,
            detail="Failed to create base agent",
            raw_error=err,
        )
    # Best-effort cleanup.
    if agent is not None:
        _safe_call(client.beta.agents.archive, agent.id)
    return ProbeResult(
        name="base",
        granted=True,
        detail=f"Created+archived agent_id={getattr(agent, 'id', '?')}",
    )


def _probe_multiagent() -> ProbeResult:
    """Probe multi-agent research-preview access.

    Strategy: create an agent with a ``multiagent`` block referencing
    ``{type: 'self'}``. If the API accepts, multi-agent is granted.

    The form-gated denial typically surfaces as a 400 with a body mentioning
    'multiagent' or 'research preview' — we just capture the error and label
    granted=False.

    Cost: 2 API calls, 0 model tokens.
    """
    client = get_client()
    agent, err = _safe_call(
        client.beta.agents.create,
        name="cma-doctor-probe-multiagent",
        model="claude-sonnet-4-6",
        system="probe",
        tools=[{"type": "agent_toolset_20260401"}],
        multiagent={
            "type": "coordinator",
            "agents": [{"type": "self"}],
        },
    )
    if err is not None:
        return ProbeResult(
            name="multiagent",
            granted=False,
            detail="Multi-agent block rejected",
            raw_error=err,
        )
    if agent is not None:
        _safe_call(client.beta.agents.archive, agent.id)
    return ProbeResult(
        name="multiagent",
        granted=True,
        detail=f"Created+archived multi-agent agent_id={getattr(agent, 'id', '?')}",
    )


def _probe_outcomes_deferred() -> ProbeResult:
    """Placeholder when --probe-outcomes is NOT passed.

    A real Outcomes probe requires provisioning a container, which costs
    pennies. Default doctor stays free; --probe-outcomes opts in.
    """
    return ProbeResult(
        name="outcomes",
        granted=None,
        detail=(
            "Probe skipped. Pass --probe-outcomes to spend ~$0.10-0.25 on a "
            "throwaway env+session and determine outcomes access state."
        ),
    )


def _probe_outcomes_live() -> ProbeResult:
    """Probe Outcomes research-preview access by attempting a real define_outcome.

    Sequence (each step captures its error if it fails):

    1. Create a throwaway environment (cloud, unrestricted networking).
    2. Create a session against the base probe agent + this env.
    3. Send ``user.define_outcome`` with a trivial inline rubric.
    4. Interrupt + delete the session, archive the environment.

    Cost: ~$0.10-0.25 in container provisioning, plus a few hundred tokens
    of session prefill. The grader doesn't actually run because we interrupt
    before iteration starts.
    """
    client = get_client()

    # Step 1: env
    env, err = _safe_call(
        client.beta.environments.create,
        name="cma-doctor-probe-outcomes-env",
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    if err is not None:
        return ProbeResult(
            name="outcomes",
            granted=False,
            detail="Failed to create probe environment",
            raw_error=err,
        )

    # Step 2: agent — we need a fresh one because the base probe archived its agent.
    agent, agent_err = _safe_call(
        client.beta.agents.create,
        name="cma-doctor-probe-outcomes-agent",
        model="claude-sonnet-4-6",
        system="probe",
        tools=[{"type": "agent_toolset_20260401"}],
    )
    if agent_err is not None:
        if env is not None:
            _safe_call(client.beta.environments.archive, env.id)
        return ProbeResult(
            name="outcomes",
            granted=False,
            detail="Failed to create probe agent",
            raw_error=agent_err,
        )

    # Step 3: session
    session, sess_err = _safe_call(
        client.beta.sessions.create,
        agent=agent.id,
        environment_id=env.id,
        title="cma-doctor-probe-outcomes",
    )
    if sess_err is not None:
        if agent is not None:
            _safe_call(client.beta.agents.archive, agent.id)
        if env is not None:
            _safe_call(client.beta.environments.archive, env.id)
        return ProbeResult(
            name="outcomes",
            granted=False,
            detail="Failed to create probe session",
            raw_error=sess_err,
        )

    # Step 4: send define_outcome — this is the actual feature gate.
    _, outcome_err = _safe_call(
        client.beta.sessions.events.send,
        session.id,
        events=[
            {
                "type": "user.define_outcome",
                "description": "Write nothing.",
                "rubric": {
                    "type": "text",
                    "content": "# Trivial rubric\n- The output is empty.",
                },
                "max_iterations": 1,
            },
        ],
    )

    # Cleanup regardless of outcome.
    _safe_call(
        client.beta.sessions.events.send,
        session.id,
        events=[{"type": "user.interrupt"}],
    )
    _safe_call(client.beta.sessions.delete, session.id)
    _safe_call(client.beta.agents.archive, agent.id)
    _safe_call(client.beta.environments.archive, env.id)

    if outcome_err is not None:
        return ProbeResult(
            name="outcomes",
            granted=False,
            detail="define_outcome event rejected",
            raw_error=outcome_err,
        )

    return ProbeResult(
        name="outcomes",
        granted=True,
        detail=f"Sent define_outcome to session_id={session.id}",
    )


# ---------------------------------------------------------------------------
# Rendering + persistence
# ---------------------------------------------------------------------------


def _render(report: DoctorReport, *, console: Console = console) -> None:
    """Pretty-print the report to the terminal."""
    table = Table(title="cma doctor", show_lines=False)
    table.add_column("Check", style="bold")
    table.add_column("Value")
    table.add_row("cma version", report.cma_version)
    table.add_row("Python", report.python_version)
    table.add_row("ANTHROPIC_API_KEY", "set" if report.api_key_present else "[red]MISSING[/red]")
    for name, present in report.env_vars_present.items():
        table.add_row(name, "set" if present else "[dim]unset[/dim]")
    console.print(table)

    if report.probes:
        ptable = Table(title="Beta access probes", show_lines=False)
        ptable.add_column("Feature", style="bold")
        ptable.add_column("Granted")
        ptable.add_column("Detail")
        for probe in report.probes:
            granted = (
                "[green]yes[/green]"
                if probe.granted is True
                else "[red]no[/red]"
                if probe.granted is False
                else "[yellow]deferred[/yellow]"
            )
            ptable.add_row(probe.name, granted, probe.detail)
        console.print(ptable)
        for probe in report.probes:
            if probe.raw_error:
                console.print(f"[dim]raw_error[{probe.name}]:[/dim] {probe.raw_error}")


def _persist_markdown(report: DoctorReport, *, path: Path) -> None:
    """Write a durable Markdown record of the probe to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Header reflects whether a real probe ran or this is local-only state.
    mode_note = "with live probes" if report.probes else "(local diagnostic only — pass --probe-beta to probe API)"
    lines = [
        "# Beta access state",
        "",
        f"_Generated by `cma doctor` {mode_note} at {report.ts} (UTC)._",
        "",
        "## Local environment",
        "",
        f"- cma version: `{report.cma_version}`",
        f"- Python: `{report.python_version}`",
        f"- `ANTHROPIC_API_KEY` present: `{report.api_key_present}`",
        f"- Beta headers applied: {', '.join(f'`{b}`' for b in report.beta_headers_applied)}",
        "",
        "Optional env vars:",
        "",
    ]
    for name, present in report.env_vars_present.items():
        lines.append(f"- `{name}`: {'set' if present else 'unset'}")
    if report.probes:
        lines += ["", "## Probes", "", "| Feature | Granted | Detail |", "|---|---|---|"]
        for probe in report.probes:
            granted = (
                "✅ yes" if probe.granted is True
                else "❌ no" if probe.granted is False
                else "⏸ deferred"
            )
            lines.append(f"| {probe.name} | {granted} | {probe.detail} |")
        for probe in report.probes:
            if probe.raw_error:
                lines += [
                    "",
                    f"### `{probe.name}` raw error",
                    "",
                    "```",
                    probe.raw_error,
                    "```",
                ]
    lines += [
        "",
        "## Re-running",
        "",
        "Re-run probes with `cma doctor --probe-beta`. Output is idempotent — "
        "each probe creates+archives its own throwaway resources.",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _persist_json(report: DoctorReport, *, path: Path) -> None:
    """JSON sidecar for programmatic consumption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def main(
    probe_beta: bool = typer.Option(
        False,
        "--probe-beta",
        help="Make live API calls to determine beta access state.",
    ),
    probe_outcomes: bool = typer.Option(
        False,
        "--probe-outcomes",
        help=(
            "Also probe Outcomes research-preview access. Costs ~$0.10-0.25 "
            "in container provisioning. Implies --probe-beta."
        ),
    ),
    output_dir: Path = typer.Option(
        Path("docs"),
        "--output-dir",
        help="Where to write beta-access-state.md + .json.",
    ),
) -> None:
    """Inspect local environment + optionally probe API beta access."""
    report = _local_diagnostic()

    emit(
        domain="cma.doctor",
        action="local_diagnostic",
        extra={
            "api_key_present": report.api_key_present,
            "env_vars_present": report.env_vars_present,
        },
    )

    # --probe-outcomes implies --probe-beta (base needs to work for outcomes
    # to be testable).
    if probe_outcomes and not probe_beta:
        probe_beta = True

    if probe_beta:
        if not report.api_key_present:
            console.print(
                "[red]ANTHROPIC_API_KEY not set; cannot probe.[/red] "
                "Export it or copy .env.example → .env first."
            )
            raise typer.Exit(code=2)
        emit(domain="cma.doctor", action="probe_start", extra={
            "betas": list(DEFAULT_BETAS),
            "probe_outcomes": probe_outcomes,
        })
        report.probes.append(_probe_base())
        # Only probe multiagent if base was granted; the call shape is the
        # same so a base denial would mask the multiagent answer anyway.
        base_granted = report.probes[-1].granted
        if base_granted:
            report.probes.append(_probe_multiagent())
        else:
            report.probes.append(
                ProbeResult(
                    name="multiagent",
                    granted=None,
                    detail="Skipped because base access was denied.",
                )
            )
        if probe_outcomes:
            if base_granted:
                report.probes.append(_probe_outcomes_live())
            else:
                report.probes.append(
                    ProbeResult(
                        name="outcomes",
                        granted=None,
                        detail="Skipped because base access was denied.",
                    )
                )
        else:
            report.probes.append(_probe_outcomes_deferred())
        emit(
            domain="cma.doctor",
            action="probe_complete",
            extra={
                p.name: ("granted" if p.granted else "denied" if p.granted is False else "deferred")
                for p in report.probes
            },
        )

    _render(report)
    md_path = output_dir / "beta-access-state.md"
    json_path = output_dir / "beta-access-state.json"
    _persist_markdown(report, path=md_path)
    _persist_json(report, path=json_path)
    console.print(f"[dim]Written:[/dim] {md_path}, {json_path}")
