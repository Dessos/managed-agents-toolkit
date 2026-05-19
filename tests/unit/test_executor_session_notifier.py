"""Tests for cma.executor.notifications.FastMCPSessionNotifier.

The session notifier is the production wiring for job-status notifications:
it captures the MCP session that submitted each job and dispatches a
``notifications/cma/job_status_changed`` JSON-RPC notification on that
session when the job reaches a terminal state.

These tests use fake Context + Session doubles rather than spinning up a
real FastMCP server. The contract under test is:

1. ``register_for_job(job_id, ctx)`` records the session.
2. ``notify_job_changed(job)`` schedules a send on the bound session.
3. The notification has the correct method + payload shape.
4. Failure modes are silent: no ctx, no session, no loop, send raises.
5. Per-job binding (not broadcast): a notification dispatches only to the
   session that originated the job.
6. The binding is consumed on the first terminal notification.

The ExecutorServer integration (binding from inside the ``submit_job``
tool closure) is covered by the existing end-to-end test in
test_executor_notifications.py — here we test the notifier surface in
isolation so failures point to a single layer.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from cma.executor.job import Job
from cma.executor.notifications import (
    JOB_STATUS_CHANGED_METHOD,
    FastMCPSessionNotifier,
    build_job_status_payload,
)

# ---------------------------------------------------------------------------
# Fakes — minimal stand-ins for FastMCP's Context / ServerSession
# ---------------------------------------------------------------------------


class _FakeSession:
    """Captures send_notification calls; no real transport."""

    def __init__(self) -> None:
        self.sent: list[Any] = []
        self.fail: bool = False

    async def send_notification(self, notification: Any) -> None:
        if self.fail:
            raise RuntimeError("transport down")
        self.sent.append(notification)


class _FakeRequestContext:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session


class _FakeContext:
    """Stand-in for mcp.server.fastmcp.Context.

    The notifier reads ``ctx.request_context.session``; we don't need to
    inherit from the real Context to satisfy that.
    """

    def __init__(self, session: _FakeSession | None = None, raise_on_access: bool = False) -> None:
        self._session = session
        self._raise = raise_on_access

    @property
    def request_context(self) -> _FakeRequestContext:
        if self._raise:
            # Mirrors how the real Context raises when accessed outside
            # an active request.
            raise ValueError("Context is not available outside of a request")
        assert self._session is not None
        return _FakeRequestContext(self._session)


def _make_job(**overrides: Any) -> Job:
    job = Job.new(
        spec_ref=overrides.get("spec_ref", "test"),
        job_type=overrides.get("job_type", "t"),
        inputs=overrides.get("inputs", {}),
    )
    if "status" in overrides:
        job.status = overrides["status"]
    return job


# ---------------------------------------------------------------------------
# register_for_job
# ---------------------------------------------------------------------------


class TestRegisterForJob:
    def test_records_session(self) -> None:
        notifier = FastMCPSessionNotifier()
        session = _FakeSession()
        ctx = _FakeContext(session=session)
        notifier.register_for_job("cmaj_abc", ctx)
        assert notifier._sessions_by_job["cmaj_abc"] is session

    def test_none_ctx_no_op(self) -> None:
        notifier = FastMCPSessionNotifier()
        notifier.register_for_job("cmaj_abc", None)
        assert notifier._sessions_by_job == {}

    def test_ctx_without_session_no_op(self) -> None:
        """If ctx.request_context.session raises, binding silently skips."""
        notifier = FastMCPSessionNotifier()
        bad_ctx = _FakeContext(raise_on_access=True)
        notifier.register_for_job("cmaj_abc", bad_ctx)  # type: ignore[arg-type]
        assert notifier._sessions_by_job == {}

    def test_distinct_jobs_distinct_sessions(self) -> None:
        notifier = FastMCPSessionNotifier()
        s_a, s_b = _FakeSession(), _FakeSession()
        notifier.register_for_job("cmaj_a", _FakeContext(s_a))
        notifier.register_for_job("cmaj_b", _FakeContext(s_b))
        assert notifier._sessions_by_job["cmaj_a"] is s_a
        assert notifier._sessions_by_job["cmaj_b"] is s_b


# ---------------------------------------------------------------------------
# notify_job_changed — async dispatch path
# ---------------------------------------------------------------------------


async def _flush_pending() -> None:
    """Drain currently-scheduled tasks. Two yields suffice in tests because
    the notifier creates one task that itself only awaits send_notification."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


