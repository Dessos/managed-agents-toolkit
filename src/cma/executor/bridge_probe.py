"""Deep probe of a bridge config: connect to each transport-equipped entry
and verify the YAML's ``expose`` list against the server's actual tools.

This is the engine behind ``cma bridge probe``. The CLI command imports
:func:`probe_bridge` and renders the report.

Probe outcomes per entry:

* ``no_transport`` — entry has no transport config, skipped (schema lint
  caught what it could; deep probe is a no-op).
* ``unreachable`` — couldn't spawn/connect (command not found, network
  failure, auth rejected, etc.). Includes the underlying error.
* ``ok`` — connected, listed tools, matched against expose. May include
  drift details (listed but absent / present but unlisted) but the probe
  itself succeeded.

Drift signals returned per entry:

* ``listed_but_absent`` — tool name in ``expose`` doesn't exist on server.
  Usually a typo or the server's tool surface changed.
* ``present_but_unlisted`` — server advertises a tool that isn't in
  ``expose`` (only flagged when ``expose: list``, not ``expose: all``).
  Operator may want to opt in.
"""

from __future__ import annotations

import asyncio
import asyncio.subprocess as aio_subprocess
import contextlib
import json
import os
from dataclasses import dataclass, field
from typing import Any

from cma.executor.bridge_config import (
    BridgeConfig,
    BridgeEntry,
    HttpTransport,
    StdioTransport,
)

# ---------------------------------------------------------------------------
# Report types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EntryProbeResult:
    """Result of probing one bridge entry."""

    mcp: str
    outcome: str  # "no_transport" | "unreachable" | "ok"
    detail: str = ""
    tools_announced: list[str] = field(default_factory=list)
    listed_but_absent: list[str] = field(default_factory=list)
    present_but_unlisted: list[str] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return bool(self.listed_but_absent or self.present_but_unlisted)


@dataclass(slots=True)
class BridgeProbeReport:
    """Aggregate report across all bridge entries."""

    entries: list[EntryProbeResult] = field(default_factory=list)

    @property
    def overall_ok(self) -> bool:
        """True iff every probed entry succeeded AND has no drift."""
        return all(
            e.outcome in {"ok", "no_transport"} and not e.has_drift
            for e in self.entries
        )


# ---------------------------------------------------------------------------
# Top-level probe
# ---------------------------------------------------------------------------


async def probe_bridge(config: BridgeConfig) -> BridgeProbeReport:
    """Probe every entry in *config*; return a structured report."""
    report = BridgeProbeReport()
    for entry in config.bridge:
        result = await _probe_entry(entry)
        report.entries.append(result)
    return report


async def _probe_entry(entry: BridgeEntry) -> EntryProbeResult:
    """Probe a single entry. Never raises — failures become outcome states."""
    if entry.transport is None:
        return EntryProbeResult(
            mcp=entry.mcp,
            outcome="no_transport",
            detail="No transport configured; deep probe skipped (shape lint only).",
        )
    try:
        if isinstance(entry.transport, StdioTransport):
            tools = await _list_tools_stdio(entry.transport)
        elif isinstance(entry.transport, HttpTransport):
            tools = await _list_tools_http(entry.transport)
        else:  # pragma: no cover — discriminator should prevent this
            return EntryProbeResult(
                mcp=entry.mcp,
                outcome="unreachable",
                detail=f"Unknown transport type {type(entry.transport).__name__!r}",
            )
    except Exception as exc:
        return EntryProbeResult(
            mcp=entry.mcp,
            outcome="unreachable",
            detail=f"{type(exc).__name__}: {exc!s}",
        )
    listed_but_absent, present_but_unlisted = _compute_drift(entry, tools)
    return EntryProbeResult(
        mcp=entry.mcp,
        outcome="ok",
        detail=f"Connected; {len(tools)} tool(s) advertised.",
        tools_announced=tools,
        listed_but_absent=listed_but_absent,
        present_but_unlisted=present_but_unlisted,
    )


# ---------------------------------------------------------------------------
# Drift computation
# ---------------------------------------------------------------------------


def _compute_drift(
    entry: BridgeEntry, announced: list[str]
) -> tuple[list[str], list[str]]:
    """Compare the YAML's expose/deny against the server's announced tools.

    Returns ``(listed_but_absent, present_but_unlisted)``.
    """
    announced_set = set(announced)
    if entry.expose == "all":
        # 'all' minus deny is the effective allowlist. Anything denied that
        # isn't actually present is a mild drift (operator denied a tool
        # that no longer exists) — surface it as "listed_but_absent" so
        # operators see stale deny entries.
        listed_but_absent = [t for t in entry.deny if t not in announced_set]
        return listed_but_absent, []
    # expose is a list
    expose_set = set(entry.expose)
    deny_set = set(entry.deny)
    listed_but_absent = sorted(expose_set - announced_set)
    present_but_unlisted = sorted(announced_set - expose_set - deny_set)
    return listed_but_absent, present_but_unlisted


