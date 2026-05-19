"""Tests for cma.executor.notifications + the notifier integration in
ExecutorServer.

Covers:

* Payload construction is stable + omits sensitive fields.
* NullNotifier is a true no-op.
* CapturingNotifier records every dispatch in order.
* ExecutorServer's ``_on_job_changed`` dispatches via the injected notifier
  and swallows notifier errors.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from textwrap import dedent

import pytest

from cma.executor.job import Job
from cma.executor.notifications import (
    JOB_STATUS_CHANGED_METHOD,
    CapturingNotifier,
    NullNotifier,
    build_job_status_payload,
)
from cma.executor.server import ExecutorServer

# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------


def _make_job(**overrides) -> Job:
    job = Job.new(
        spec_ref=overrides.get("spec_ref", "test"),
        job_type=overrides.get("job_type", "t"),
        inputs=overrides.get("inputs", {"secret": "never-leak-me"}),
    )
    if "status" in overrides:
        job.status = overrides["status"]
    if "result" in overrides:
        job.result = overrides["result"]
    if "error" in overrides:
        job.error = overrides["error"]
    return job


class TestBuildPayload:
    def test_contains_safe_fields(self) -> None:
        job = _make_job()
        payload = build_job_status_payload(job)
        assert payload["job_id"] == job.id
        assert payload["status"] == job.status.value
        assert payload["spec_ref"] == job.spec_ref
        assert payload["job_type"] == job.job_type
        assert payload["submitted_at"] == job.submitted_at

    def test_omits_inputs_and_results(self) -> None:
        """The notification payload must NEVER carry raw inputs/results.

        Inputs may contain operator-only data; results may contain
        proprietary metrics. The notification is purely a 'state changed'
        hint; the consumer fetches substance via get_job_result.
        """
        job = _make_job(
            inputs={"proprietary_param": "leak-marker"},
            result={"private_metric": 1.5},
        )
        payload = build_job_status_payload(job)
        serialized = json.dumps(payload)
        assert "leak-marker" not in serialized
        assert "proprietary_param" not in serialized
        assert "private_metric" not in serialized

    def test_omits_error_messages(self) -> None:
        """Errors may include path leaks; payload omits them."""
        job = _make_job(error="FileNotFoundError: /home/user/secret/path")
        payload = build_job_status_payload(job)
        assert "secret" not in json.dumps(payload)
        assert "error" not in payload

    def test_started_finished_nullable(self) -> None:
        # A queued job has no started_at / finished_at.
        job = _make_job()
        payload = build_job_status_payload(job)
        assert payload["started_at"] is None
        assert payload["finished_at"] is None

    def test_terminal_job_has_finished_at(self) -> None:
        job = _make_job()
        job.mark_running(pid=42)
        job.mark_succeeded({"ok": True})
        payload = build_job_status_payload(job)
        assert payload["started_at"] is not None
        assert payload["finished_at"] is not None
        assert payload["status"] == "succeeded"


# ---------------------------------------------------------------------------
# Notifier implementations
# ---------------------------------------------------------------------------


class TestNullNotifier:
    def test_no_op(self) -> None:
        notifier = NullNotifier()
        # Calling it on a real job should be silent and never raise.
        notifier.notify_job_changed(_make_job())


class TestCapturingNotifier:
    def test_records_each_dispatch(self) -> None:
        notifier = CapturingNotifier()
        job1 = _make_job(spec_ref="a")
        job2 = _make_job(spec_ref="b")
        notifier.notify_job_changed(job1)
        notifier.notify_job_changed(job2)
        assert len(notifier.notifications) == 2
        assert notifier.notifications[0]["params"]["spec_ref"] == "a"
        assert notifier.notifications[1]["params"]["spec_ref"] == "b"

    def test_uses_canonical_method_name(self) -> None:
        notifier = CapturingNotifier()
        notifier.notify_job_changed(_make_job())
        assert notifier.notifications[0]["method"] == JOB_STATUS_CHANGED_METHOD


# ---------------------------------------------------------------------------
# ExecutorServer integration
# ---------------------------------------------------------------------------


def _setup_project(tmp_path: Path) -> Path:
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


class TestServerIntegration:
    def test_default_notifier_is_null(self, tmp_path: Path) -> None:
        workspace = _setup_project(tmp_path)
        server = ExecutorServer.make(workspace_root=workspace, project_name="t")
        assert isinstance(server.notifier, NullNotifier)

    def test_custom_notifier_dispatches_on_change(self, tmp_path: Path) -> None:
        workspace = _setup_project(tmp_path)
        capturer = CapturingNotifier()
        server = ExecutorServer.make(
            workspace_root=workspace, project_name="t", notifier=capturer
        )
        # Directly call the hook (simulates _run_and_notify finishing).
        job = _make_job(spec_ref="anything", job_type="t")
        server._on_job_changed(job)
        assert len(capturer.notifications) == 1
        assert capturer.notifications[0]["method"] == JOB_STATUS_CHANGED_METHOD

    def test_non_job_payload_silently_ignored(self, tmp_path: Path) -> None:
        """_on_job_changed must tolerate non-Job inputs (defensive)."""
        workspace = _setup_project(tmp_path)
        capturer = CapturingNotifier()
        server = ExecutorServer.make(
            workspace_root=workspace, project_name="t", notifier=capturer
        )
        server._on_job_changed("not a job")  # type: ignore[arg-type]
        server._on_job_changed(None)  # type: ignore[arg-type]
        server._on_job_changed({"job_id": "fake"})  # type: ignore[arg-type]
        assert capturer.notifications == []

    def test_notifier_error_swallowed(self, tmp_path: Path) -> None:
        """A failing notifier MUST NOT propagate — never on critical path."""
        workspace = _setup_project(tmp_path)

        class BrokenNotifier:
            def notify_job_changed(self, job):  # type: ignore[no-untyped-def]
                raise RuntimeError("transport down")

        server = ExecutorServer.make(
            workspace_root=workspace, project_name="t", notifier=BrokenNotifier()
        )
        # Must not raise:
        server._on_job_changed(_make_job())

    @pytest.mark.asyncio
    async def test_end_to_end_notification_via_real_run(self, tmp_path: Path) -> None:
        """Submit a real job → run to completion → capturer sees notification."""
        workspace = _setup_project(tmp_path)
        capturer = CapturingNotifier()
        server = ExecutorServer.make(
            workspace_root=workspace, project_name="t", notifier=capturer
        )

        submit_resp = json.loads(
            await server._submit_job(spec_ref="add", inputs={"x": 7, "y": 5})
        )
        job_id = submit_resp["job_id"]

        # Poll until terminal.
        for _ in range(100):
            status_resp = json.loads(await server._get_job_status(job_id))
            if status_resp["status"] not in {"queued", "running"}:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail(f"Job {job_id} never reached terminal state")

        # Exactly one notification should have fired — when _on_job_changed
        # was called at the end of _run_and_notify.
        assert len(capturer.notifications) == 1
        n = capturer.notifications[0]
        assert n["method"] == JOB_STATUS_CHANGED_METHOD
        assert n["params"]["job_id"] == job_id
        assert n["params"]["status"] == "succeeded"
        # And: payload contains no result substance.
        assert "12" not in json.dumps(n["params"])
