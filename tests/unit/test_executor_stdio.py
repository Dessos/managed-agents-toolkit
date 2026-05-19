"""Integration tests for the `cma executor stdio` MCP server.

Spawns the executor as a subprocess (just like Claude Code would), sends
JSON-RPC frames over stdin, reads responses from stdout, validates the
MCP handshake + tool registration + tool invocation.

These tests use raw line-delimited JSON-RPC because that's the easiest
fixed contract; FastMCP's stdio transport speaks that protocol.
"""

from __future__ import annotations

import asyncio
import asyncio.subprocess as aio_subprocess
import contextlib
import json
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest


def _make_project(tmp_path: Path) -> Path:
    """Build a minimal consumer project layout the executor can boot from."""
    specs_dir = tmp_path / ".managed-agents" / "job_specs"
    specs_dir.mkdir(parents=True)
    (specs_dir / "add.yaml").write_text(
        dedent(
            """
            spec_ref: add
            job_type: math
            handler: add_handler
            timeout_seconds: 30
            allowed_inputs:
              x:
                type: integer
              y:
                type: integer
            """
        ).strip(),
        encoding="utf-8",
    )
    adapters_dir = tmp_path / ".managed-agents" / "adapters"
    adapters_dir.mkdir(parents=True)
    (adapters_dir / "local_executor.py").write_text(
        dedent(
            """
            async def add_handler(x, y):
                return {"sum": x + y}
            JOB_HANDLERS = {"add_handler": add_handler}
            """
        ),
        encoding="utf-8",
    )
    return tmp_path


async def _spawn_stdio_executor(workspace: Path) -> aio_subprocess.Process:
    """Spawn the executor as a subprocess via the cma CLI's stdio command."""
    return await aio_subprocess.create_subprocess_exec(
        sys.executable,
        "-m",
        "cma.cli",
        "executor",
        "stdio",
        "--workspace-root",
        str(workspace),
        "--project-name",
        "stdio-test",
        stdin=aio_subprocess.PIPE,
        stdout=aio_subprocess.PIPE,
        stderr=aio_subprocess.PIPE,
    )


async def _send(process: aio_subprocess.Process, msg: dict[str, Any]) -> None:
    """Write one JSON-RPC frame to the server's stdin (line-delimited)."""
    assert process.stdin is not None
    line = (json.dumps(msg) + "\n").encode("utf-8")
    process.stdin.write(line)
    await process.stdin.drain()


async def _read_response(
    process: aio_subprocess.Process,
    *,
    expect_id: int,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Read frames until we get one matching the expected id; skip notifications."""
    assert process.stdout is not None

    async def _next_frame() -> dict[str, Any] | None:
        line = await process.stdout.readline()
        if not line:
            return None
        return json.loads(line.decode("utf-8"))

    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(
                f"No JSON-RPC response with id={expect_id} within {timeout}s"
            )
        frame = await asyncio.wait_for(_next_frame(), timeout=remaining)
        if frame is None:
            raise RuntimeError("stdio executor closed its stdout unexpectedly")
        if frame.get("id") == expect_id:
            return frame
        # Otherwise it's a notification or a different response — keep reading.


async def _shutdown(process: aio_subprocess.Process) -> None:
    """Terminate the subprocess, with brief grace then kill."""
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except TimeoutError:
        process.kill()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=2.0)


@pytest.mark.asyncio
async def test_stdio_initialize_handshake(tmp_path: Path) -> None:
    """The server responds to initialize with serverInfo + protocolVersion."""
    workspace = _make_project(tmp_path)
    process = await _spawn_stdio_executor(workspace)
    try:
        await _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "cma-test", "version": "0.0"},
                },
            },
        )
        resp = await _read_response(process, expect_id=1)
        assert "result" in resp, f"unexpected response: {resp}"
        result = resp["result"]
        assert "serverInfo" in result
        assert result["serverInfo"]["name"] == "cma-local-executor"
        assert "protocolVersion" in result
    finally:
        await _shutdown(process)


@pytest.mark.asyncio
async def test_stdio_tools_list_advertises_executor_tools(tmp_path: Path) -> None:
    """tools/list returns the five executor tools with non-empty descriptions."""
    workspace = _make_project(tmp_path)
    process = await _spawn_stdio_executor(workspace)
    try:
        await _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "cma-test", "version": "0.0"},
                },
            },
        )
        await _read_response(process, expect_id=1)
        await _send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        await _send(
            process,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        resp = await _read_response(process, expect_id=2)
        assert "result" in resp, f"unexpected: {resp}"
        tools = resp["result"]["tools"]
        names = {t["name"] for t in tools}
        assert names == {
            "submit_job",
            "get_job_status",
            "get_job_result",
            "list_jobs",
            "cancel_job",
        }
        for tool in tools:
            assert tool["description"], f"tool {tool['name']} has empty description"
    finally:
        await _shutdown(process)


@pytest.mark.asyncio
async def test_stdio_submit_get_result_via_jsonrpc(tmp_path: Path) -> None:
    """End-to-end: submit_job → poll get_job_status → get_job_result."""
    workspace = _make_project(tmp_path)
    process = await _spawn_stdio_executor(workspace)
    try:
        # initialize + initialized notification
        await _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "cma-test", "version": "0.0"},
                },
            },
        )
        await _read_response(process, expect_id=1)
        await _send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        # submit_job
        await _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "submit_job",
                    "arguments": {"spec_ref": "add", "inputs": {"x": 5, "y": 7}},
                },
            },
        )
        submit_resp = await _read_response(process, expect_id=2)
        content = submit_resp["result"]["content"]
        envelope = json.loads(content[0]["text"])
        assert "job_id" in envelope
        job_id = envelope["job_id"]

        # Poll get_job_status until terminal (up to 15s)
        next_id = 3
        for _ in range(60):
            await _send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": next_id,
                    "method": "tools/call",
                    "params": {
                        "name": "get_job_status",
                        "arguments": {"job_id": job_id},
                    },
                },
            )
            status_resp = await _read_response(process, expect_id=next_id)
            next_id += 1
            status_envelope = json.loads(status_resp["result"]["content"][0]["text"])
            if status_envelope.get("status") not in {"queued", "running"}:
                break
            await asyncio.sleep(0.25)
        else:
            pytest.fail(f"job {job_id} never reached terminal state")

        # get_job_result
        await _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": next_id,
                "method": "tools/call",
                "params": {
                    "name": "get_job_result",
                    "arguments": {"job_id": job_id},
                },
            },
        )
        result_resp = await _read_response(process, expect_id=next_id)
        result_envelope = json.loads(result_resp["result"]["content"][0]["text"])
        assert result_envelope["status"] == "succeeded"
        assert result_envelope["metrics"] == {"sum": 12}
    finally:
        await _shutdown(process)
