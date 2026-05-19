"""Integration tests for cma.executor.runner + cma.executor.worker.

These spawn real subprocesses, so they're slower than pure-mock tests.
They're still under tests/unit/ because they don't need network or live
SDK access — just a working Python interpreter (sys.executable).

Coverage:
- Happy path: simple async handler returns a result; runner records it.
- Failure path: handler raises; runner records the error.
- Timeout path: handler sleeps past spec timeout; runner kills it.
- Adapter import failure: missing JOB_HANDLERS; runner records the error.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from cma.executor.job import JobStatus
from cma.executor.runner import JobRunner
from cma.executor.spec import JobSpec
from cma.executor.store import JobStore


def _write_adapter(tmp_path: Path, body: str) -> Path:
    p = tmp_path / ".managed-agents" / "adapters"
    p.mkdir(parents=True)
    adapter = p / "local_executor.py"
    adapter.write_text(dedent(body), encoding="utf-8")
    return adapter


@pytest.mark.asyncio
async def test_runner_happy_path(tmp_path: Path) -> None:
    """Spawn worker, handler returns a simple dict, runner records success."""
    adapter = _write_adapter(
        tmp_path,
        """
        async def add_one(value):
            return {"result": value + 1}
        JOB_HANDLERS = {"add_one": add_one}
        """,
    )
    store = JobStore(tmp_path / ".managed-agents" / ".state" / "exec.db")
    runner = JobRunner(store=store)
    spec = JobSpec(spec_ref="test", job_type="t", handler="add_one", timeout_seconds=30)
    job = runner.submit(spec=spec, inputs={"value": 41}, adapter_path=adapter)
    done = await runner.run_until_done(job=job, spec=spec, adapter_path=adapter)
    assert done.status is JobStatus.SUCCEEDED
    assert done.result == {"result": 42}


@pytest.mark.asyncio
async def test_runner_handler_raises(tmp_path: Path) -> None:
    """Handler raises; runner captures the error."""
    adapter = _write_adapter(
        tmp_path,
        """
        async def boom(**kwargs):
            raise RuntimeError("intentional test failure")
        JOB_HANDLERS = {"boom": boom}
        """,
    )
    store = JobStore(tmp_path / ".managed-agents" / ".state" / "exec.db")
    runner = JobRunner(store=store)
    spec = JobSpec(spec_ref="t", job_type="t", handler="boom", timeout_seconds=30)
    job = runner.submit(spec=spec, inputs={}, adapter_path=adapter)
    done = await runner.run_until_done(job=job, spec=spec, adapter_path=adapter)
    assert done.status is JobStatus.FAILED
    assert done.error is not None
    assert "intentional test failure" in done.error


@pytest.mark.asyncio
async def test_runner_timeout(tmp_path: Path) -> None:
    """Handler sleeps past timeout; runner SIGTERMs it."""
    adapter = _write_adapter(
        tmp_path,
        """
        import asyncio
        async def slow(**kwargs):
            await asyncio.sleep(60)
            return {"never": "gets here"}
        JOB_HANDLERS = {"slow": slow}
        """,
    )
    store = JobStore(tmp_path / ".managed-agents" / ".state" / "exec.db")
    runner = JobRunner(store=store)
    spec = JobSpec(spec_ref="t", job_type="t", handler="slow", timeout_seconds=1)
    job = runner.submit(spec=spec, inputs={}, adapter_path=adapter)
    done = await runner.run_until_done(job=job, spec=spec, adapter_path=adapter)
    assert done.status is JobStatus.TIMED_OUT
    assert done.error and "timeout" in done.error.lower()


@pytest.mark.asyncio
async def test_runner_missing_handlers_dict(tmp_path: Path) -> None:
    """Adapter without JOB_HANDLERS; worker reports the AttributeError."""
    adapter = _write_adapter(tmp_path, "x = 1\n")
    store = JobStore(tmp_path / ".managed-agents" / ".state" / "exec.db")
    runner = JobRunner(store=store)
    spec = JobSpec(spec_ref="t", job_type="t", handler="any", timeout_seconds=30)
    job = runner.submit(spec=spec, inputs={}, adapter_path=adapter)
    done = await runner.run_until_done(job=job, spec=spec, adapter_path=adapter)
    assert done.status is JobStatus.FAILED
    assert done.error is not None
    assert "JOB_HANDLERS" in done.error


@pytest.mark.asyncio
async def test_runner_non_dict_result(tmp_path: Path) -> None:
    """Handler returns non-dict; worker enforces contract."""
    adapter = _write_adapter(
        tmp_path,
        """
        async def bad(**kwargs):
            return "not a dict"
        JOB_HANDLERS = {"bad": bad}
        """,
    )
    store = JobStore(tmp_path / ".managed-agents" / ".state" / "exec.db")
    runner = JobRunner(store=store)
    spec = JobSpec(spec_ref="t", job_type="t", handler="bad", timeout_seconds=30)
    job = runner.submit(spec=spec, inputs={}, adapter_path=adapter)
    done = await runner.run_until_done(job=job, spec=spec, adapter_path=adapter)
    assert done.status is JobStatus.FAILED
    assert done.error is not None
    assert "dict" in done.error


@pytest.mark.asyncio
async def test_runner_inputs_passed_through(tmp_path: Path) -> None:
    """Inputs reach the handler via the worker's stdin → asyncio.run path."""
    adapter = _write_adapter(
        tmp_path,
        """
        async def echo(**kwargs):
            return {"got": kwargs}
        JOB_HANDLERS = {"echo": echo}
        """,
    )
    store = JobStore(tmp_path / ".managed-agents" / ".state" / "exec.db")
    runner = JobRunner(store=store)
    spec = JobSpec(spec_ref="t", job_type="t", handler="echo", timeout_seconds=30)
    job = runner.submit(
        spec=spec,
        inputs={"dataset": "dataset-alpha", "days": 100},
        adapter_path=adapter,
    )
    done = await runner.run_until_done(job=job, spec=spec, adapter_path=adapter)
    assert done.status is JobStatus.SUCCEEDED
    assert done.result == {"got": {"dataset": "dataset-alpha", "days": 100}}
