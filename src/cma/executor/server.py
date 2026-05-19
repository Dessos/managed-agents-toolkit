"""MCP server exposing the local executor's tools to cloud agents.

Uses the ``mcp`` package's :class:`FastMCP` server. Tools follow the
docs-best-practice discipline:

* ``submit_job(job_type, spec_ref, inputs)`` — kick off a job, return job_id.
* ``get_job_status(job_id)`` — poll for current status.
* ``get_job_result(job_id)`` — fetch result envelope for a terminal job.
* ``list_jobs(status, job_type, limit)`` — operator-controlled visibility.
* ``cancel_job(job_id)`` — terminate a running job.

The cloud agent connects via Cloudflare Tunnel to this server's
``/mcp`` HTTP endpoint and authenticates via a static bearer token.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context

from cma.executor.adapter import find_adapter_path
from cma.executor.audit import AuditLogger
from cma.executor.job import JobStatus
from cma.executor.notifications import Notifier, NullNotifier
from cma.executor.runner import JobRunner
from cma.executor.spec import JobSpec, load_job_specs
from cma.executor.store import JobStore


class ExecutorServer:
    """Composition root for the local executor MCP daemon.

    Owns the store, runner, spec registry, audit logger, and the
    FastMCP server instance. Build via :meth:`make`.

    NOTE on auth: this class scaffolds the auth model (presented-token
    threading through audit logs) but the actual HTTP-level bearer check
    is performed by Starlette middleware applied at startup time — see
    :func:`build_starlette_app` in :mod:`cma.cli.executor` for that wiring.
    A request that reaches the tools below has ALREADY been authenticated
    by the middleware.

    NOTE on notifications: per the operator's "defensive: both polling
    AND notifications" decision, the tools below implement the polling
    contract fully. The notification path is delivered through the
    injected :class:`~cma.executor.notifications.Notifier`. The CLI
    entry points (``cma executor stdio`` / ``serve``) wire a
    :class:`~cma.executor.notifications.FastMCPSessionNotifier` which
    captures the originating session in ``submit_job`` and dispatches
    on terminal transitions. Notification consumption is still
    client-dependent — the polling path remains the authoritative contract.
    """

    def __init__(
        self,
        *,
        workspace_root: Path,
        project_name: str,
        store: JobStore,
        runner: JobRunner,
        specs: dict[str, JobSpec],
        audit: AuditLogger,
        notifier: Notifier | None = None,
    ):
        self.workspace_root = workspace_root
        self.project_name = project_name
        self.store = store
        self.runner = runner
        self.specs = specs
        self.audit = audit
        # Notifier is dependency-injected so production can wire FastMCP's
        # session-based notifier while tests use a CapturingNotifier.
        # NullNotifier is the safe default — no notifications, no errors.
        self.notifier: Notifier = notifier or NullNotifier()
        self._adapter_path = find_adapter_path(workspace_root)
        # Strong references to in-flight background tasks (jobs running async
        # in the background). Without this, the asyncio runtime may garbage-
        # collect the task mid-execution. Tasks are auto-removed on done.
        self._background_tasks: set[asyncio.Task[None]] = set()

        from mcp.server.fastmcp import FastMCP

        self.mcp = FastMCP(
            name="cma-local-executor",
            instructions=(
                "Local executor for the Claude Managed Agents toolkit. "
                "Submit jobs by spec_ref + inputs; the executor runs them in "
                "isolated subprocesses on the operator's machine and returns "
                "structured metrics. Strategy source and raw data never leave "
                "the local environment."
            ),
        )
        self._register_tools()

    # -----------------------------------------------------------------
    # Factory
    # -----------------------------------------------------------------

    @classmethod
    def make(
        cls,
        *,
        workspace_root: Path,
        project_name: str,
        notifier: Notifier | None = None,
    ) -> ExecutorServer:
        """Build all components from a project's ``.managed-agents/`` layout.

        :param notifier: Optional Notifier for job-status-changed events.
            Defaults to NullNotifier (silent). Production CLIs may wire a
            FastMCP-session notifier here; tests pass a CapturingNotifier.
        """
        state_dir = workspace_root / ".managed-agents" / ".state"
        state_dir.mkdir(parents=True, exist_ok=True)
        store = JobStore(state_dir / "executor.db")
        runner = JobRunner(store=store)
        specs = load_job_specs(workspace_root / ".managed-agents" / "job_specs")
        audit = AuditLogger(workspace_root=workspace_root, project=project_name)
        return cls(
            workspace_root=workspace_root,
            project_name=project_name,
            store=store,
            runner=runner,
            specs=specs,
            audit=audit,
            notifier=notifier,
        )

    # -----------------------------------------------------------------
    # Tool registration
    # -----------------------------------------------------------------

    def _register_tools(self) -> None:
        """Bind MCP tools to instance methods. Called once at construction."""
        # Capture self in closures so FastMCP's signature-only inspection
        # sees clean parameter names (no 'self' wart in tool schemas).

        @self.mcp.tool(description=(
            "Submit a new job by spec_ref. The spec_ref resolves to a local "
            "job specification declaring the job_type, handler, and allowed "
            "inputs. Returns the job_id which can be passed to get_job_status "
            "/ get_job_result. Example: submit_job(spec_ref='example-long-job', "
            "inputs={'dataset': 'dataset-alpha', 'chunk_size_steps': 100}). "
            "Validates inputs against the spec's allowed_inputs schema; rejects "
            "unknown keys and out-of-range values."
        ))
        async def submit_job(
            spec_ref: str,
            inputs: dict[str, Any],
            ctx: Context,
        ) -> str:
            return await self._submit_job(spec_ref=spec_ref, inputs=inputs, ctx=ctx)

        @self.mcp.tool(description=(
            "Get the current status of a job. Returns {id, status, "
            "submitted_at, started_at, finished_at, job_type}. Status is one "
            "of: queued, running, succeeded, failed, cancelled, timed_out. "
            "Poll this until status enters a terminal state (anything but "
            "queued/running), then call get_job_result for the metrics."
        ))
        async def get_job_status(job_id: str) -> str:
            return await self._get_job_status(job_id)

        @self.mcp.tool(description=(
            "Fetch the full result envelope for a terminal job. Returns "
            "{job_id, status, metrics, error}. If the job is still running, "
            "returns status='running' with no metrics. The metrics dict shape "
            "matches the job spec's output_summary contract. NEVER returns "
            "raw strategy source or full data — only the curated summary the "
            "operator declared safe to surface."
        ))
        async def get_job_result(job_id: str) -> str:
            return await self._get_job_result(job_id)

        @self.mcp.tool(description=(
            "List recent jobs with optional filters. Returns a list of job "
            "summaries (no raw inputs). status filter is one of: queued, "
            "running, succeeded, failed, cancelled, timed_out, or omitted "
            "for all. job_type filter narrows by category. limit caps the "
            "result count (default 50, max 200)."
        ))
        async def list_jobs(
            status: str | None = None,
            job_type: str | None = None,
            limit: int = 50,
        ) -> str:
            return await self._list_jobs(status=status, job_type=job_type, limit=limit)

        @self.mcp.tool(description=(
            "Cancel a running job. SIGTERMs the worker subprocess with a 5-second "
            "grace period before SIGKILL. Returns {job_id, cancelled} where "
            "cancelled is true iff the job was actually running. Idempotent: "
            "calling on a terminal job is a no-op."
        ))
        async def cancel_job(job_id: str) -> str:
            return await self._cancel_job(job_id)

    # -----------------------------------------------------------------
    # Tool implementations
    # -----------------------------------------------------------------

    async def _submit_job(
        self,
        *,
        spec_ref: str,
        inputs: dict[str, Any],
        ctx: Context | None = None,
    ) -> str:
        # No client_token at this layer — auth happens at HTTP middleware.
        # The audit log records the call regardless.
        with self.audit.timed(tool="submit_job", client_token=None, args={"spec_ref": spec_ref}) as out:
            if spec_ref not in self.specs:
                out["error"] = f"Unknown spec_ref: {spec_ref!r}"
                return json.dumps({"error": out["error"]})
            spec = self.specs[spec_ref]
            try:
                validated = spec.validate_inputs(inputs)
            except ValueError as exc:
                out["error"] = str(exc)
                return json.dumps({"error": str(exc)})

            job = self.runner.submit(
                spec=spec, inputs=validated, adapter_path=self._adapter_path
            )
            out["job_id"] = job.id

            # If the notifier wants per-job session binding (FastMCPSessionNotifier),
            # let it capture the active session now while ctx is in scope. Any
            # binding errors are non-fatal; the polling path is the contract.
            register = getattr(self.notifier, "register_for_job", None)
            if register is not None and ctx is not None:
                with contextlib.suppress(Exception):
                    register(job.id, ctx)

            # Kick off the subprocess in a fire-and-forget task; cloud agent
            # polls for completion via get_job_status / get_job_result. We
            # hold a reference on self._background_tasks so the task isn't
            # garbage-collected before completion (per RUF006 / asyncio docs).
            task = asyncio.create_task(self._run_and_notify(job=job, spec=spec))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            return json.dumps({"job_id": job.id, "status": job.status.value})

    async def _run_and_notify(self, *, job: object, spec: JobSpec) -> None:
        """Background coroutine: run the job, then fire change notification.

        Notification path is currently a no-op hook. Phase 2 wires it to
        FastMCP's server-initiated notifications once we've confirmed
        Anthropic forwards those to cloud agents.
        """
        # mypy: the Job type is imported lazily to avoid a cycle with the
        # outer module. We know the shape from submit().
        done = await self.runner.run_until_done(
            job=job,  # type: ignore[arg-type]
            spec=spec,
            adapter_path=self._adapter_path,
        )
        self._on_job_changed(done)

    def _on_job_changed(self, job: object) -> None:
        """Hook for notification emission. Delegates to the injected notifier.

        Fire-and-forget: any error in the notifier is swallowed silently.
        The job's terminal state is already in the store regardless — the
        notification is a hint to the client to poll sooner, not the
        authoritative source.
        """
        # Cast: object → Job. _run_and_notify always passes a Job.
        from cma.executor.job import Job

        if not isinstance(job, Job):
            return
        # Intentionally silent on any notifier failure. If you want visibility,
        # inject a Notifier implementation that logs.
        with contextlib.suppress(Exception):
            self.notifier.notify_job_changed(job)

    async def _get_job_status(self, job_id: str) -> str:
        with self.audit.timed(tool="get_job_status", client_token=None, job_id=job_id) as out:
            job = self.store.get(job_id)
            if job is None:
                out["error"] = "Job not found"
                return json.dumps({"error": "Job not found"})
            out["result_status"] = job.status.value
            return json.dumps(job.to_summary())

    async def _get_job_result(self, job_id: str) -> str:
        with self.audit.timed(tool="get_job_result", client_token=None, job_id=job_id) as out:
            job = self.store.get(job_id)
            if job is None:
                out["error"] = "Job not found"
                return json.dumps({"error": "Job not found"})
            out["result_status"] = job.status.value
            envelope = {
                "job_id": job.id,
                "status": job.status.value,
                "metrics": job.result or {},
                "error": job.error,
            }
            return json.dumps(envelope)

    async def _list_jobs(
        self,
        *,
        status: str | None,
        job_type: str | None,
        limit: int,
    ) -> str:
        with self.audit.timed(
            tool="list_jobs",
            client_token=None,
            args={"status": status, "job_type": job_type, "limit": limit},
        ):
            # Clamp limit defensively
            limit = max(1, min(limit, 200))
            status_enum = JobStatus(status) if status else None
            jobs = self.store.list_jobs(
                status=status_enum, job_type=job_type, limit=limit
            )
            return json.dumps([j.to_summary() for j in jobs])

    async def _cancel_job(self, job_id: str) -> str:
        with self.audit.timed(tool="cancel_job", client_token=None, job_id=job_id) as out:
            cancelled = await self.runner.cancel(job_id)
            out["result_status"] = "cancelled" if cancelled else "not_running"
            return json.dumps({"job_id": job_id, "cancelled": cancelled})