class TestNotifyJobChanged:
    @pytest.mark.asyncio
    async def test_dispatches_to_bound_session(self) -> None:
        notifier = FastMCPSessionNotifier()
        session = _FakeSession()
        job = _make_job(spec_ref="s")
        notifier.register_for_job(job.id, _FakeContext(session))

        notifier.notify_job_changed(job)
        await _flush_pending()

        assert len(session.sent) == 1
        notification = session.sent[0]
        # The notification is a Pydantic model from mcp.types
        assert notification.method == JOB_STATUS_CHANGED_METHOD
        assert notification.params == build_job_status_payload(job)

    @pytest.mark.asyncio
    async def test_unbound_job_silent(self) -> None:
        """A job_id with no recorded session emits nothing."""
        notifier = FastMCPSessionNotifier()
        session = _FakeSession()
        # Register a DIFFERENT job
        notifier.register_for_job("cmaj_other", _FakeContext(session))
        notifier.notify_job_changed(_make_job())  # different id
        await _flush_pending()
        assert session.sent == []

    @pytest.mark.asyncio
    async def test_binding_consumed_on_first_notification(self) -> None:
        """The session map shrinks by one entry per notification — a
        defensive double-fire on the same job_id no-ops the second time."""
        notifier = FastMCPSessionNotifier()
        session = _FakeSession()
        job = _make_job()
        notifier.register_for_job(job.id, _FakeContext(session))

        notifier.notify_job_changed(job)
        await _flush_pending()
        notifier.notify_job_changed(job)  # second call, binding already popped
        await _flush_pending()

        assert len(session.sent) == 1
        assert job.id not in notifier._sessions_by_job

    @pytest.mark.asyncio
    async def test_per_job_binding_not_broadcast(self) -> None:
        """A notification reaches ONLY the session that submitted the job."""
        notifier = FastMCPSessionNotifier()
        session_a, session_b = _FakeSession(), _FakeSession()
        job_a = _make_job(spec_ref="a")
        job_b = _make_job(spec_ref="b")
        notifier.register_for_job(job_a.id, _FakeContext(session_a))
        notifier.register_for_job(job_b.id, _FakeContext(session_b))

        notifier.notify_job_changed(job_a)
        await _flush_pending()

        assert len(session_a.sent) == 1
        assert session_b.sent == []
        assert session_a.sent[0].params["spec_ref"] == "a"

    @pytest.mark.asyncio
    async def test_send_failure_swallowed(self) -> None:
        """Transport errors in send_notification MUST NOT propagate."""
        notifier = FastMCPSessionNotifier()
        session = _FakeSession()
        session.fail = True
        job = _make_job()
        notifier.register_for_job(job.id, _FakeContext(session))

        notifier.notify_job_changed(job)
        await _flush_pending()
        # No exception escaped; binding still consumed.
        assert job.id not in notifier._sessions_by_job
        assert session.sent == []  # send raised before append

    def test_no_event_loop_silent(self) -> None:
        """Called outside an event loop, the notifier drops the notification.

        The job's terminal state is already persisted; the notification is
        a hint, not a guarantee. This path covers test code that constructs
        the notifier + calls notify_job_changed synchronously (without
        spinning up a loop). The binding is consumed regardless (one
        attempt per job_id, success or not — simpler invariant than
        "retry-on-no-loop").
        """
        notifier = FastMCPSessionNotifier()
        session = _FakeSession()
        job = _make_job()
        notifier.register_for_job(job.id, _FakeContext(session))

        # No running loop here.
        notifier.notify_job_changed(job)

        assert session.sent == []
        assert job.id not in notifier._sessions_by_job


# ---------------------------------------------------------------------------
# Payload shape on the wire — verify the bytes-on-the-wire contract
# ---------------------------------------------------------------------------


class TestWireFormat:
    @pytest.mark.asyncio
    async def test_notification_dump_matches_jsonrpc_shape(self) -> None:
        """The dumped notification must be a valid JSON-RPC notification
        envelope: ``{method: ..., params: ...}`` (no id, no jsonrpc field;
        the session wrapper adds those)."""
        notifier = FastMCPSessionNotifier()
        session = _FakeSession()
        job = _make_job()
        notifier.register_for_job(job.id, _FakeContext(session))
        notifier.notify_job_changed(job)
        await _flush_pending()

        sent = session.sent[0]
        dumped = sent.model_dump(by_alias=True, mode="json", exclude_none=True)
        assert dumped["method"] == JOB_STATUS_CHANGED_METHOD
        assert dumped["params"]["job_id"] == job.id
        assert dumped["params"]["spec_ref"] == job.spec_ref
        # Round-trips through JSON cleanly (verifies no non-serializable types).
        assert json.loads(json.dumps(dumped))["method"] == JOB_STATUS_CHANGED_METHOD
