"""MCP notification emission for the executor.

When a job transitions between states, the executor can emit a structured
notification so connected MCP clients can react without polling. The
notification method is namespaced: ``notifications/cma/job_status_changed``.

Important caveat: **client consumption is not guaranteed.** Clients
(Claude Code, Managed Agents cloud agents) MAY log custom notifications
without acting on them. The polling path via ``get_job_status`` remains
the authoritative contract; notifications are a hint, not a guarantee.

This module separates the notification payload + dispatcher from the
underlying transport. The :class:`Notifier` protocol is what
:class:`ExecutorServer` calls; concrete implementations adapt to FastMCP's
session API or to a test capture-list.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any, Protocol

from cma.executor.job import Job

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


# Notification method name; namespaced under cma/ so it doesn't clash
# with MCP-standard notifications (e.g. resources/updated).
JOB_STATUS_CHANGED_METHOD = "notifications/cma/job_status_changed"


def build_job_status_payload(job: Job) -> dict[str, Any]:
    """Construct the params dict for a job_status_changed notification.

    Schema (stable contract for downstream consumers):

    .. code-block:: json

        {
          "job_id": "cmaj_xxx",
          "status": "succeeded" | "failed" | "cancelled" | "timed_out" | "running" | "queued",
          "spec_ref": "the-job-spec-slug",
          "job_type": "the-job-spec-category",
          "submitted_at": "iso-8601",
          "started_at": "iso-8601 or null",
          "finished_at": "iso-8601 or null"
        }

    Never includes raw inputs, raw results, or error messages — those go
    via ``get_job_result``. The notification is JUST a hint that state
    changed; the consumer should call ``get_job_result`` for substance.
    """
    return {
        "job_id": job.id,
        "status": job.status.value,
        "spec_ref": job.spec_ref,
        "job_type": job.job_type,
        "submitted_at": job.submitted_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


# ---------------------------------------------------------------------------
# Notifier protocol + null implementation
# ---------------------------------------------------------------------------


class Notifier(Protocol):
    """Anything that can dispatch a job-status-changed notification."""

    def notify_job_changed(self, job: Job) -> None:
        """Fire-and-forget: must not raise; must not block longer than ~10ms.

        Implementations should swallow transport errors silently — the
        notification path is never on the job's critical path. The job's
        terminal state is already persisted in the store; this is purely
        a hint to the client.
        """
        ...


class NullNotifier:
    """No-op notifier. Default; safe when no transport is wired."""

    def notify_job_changed(self, job: Job) -> None:
        return


# ---------------------------------------------------------------------------
# Capture-list notifier (test helper)
# ---------------------------------------------------------------------------


class CapturingNotifier:
    """Records every notification dispatch in-memory. For tests."""

    def __init__(self) -> None:
        self.notifications: list[dict[str, Any]] = []

    def notify_job_changed(self, job: Job) -> None:
        self.notifications.append(
            {
                "method": JOB_STATUS_CHANGED_METHOD,
                "params": build_job_status_payload(job),
            }
        )


# ---------------------------------------------------------------------------
# FastMCP session-backed notifier
# ---------------------------------------------------------------------------


class FastMCPSessionNotifier:
    """Dispatches job_status_changed notifications over the MCP session
    that originated each job.

    Pairing model — **per-job session binding**, not broadcast. When
    ``submit_job`` is called, ``ExecutorServer`` extracts the active
    ``Context`` and calls :meth:`register_for_job`, recording
    ``(job_id -> session)``. When the job reaches a terminal state and the
    server fires :meth:`notify_job_changed`, the recorded session is
    looked up by ``job_id`` and the notification sent only to that one
    client. Other connected clients see nothing about this job.

    Why per-job binding rather than broadcast:

    * For the operator's current stdio path (one client per process),
      per-job binding is equivalent to broadcast — there's only ever one
      session.
    * For the future HTTP path (Managed Agents cloud agent + Claude Code
      attached concurrently), broadcast would leak job IDs and status
      transitions across clients that didn't submit them. Per-job binding
      keeps each client's view scoped to its own submissions, mirroring
      how ``get_job_result`` already behaves (a client can only act on
      ``job_id`` strings it has been told about).
    * The cost is one dict entry per pending job, freed on the first
      terminal-state notification.

    Failures (no session bound, session already torn down, transport
    error mid-send) are silent. The notification is never on the job's
    critical path; the terminal state is already committed to the store
    and the client can poll ``get_job_status`` regardless.
    """

    def __init__(self) -> None:
        self._sessions_by_job: dict[str, Any] = {}
        # Strong refs to in-flight send tasks. Prevents GC of a scheduled
        # coroutine before it completes — mirrors ExecutorServer's pattern
        # for background subprocess tasks.
        self._send_tasks: set[asyncio.Task[None]] = set()

    def register_for_job(self, job_id: str, ctx: Context | None) -> None:
        """Bind a job_id to the originating MCP session.

        Called by ``ExecutorServer._submit_job`` right after the job is
        accepted. ``ctx`` is the FastMCP-injected Context for the
        submitting tool call; ``None`` is tolerated (e.g. tests that call
        ``_submit_job`` directly with no transport).
        """
        if ctx is None:
            return
        try:
            session = ctx.request_context.session
        except (AttributeError, ValueError):
            # ValueError: Context raises when accessed outside a request.
            # AttributeError: defensive against future API drift.
            return
        self._sessions_by_job[job_id] = session

    def notify_job_changed(self, job: Job) -> None:
        # Look up + remove in one step — the contract is one notification
        # per job, on its terminal transition. If notify_job_changed fires
        # twice for the same job_id (shouldn't, but defensive), the
        # second call no-ops because the binding is gone.
        session = self._sessions_by_job.pop(job.id, None)
        if session is None:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Called from outside an event loop (synchronous test path).
            # The async send needs a loop; drop the notification rather
            # than block. The job's terminal state is already persisted.
            return

        # Build the JSON-RPC notification. We instantiate the parametrised
        # base Notification (dict params, free-form method string) because
        # the canonical ServerNotification union doesn't include custom
        # cma/* methods — they aren't in MCP's spec, they're our extension.
        import mcp.types as mcp_types

        notification = mcp_types.Notification[dict[str, Any], str](
            method=JOB_STATUS_CHANGED_METHOD,
            params=build_job_status_payload(job),
        )

        async def _send() -> None:
            with contextlib.suppress(Exception):
                await session.send_notification(notification)

        task = loop.create_task(_send())
        self._send_tasks.add(task)
        task.add_done_callback(self._send_tasks.discard)
