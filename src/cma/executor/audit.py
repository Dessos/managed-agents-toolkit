"""Audit log for the executor.

Every authenticated MCP tool call from a cloud agent emits one JSONL line.
The audit log is the operator's primary surface for answering "what did
the cloud agent do today?".

Fields recorded:

* ``ts`` — UTC ISO timestamp
* ``tool`` — MCP tool name (``submit_job``, ``get_job_status``, etc.)
* ``job_id`` — the job ID involved (or null for list operations)
* ``args_sanitized`` — sanitized args (telemetry.redact() applied)
* ``result_status`` — terminal job status if relevant
* ``elapsed_ms`` — time taken to handle the call
* ``client_id`` — first 8 chars of the bearer token hash (NOT the token);
  lets the operator distinguish multiple authenticated callers

The audit log is APPEND-ONLY. The operator views via ``cma audit --since=...``.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cma.telemetry.jsonl import TelemetryEmitter


def _client_fingerprint(token: str | None) -> str:
    """Stable 8-char fingerprint of the bearer token for log correlation.

    Returns ``"anon"`` if no token (should never happen on authenticated
    paths but kept for defensive logging).
    """
    if not token:
        return "anon"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


def audit_log_path(workspace_root: Path | str) -> Path:
    """Return the executor audit JSONL path for *workspace_root*.

    Single source of truth — the CLI reader, the AuditLogger writer, and
    any future analytics tooling all derive the path from this helper so
    they cannot drift.
    """
    return Path(workspace_root) / ".managed-agents" / ".state" / "executor-audit.jsonl"


class AuditLogger:
    """JSONL audit sink for executor MCP calls.

    Wraps :class:`TelemetryEmitter` with executor-specific defaults so
    that audit lines land in a per-project file separate from general
    toolkit telemetry.
    """

    def __init__(self, *, workspace_root: Path | str, project: str):
        # Audit log lives under the consumer project's .managed-agents/.state
        # so the operator can grep it without searching the toolkit dir.
        path = audit_log_path(workspace_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._emitter = TelemetryEmitter(path=path, project=project)

    def log(
        self,
        *,
        tool: str,
        client_token: str | None,
        args: Mapping[str, Any] | None = None,
        job_id: str | None = None,
        result_status: str | None = None,
        elapsed_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        """Emit one audit line.

        :param tool: MCP tool name (``submit_job``, etc.).
        :param client_token: Used only to derive the client fingerprint —
            never logged in raw form.
        :param args: Tool input args. Will be sanitized by the telemetry
            redactor before write.
        :param job_id: Optional job ID this call refers to.
        :param result_status: Optional terminal status string.
        :param elapsed_ms: Wall-clock duration of the call.
        :param error: If the call failed, the error message.
        """
        extra: dict[str, Any] = {
            "tool": tool,
            "client_id": _client_fingerprint(client_token),
        }
        if args is not None:
            extra["args"] = dict(args)
        if job_id is not None:
            extra["job_id"] = job_id
        if result_status is not None:
            extra["result_status"] = result_status
        if elapsed_ms is not None:
            extra["elapsed_ms"] = round(elapsed_ms, 2)
        if error is not None:
            extra["error"] = error
        self._emitter.emit(domain="cma.executor.audit", action=tool, extra=extra)

    @contextmanager
    def timed(
        self,
        *,
        tool: str,
        client_token: str | None,
        args: Mapping[str, Any] | None = None,
        job_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Context manager that auto-records elapsed time + result.

        Caller fills ``result["job_id"]``, ``result["result_status"]``, or
        ``result["error"]`` inside the block; the wrapper emits the audit
        line on exit (even on exception, capturing the exception type).

        Example::

            with audit.timed(tool="submit_job", client_token=tok, args=args) as out:
                job = do_work()
                out["job_id"] = job.id
                out["result_status"] = job.status
        """
        start = time.monotonic()
        captured: dict[str, Any] = {"job_id": job_id}
        try:
            yield captured
        except BaseException as exc:
            captured.setdefault("error", f"{type(exc).__name__}: {exc!s}")
            raise
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            self.log(
                tool=tool,
                client_token=client_token,
                args=args,
                job_id=captured.get("job_id"),
                result_status=captured.get("result_status"),
                elapsed_ms=elapsed_ms,
                error=captured.get("error"),
            )


def _utc_now_iso() -> str:
    """Current UTC time as ISO 8601."""
    return datetime.now(UTC).isoformat(timespec="seconds")
