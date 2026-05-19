"""Subprocess job runner — spawns child Python per job, captures result.

Per the operator-confirmed architecture:

* One child process per job (strong isolation).
* Child reads request JSON from stdin, writes result JSON to stdout.
* Parent enforces wall-clock timeout via ``asyncio.wait_for``.
* On timeout, parent SIGTERMs then SIGKILLs the child.

The runner is async because we want one event loop to manage many jobs
concurrently (within the executor's own concurrency limit). Each job's
subprocess is fully isolated from others.

Subprocess invocation uses ``asyncio.create_subprocess_exec`` (argv list,
no shell interpolation) — the safe variant. The child is spawned with
``sys.executable`` so it runs in the same interpreter as the parent.
"""

from __future__ import annotations

import asyncio
import asyncio.subprocess as aio_subprocess
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

from cma.executor.job import Job
from cma.executor.spec import JobSpec
from cma.executor.store import JobStore


class JobRunner:
    """Run jobs as subprocesses. One runner instance per executor daemon.

    Caller workflow:

    1. ``runner.submit(spec, inputs, adapter_path)`` — creates a Job,
       persists to the store, returns the job_id.
    2. ``runner.run_until_done(job_id)`` — awaitable that resolves when the
       subprocess exits (or times out / is cancelled). Mutates the Job in
       the store throughout.
    3. ``runner.cancel(job_id)`` — terminates the running subprocess.
    """

    def __init__(self, *, store: JobStore):
        self.store = store
        # Map job_id -> running subprocess handle.
        self._running: dict[str, aio_subprocess.Process] = {}

    # -----------------------------------------------------------------
    # Submission
    # -----------------------------------------------------------------

    def submit(
        self,
        *,
        spec: JobSpec,
        inputs: dict[str, Any],
        adapter_path: Path,
    ) -> Job:
        """Persist a queued job; return it. Does NOT start the subprocess."""
        job = Job.new(
            spec_ref=spec.spec_ref,
            job_type=spec.job_type,
            inputs=inputs,
        )
        self.store.put(job)
        return job

    # -----------------------------------------------------------------
    # Execution
    # -----------------------------------------------------------------

    async def run_until_done(
        self,
        *,
        job: Job,
        spec: JobSpec,
        adapter_path: Path,
    ) -> Job:
        """Spawn the worker, await completion, persist final state. Return Job.

        Always returns a Job (never raises) so callers can rely on
        ``job.status`` for the outcome. Failures are recorded on the Job.
        """
        request_payload = json.dumps(
            {
                "adapter_path": str(adapter_path),
                "handler_name": spec.handler,
                "inputs": job.inputs,
            }
        )

        # Spawn the worker. We use sys.executable so the child runs with
        # the same Python interpreter as the parent (and therefore can
        # ``import cma.executor.worker``).
        try:
            process = await aio_subprocess.create_subprocess_exec(
                sys.executable,
                "-m",
                "cma.executor.worker",
                stdin=aio_subprocess.PIPE,
                stdout=aio_subprocess.PIPE,
                stderr=aio_subprocess.PIPE,
            )
        except Exception as exc:
            job.mark_failed(f"Failed to spawn worker: {type(exc).__name__}: {exc!s}")
            self.store.put(job)
            return job

        assert process.pid is not None
        job.mark_running(pid=process.pid)
        self.store.put(job)
        self._running[job.id] = process

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(input=request_payload.encode("utf-8")),
                timeout=spec.timeout_seconds,
            )
        except TimeoutError:
            await _terminate_process(process, grace_seconds=5.0)
            job.mark_timed_out()
            self.store.put(job)
            return job
        finally:
            self._running.pop(job.id, None)

        # Subprocess exited. Read the JSON envelope from stdout.
        try:
            stdout_str = stdout_bytes.decode("utf-8").strip()
            last_line = stdout_str.splitlines()[-1] if stdout_str else ""
            envelope = json.loads(last_line) if last_line else {}
        except (json.JSONDecodeError, IndexError, UnicodeDecodeError) as exc:
            stderr_str = stderr_bytes.decode("utf-8", errors="replace")[-2000:]
            job.mark_failed(
                f"Could not parse worker output: {type(exc).__name__}: {exc!s}. "
                f"stderr tail: {stderr_str!r}"
            )
            self.store.put(job)
            return job

        if envelope.get("status") == "ok":
            job.mark_succeeded(envelope.get("result", {}))
        elif envelope.get("status") == "error":
            stderr_str = stderr_bytes.decode("utf-8", errors="replace")[-500:]
            err_msg = envelope.get("error", "unknown worker error")
            if stderr_str:
                err_msg = f"{err_msg} (stderr tail: {stderr_str!r})"
            job.mark_failed(err_msg)
        else:
            stderr_str = stderr_bytes.decode("utf-8", errors="replace")[-500:]
            job.mark_failed(
                f"Worker exited {process.returncode} with unexpected envelope "
                f"{envelope!r}. stderr tail: {stderr_str!r}"
            )

        self.store.put(job)
        return job

    # -----------------------------------------------------------------
    # Cancellation
    # -----------------------------------------------------------------

    async def cancel(self, job_id: str) -> bool:
        """Terminate a running job. Returns True if it was running.

        Idempotent — calling cancel on a non-running or already-completed
        job returns False. The store record is updated to ``cancelled`` if
        the cancel actually fired.
        """
        process = self._running.get(job_id)
        if process is None:
            return False
        await _terminate_process(process, grace_seconds=5.0)
        job = self.store.get(job_id)
        if job is not None and not job.status.is_terminal:
            job.mark_cancelled()
            self.store.put(job)
        return True


async def _terminate_process(
    process: aio_subprocess.Process,
    *,
    grace_seconds: float,
) -> None:
    """SIGTERM then SIGKILL after grace period. Tolerant of already-dead procs."""
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except (ProcessLookupError, OSError):
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        return
    except TimeoutError:
        pass
    try:
        process.kill()
    except (ProcessLookupError, OSError):
        return
    # Best-effort wait after kill — return regardless.
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=2.0)
