"""Tests for cma.executor.job."""

from __future__ import annotations

import re

from cma.executor.job import Job, JobResult, JobStatus, new_job_id


class TestNewJobId:
    def test_prefix(self) -> None:
        assert new_job_id().startswith("cmaj_")

    def test_uniqueness(self) -> None:
        ids = {new_job_id() for _ in range(1000)}
        assert len(ids) == 1000

    def test_shape(self) -> None:
        # cmaj_ + url-safe chars (16 bytes → 22 chars typically)
        assert re.match(r"^cmaj_[A-Za-z0-9_-]+$", new_job_id())


class TestJobStatus:
    def test_terminal_states(self) -> None:
        assert JobStatus.SUCCEEDED.is_terminal
        assert JobStatus.FAILED.is_terminal
        assert JobStatus.CANCELLED.is_terminal
        assert JobStatus.TIMED_OUT.is_terminal

    def test_non_terminal_states(self) -> None:
        assert not JobStatus.QUEUED.is_terminal
        assert not JobStatus.RUNNING.is_terminal


class TestJob:
    def test_new_starts_queued(self) -> None:
        job = Job.new(spec_ref="x", job_type="t", inputs={"a": 1})
        assert job.status is JobStatus.QUEUED
        assert job.submitted_at  # non-empty
        assert job.started_at is None
        assert job.finished_at is None
        assert job.result is None
        assert job.error is None
        assert job.inputs == {"a": 1}

    def test_mark_running_records_pid(self) -> None:
        job = Job.new(spec_ref="x", job_type="t", inputs={})
        job.mark_running(pid=12345)
        assert job.status is JobStatus.RUNNING
        assert job.pid == 12345
        assert job.started_at is not None

    def test_mark_succeeded_sets_result(self) -> None:
        job = Job.new(spec_ref="x", job_type="t", inputs={})
        job.mark_running(pid=99)
        job.mark_succeeded({"metric_a": 1.5})
        assert job.status is JobStatus.SUCCEEDED
        assert job.result == {"metric_a": 1.5}
        assert job.finished_at is not None

    def test_mark_failed_sets_error(self) -> None:
        job = Job.new(spec_ref="x", job_type="t", inputs={})
        job.mark_failed("boom")
        assert job.status is JobStatus.FAILED
        assert job.error == "boom"

    def test_mark_timed_out_sets_default_error(self) -> None:
        job = Job.new(spec_ref="x", job_type="t", inputs={})
        job.mark_timed_out()
        assert job.status is JobStatus.TIMED_OUT
        assert job.error and "timeout" in job.error.lower()

    def test_summary_omits_inputs(self) -> None:
        """to_summary() must NOT include raw inputs — they may carry IP."""
        job = Job.new(spec_ref="example-strategy-v1", job_type="compute", inputs={"secret_param": 42})
        s = job.to_summary()
        assert "secret_param" not in str(s)
        assert s["id"] == job.id
        assert s["job_type"] == "compute"
        assert s["status"] == "queued"


class TestJobResult:
    def test_frozen(self) -> None:
        import dataclasses

        r = JobResult(job_id="cmaj_x", status=JobStatus.SUCCEEDED, metrics={"metric_a": 1.0})
        assert r.metrics["metric_a"] == 1.0
        # frozen=True means we can't mutate
        import pytest

        with pytest.raises(dataclasses.FrozenInstanceError):
            r.error = "x"  # type: ignore[misc]