# ---------------------------------------------------------------------------
# MCP handshake constants
# ---------------------------------------------------------------------------


_INIT_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "cma-bridge-probe", "version": "0.0"},
    },
}
_INITIALIZED_NOTIFICATION = {
    "jsonrpc": "2.0",
    "method": "notifications/initialized",
}
_TOOLS_LIST_REQUEST = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {},
}


# ---------------------------------------------------------------------------
# Stdio probe
# ---------------------------------------------------------------------------


async def _list_tools_stdio(transport: StdioTransport) -> list[str]:
    """Spawn the command and run an MCP initialize + tools/list round-trip."""
    env = os.environ.copy()
    env.update(transport.env)
    # argv list, no shell — safe by construction.
    process = await aio_subprocess.create_subprocess_exec(
        transport.command,
        *transport.args,
        stdin=aio_subprocess.PIPE,
        stdout=aio_subprocess.PIPE,
        stderr=aio_subprocess.PIPE,
        env=env,
        cwd=transport.cwd,
    )
    try:
        return await _mcp_tools_list_over_stdio(process)
    finally:
        await _shutdown(process)


async def _mcp_tools_list_over_stdio(process: aio_subprocess.Process) -> list[str]:
    """Send the MCP handshake then tools/list over the process's stdio."""
    assert process.stdin is not None and process.stdout is not None

    async def _send(msg: dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        await process.stdin.drain()

    async def _read_response(expect_id: int, timeout: float = 10.0) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(
                    f"No JSON-RPC response with id={expect_id} within {timeout}s"
                )
            assert process.stdout is not None
            line = await asyncio.wait_for(process.stdout.readline(), timeout=remaining)
            if not line:
                raise RuntimeError("MCP server closed stdout unexpectedly")
            try:
                # json.loads is typed Any; we narrow to dict[str, Any] for mypy.
                frame: dict[str, Any] = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                # Some MCP servers emit log lines to stdout. Skip non-JSON.
                continue
            if frame.get("id") == expect_id:
                return frame

    await _send(_INIT_REQUEST)
    await _read_response(expect_id=1)
    await _send(_INITIALIZED_NOTIFICATION)
    await _send(_TOOLS_LIST_REQUEST)
    resp = await _read_response(expect_id=2)
    if "error" in resp:
        raise RuntimeError(f"tools/list error: {resp['error']}")
    tools = resp.get("result", {}).get("tools", [])
    return [t["name"] for t in tools if isinstance(t.get("name"), str)]


# ---------------------------------------------------------------------------
# HTTP probe
# ---------------------------------------------------------------------------


async def _list_tools_http(transport: HttpTransport) -> list[str]:
    """Run MCP init + tools/list against an HTTP streamable endpoint.

    The streamable-HTTP transport spec uses POST per JSON-RPC frame; we
    issue direct JSON-RPC requests and parse the response bodies.
    """
    import httpx  # local import — only needed for HTTP probe path

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if transport.auth_token_env:
        token = os.environ.get(transport.auth_token_env, "").strip()
        if not token:
            raise RuntimeError(
                f"Auth token env var {transport.auth_token_env!r} is unset; "
                f"cannot probe {transport.url}"
            )
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
        r = await client.post(transport.url, json=_INIT_REQUEST)
        r.raise_for_status()
        init_resp = r.json()
        if "error" in init_resp:
            raise RuntimeError(f"initialize error: {init_resp['error']}")
        await client.post(transport.url, json=_INITIALIZED_NOTIFICATION)
        r = await client.post(transport.url, json=_TOOLS_LIST_REQUEST)
        r.raise_for_status()
        list_resp = r.json()
        if "error" in list_resp:
            raise RuntimeError(f"tools/list error: {list_resp['error']}")
        tools: list[dict[str, Any]] = list_resp.get("result", {}).get("tools", [])
        return [t["name"] for t in tools if isinstance(t.get("name"), str)]


# ---------------------------------------------------------------------------
# Process shutdown
# ---------------------------------------------------------------------------


async def _shutdown(process: aio_subprocess.Process) -> None:
    """Terminate the subprocess politely, then forcibly if it hangs."""
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=3.0)
        return
    except TimeoutError:
        pass
    process.kill()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=2.0)
