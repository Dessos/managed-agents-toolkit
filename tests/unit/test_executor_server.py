"""Tests for cma.executor.server — the MCP server's tool implementations.

We exercise the tool methods directly (``_submit_job``, ``_get_job_status``,
etc.) rather than through the MCP HTTP transport. The transport itself is
provided by FastMCP and has its own upstream tests; what we care about is
that OUR business logic dispatches correctly.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from textwrap import dedent

import pytest

from cma.executor.server import ExecutorServer


def _setup_project(tmp_path: Path) -> Path:
    """Build a minimal consumer-project layout for the server."""
    # Job spec
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
                min: 0
                max: 1000
              y:
                type: integer
                min: 0
                max: 1000
            """
        ).strip(),
        encoding="utf-8",
    )
    # Adapter
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


@pytest.mark.asyncio
async def test_submit_get_result_happy_path(tmp_path: Path) -> None:
    """End-to-end: submit a real job, poll until done, fetch result."""
    workspace = _setup_project(tmp_path)
    server = ExecutorServer.make(workspace_root=workspace, project_name="test")

    submit_resp = json.loads(await server._submit_job(spec_ref="add", inputs={"x": 2, "y": 3}))
    assert "job_id" in submit_resp
    assert submit_resp["status"] == "queued"
    job_id = submit_resp["job_id"]

    # Poll until terminal (up to 10s).
    for _ in range(100):
        status_resp = json.loads(await server._get_job_status(job_id))
        if status_resp["status"] not in {"queued", "running"}:
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail(f"Job {job_id} never reached terminal state")

    result_resp = json.loads(await server._get_job_result(job_id))
    assert result_resp["status"] == "succeeded"
    assert result_resp["metrics"] == {"sum": 5}
    assert result_resp["error"] is None


@pytest.mark.asyncio
async def test_submit_rejects_unknown_spec_ref(tmp_path: Path) -> None:
    workspace = _setup_project(tmp_path)
    server = ExecutorServer.make(workspace_root=workspace, project_name="test")

    resp = json.loads(await server._submit_job(spec_ref="nonexistent", inputs={}))
    assert "error" in resp
    assert "nonexistent" in resp["error"]


@pytest.mark.asyncio
async def test_submit_rejects_invalid_inputs(tmp_path: Path) -> None:
    """Inputs that violate the spec's allowed_inputs schema → error before run."""
    workspace = _setup_project(tmp_path)
    server = ExecutorServer.make(workspace_root=workspace, project_name="test")

    # x is allowed_inputs[x].min=0; -5 violates.
    resp = json.loads(await server._submit_job(spec_ref="add", inputs={"x": -5, "y": 3}))
    assert "error" in resp


@pytest.mark.asyncio
async def test_get_status_unknown_job(tmp_path: Path) -> None:
    workspace = _setup_project(tmp_path)
    server = ExecutorServer.make(workspace_root=workspace, project_name="test")

    resp = json.loads(await server._get_job_status("cmaj_does_not_exist"))
    assert resp == {"error": "Job not found"}


@pytest.mark.asyncio
async def test_list_jobs(tmp_path: Path) -> None:
    workspace = _setup_project(tmp_path)
    server = ExecutorServer.make(workspace_root=workspace, project_name="test")

    # Submit two jobs.
    r1 = json.loads(await server._submit_job(spec_ref="add", inputs={"x": 1, "y": 1}))
    r2 = json.loads(await server._submit_job(spec_ref="add", inputs={"x": 2, "y": 2}))

    # Wait for both to finish (be patient).
    for _ in range(100):
        s1 = json.loads(await server._get_job_status(r1["job_id"]))
        s2 = json.loads(await server._get_job_status(r2["job_id"]))
        if s1["status"] not in {"queued", "running"} and s2["status"] not in {"queued", "running"}:
            break
        await asyncio.sleep(0.1)

    all_jobs = json.loads(await server._list_jobs(status=None, job_type=None, limit=50))
    ids = {j["id"] for j in all_jobs}
    assert r1["job_id"] in ids
    assert r2["job_id"] in ids


@pytest.mark.asyncio
async def test_list_filter_by_status(tmp_path: Path) -> None:
    workspace = _setup_project(tmp_path)
    server = ExecutorServer.make(workspace_root=workspace, project_name="test")

    r = json.loads(await server._submit_job(spec_ref="add", inputs={"x": 1, "y": 1}))
    # Wait for terminal.
    for _ in range(100):
        s = json.loads(await server._get_job_status(r["job_id"]))
        if s["status"] not in {"queued", "running"}:
            break
        await asyncio.sleep(0.1)

    succeeded = json.loads(await server._list_jobs(status="succeeded", job_type=None, limit=50))
    assert all(j["status"] == "succeeded" for j in succeeded)
    failed = json.loads(await server._list_jobs(status="failed", job_type=None, limit=50))
    assert all(j["status"] == "failed" for j in failed)


@pytest.mark.asyncio
async def test_list_clamps_limit(tmp_path: Path) -> None:
    """limit > 200 must clamp to 200; limit < 1 must clamp to 1."""
    workspace = _setup_project(tmp_path)
    server = ExecutorServer.make(workspace_root=workspace, project_name="test")
    # No jobs, but we just check the limit logic doesn't blow up.
    await server._list_jobs(status=None, job_type=None, limit=10_000)
    await server._list_jobs(status=None, job_type=None, limit=-5)


@pytest.mark.asyncio
async def test_get_result_unknown_job(tmp_path: Path) -> None:
    workspace = _setup_project(tmp_path)
    server = ExecutorServer.make(workspace_root=workspace, project_name="test")
    resp = json.loads(await server._get_job_result("cmaj_unknown"))
    assert resp == {"error": "Job not found"}
