"""``cma audit`` — human-readable view of the executor audit log.

The executor writes one JSONL line per authenticated MCP tool call (see
:mod:`cma.executor.audit`). This command is the operator's read surface
over that file: filter by time / tool / client / error state, render as a
rich table or raw JSONL.

Sanitization is already applied at write time by the telemetry redactor,
so this reader does NOT need to re-sanitize — but it also doesn't re-open
the redactor in case a future audit-line carries a field the redactor
didn't know to mask. If you spot a leaked credential here, fix the
redactor in :mod:`cma.telemetry.jsonl`, not this view.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from cma.executor.audit import audit_log_path

app = typer.Typer(help="Inspect the executor MCP audit log.")
console = Console()


# ---------------------------------------------------------------------------
# Duration / timestamp parsing
# ---------------------------------------------------------------------------


_DURATION_RE = re.compile(r"^(\d+)\s*([smhdw])$", re.IGNORECASE)
_DURATION_UNITS: dict[str, timedelta] = {
    "s": timedelta(seconds=1),
    "m": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
}


def parse_since(value: str) -> datetime:
    """Parse ``--since`` into a UTC ``datetime``.

    Accepted forms:

    * Relative: ``24h``, ``7d``, ``30m``, ``1w``, ``120s``
    * ISO 8601: ``2026-05-18``, ``2026-05-18T03:14:22``, ``2026-05-18T03:14:22Z``
    * The literal ``all`` returns a sentinel far in the past

    Raises :class:`ValueError` on anything else.
    """
    value = value.strip()
    if value.lower() == "all":
        return datetime(1970, 1, 1, tzinfo=UTC)

    relative = _DURATION_RE.match(value)
    if relative is not None:
        quantity = int(relative.group(1))
        unit_key = relative.group(2).lower()
        delta = _DURATION_UNITS[unit_key] * quantity
        return datetime.now(UTC) - delta

    # ISO 8601 — accept both with and without timezone suffix.
    candidate = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(
            f"Could not parse {value!r} as a duration (e.g. '24h', '7d') or ISO 8601 timestamp."
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


def _iter_audit_lines(path: Path) -> list[dict[str, Any]]:
    """Read every JSONL line from *path*, skipping malformed ones.

    Returns oldest-first. Malformed lines are reported to stderr but do
    not abort the read — the audit log is forensic, partial recovery beats
    no recovery.
    """
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    bad = 0
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entries.append(json.loads(raw))
            except json.JSONDecodeError:
                bad += 1
                console.print(
                    f"[yellow]skipped malformed line {lineno} in {path.name}[/yellow]",
                    soft_wrap=True,
                )
    if bad:
        console.print(f"[dim]({bad} malformed line(s) skipped)[/dim]")
    return entries


def _parse_ts(entry: dict[str, Any]) -> datetime | None:
    """Extract the UTC timestamp from one audit entry, or None if missing/bad."""
    ts = entry.get("ts")
    if not isinstance(ts, str):
        return None
    candidate = ts.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def filter_entries(
    entries: list[dict[str, Any]],
    *,
    since: datetime | None = None,
    tool: str | None = None,
    client_id: str | None = None,
    error_only: bool = False,
) -> list[dict[str, Any]]:
    """Apply all filters in turn. Pure function — easy to test in isolation."""
    out: list[dict[str, Any]] = []
    for entry in entries:
        if since is not None:
            ts = _parse_ts(entry)
            if ts is None or ts < since:
                continue
        if tool is not None and entry.get("tool") != tool:
            continue
        if client_id is not None and entry.get("client_id") != client_id:
            continue
        if error_only and not entry.get("error"):
            continue
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _summarize_args(args: Any, max_len: int = 60) -> str:
    """Render the args field as a short single-line summary for the table."""
    if not args:
        return ""
    if isinstance(args, dict):
        parts = []
        for k, v in args.items():
            v_repr = repr(v) if not isinstance(v, str) else v
            if len(v_repr) > 20:
                v_repr = v_repr[:17] + "..."
            parts.append(f"{k}={v_repr}")
        rendered = " ".join(parts)
    else:
        rendered = str(args)
    if len(rendered) > max_len:
        rendered = rendered[: max_len - 3] + "..."
    return rendered


def _short_ts(ts: str | None) -> str:
    """``2026-05-19T03:14:22Z`` → ``05-19 03:14:22``."""
    if not ts:
        return ""
    # Slice the date+time portion; drop year and TZ for compactness.
    body = ts.replace("Z", "").split("+")[0]
    if "T" in body:
        date_part, time_part = body.split("T", 1)
        return f"{date_part[5:]} {time_part[:8]}"
    return body


def render_table(entries: list[dict[str, Any]]) -> None:
    """Print entries as a rich table."""
    if not entries:
        console.print("[yellow]No audit entries matched the filters.[/yellow]")
        return

    table = Table(title=f"Audit log — {len(entries)} entries", show_lines=False)
    table.add_column("ts (UTC)", no_wrap=True)
    table.add_column("tool", style="bold")
    table.add_column("client", style="dim")
    table.add_column("job_id", style="dim")
    table.add_column("status")
    table.add_column("ms", justify="right")
    table.add_column("args / error")

    for entry in entries:
        status = entry.get("result_status") or ""
        error = entry.get("error")
        last_col = (
            f"[red]error:[/red] {error}"
            if error
            else _summarize_args(entry.get("args"))
        )
        ms_raw = entry.get("elapsed_ms")
        ms = f"{ms_raw:.1f}" if isinstance(ms_raw, (int, float)) else ""
        table.add_row(
            _short_ts(entry.get("ts")),
            str(entry.get("tool") or entry.get("action") or "?"),
            str(entry.get("client_id") or "?"),
            str(entry.get("job_id") or ""),
            status,
            ms,
            last_col,
        )

    console.print(table)
    _render_footer(entries)


def _render_footer(entries: list[dict[str, Any]]) -> None:
    """Counts by tool / status / client, plus error count."""
    tool_counts = Counter(e.get("tool") for e in entries if e.get("tool"))
    status_counts = Counter(e.get("result_status") for e in entries if e.get("result_status"))
    client_counts = Counter(e.get("client_id") for e in entries if e.get("client_id"))
    errors = sum(1 for e in entries if e.get("error"))

    def _fmt(counter: Counter[Any]) -> str:
        return ", ".join(f"{k}={v}" for k, v in counter.most_common()) or "—"

    console.print(f"[dim]by tool:[/dim] {_fmt(tool_counts)}")
    if status_counts:
        console.print(f"[dim]by status:[/dim] {_fmt(status_counts)}")
    console.print(f"[dim]by client:[/dim] {_fmt(client_counts)}")
    if errors:
        console.print(f"[red]errors:[/red] {errors}")


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def main(
    since: str = typer.Option(
        "24h",
        "--since",
        help="Window start — duration (24h, 7d, 30m, 1w) or ISO 8601 timestamp. Pass 'all' for everything.",
    ),
    tool: str | None = typer.Option(
        None,
        "--tool",
        help="Filter by MCP tool name (submit_job, get_job_status, ...).",
    ),
    client_id: str | None = typer.Option(
        None,
        "--client-id",
        help="Filter by the 8-char client fingerprint (see executor.audit).",
    ),
    error_only: bool = typer.Option(
        False,
        "--error-only",
        help="Show only entries that failed (have a non-empty 'error' field).",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit matching entries as raw JSONL on stdout instead of a table.",
    ),
    workspace_root: Path = typer.Option(
        Path("."),
        "--workspace-root",
        help="Consumer project root.",
    ),
) -> None:
    """Read + filter the executor audit log."""
    workspace_root = workspace_root.resolve()
    log_path = audit_log_path(workspace_root)

    try:
        since_dt = parse_since(since)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    entries = _iter_audit_lines(log_path)
    if not entries:
        if not log_path.is_file():
            console.print(
                f"[yellow]No audit log at {log_path}.[/yellow] "
                "Run the executor at least once (cma executor stdio or serve) to populate it."
            )
        else:
            console.print("[yellow]Audit log is empty.[/yellow]")
        return

    filtered = filter_entries(
        entries,
        since=since_dt,
        tool=tool,
        client_id=client_id,
        error_only=error_only,
    )

    if json_out:
        for entry in filtered:
            sys.stdout.write(json.dumps(entry) + "\n")
        return

    render_table(filtered)
