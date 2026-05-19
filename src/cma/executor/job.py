"""Job + JobStatus + JobResult dataclasses for the local executor.

These are pure-logic types — no I/O, no SDK calls. The executor's runner
and server modules build on these.

A job moves through these states:

    queued ──▶ running ──▶ succeeded
                      └─▶ failed
                      └─▶ cancelled
                      └─▶ timed_out

Once in a terminal state, a job's record stays in the SQLite state DB until
explicitly purged. The cloud agent fetches the result via the MCP tool
``get_job_result``.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class JobStatus(StrEnum):
    """Lifecycle states for a job.

    Terminal states are ``succeeded``, ``failed``, ``cancelled``,
    ``timed_out``. Once a job lands in any of these, it stays put.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def is_terminal(self) -> bool:
        """True if the job is in a state that won't change without restart."""
        return self in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.TIMED_OUT,
        }


def new_job_id() -> str:
    """Generate a fresh opaque job ID.

    Format: ``cmaj_<22 url-safe chars>``. ``cmaj`` = CMA job, distinct prefix
    so cloud agents can validate the shape if they want. 22 chars of url-safe
    randomness = ~132 bits.
    """
    return f"cmaj_{secrets.token_urlsafe(16)}"


@dataclass(slots=True)
class Job:
    """One in-flight (or recently-completed) job.

    The instance is mutated in place as status transitions occur. The
    state DB (:mod:`cma.executor.runner`) persists snapshots after each
    transition.
    """

    id: str
    spec_ref: str  # opaque to the cloud agent; resolves locally to a JobSpec
    job_type: str  # echoed from the resolved spec for filtering
    inputs: dict[str, Any]
    status: JobStatus
    submitted_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    pid: int | None = None  # PID of the worker subprocess while running

    @classmethod
    def new(
        cls,
        *,
        spec_ref: str,
        job_type: str,
        inputs: dict[str, Any],
    ) -> Job:
        """Construct a fresh queued job."""
        return cls(
            id=new_job_id(),
            spec_ref=spec_ref,
            job_type=job_type,
            inputs=inputs,
            status=JobStatus.QUEUED,
            submitted_at=_utc_now_iso(),
        )

    def mark_running(self, pid: int) -> None:
        self.status = JobStatus.RUNNING
        self.started_at = _utc_now_iso()
        self.pid = pid

    def mark_succeeded(self, result: dict[str, Any]) -> None:
        self.status = JobStatus.SUCCEEDED
        self.finished_at = _utc_now_iso()
        self.result = result

    def mark_failed(self, error: str) -> None:
        self.status = JobStatus.FAILED
        self.finished_at = _utc_now_iso()
        self.error = error

    def mark_cancelled(self) -> None:
        self.status = JobStatus.CANCELLED
        self.finished_at = _utc_now_iso()

    def mark_timed_out(self) -> None:
        self.status = JobStatus.TIMED_OUT
        self.finished_at = _utc_now_iso()
        self.error = "Job exceeded its configured timeout."

    def to_summary(self) -> dict[str, Any]:
        """Compact view safe to return over MCP (no raw inputs leak)."""
        return {
            "id": self.id,
            "job_type": self.job_type,
            "status": self.status.value,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass(slots=True, frozen=True)
class JobResult:
    """Final result envelope returned to the cloud agent.

    Contains ONLY summary metrics — never raw artifacts, never strategy
    source. The :attr:`metrics` dict is whatever the adapter chose to
    return; the :attr:`deliverables_uri` (optional) is a signed Files-API
    URL the cloud agent can fetch.
    """

    job_id: str
    status: JobStatus
    metrics: dict[str, Any] = field(default_factory=dict)
    deliverables_uri: str | None = None
    error: str | None = None


def _utc_now_iso() -> str:
    """Current UTC time as an ISO 8601 string (seconds precision)."""
    return datetime.now(UTC).isoformat(timespec="seconds")
