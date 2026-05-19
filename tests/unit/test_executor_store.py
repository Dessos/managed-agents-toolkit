"""Tests for cma.executor.store — SQLite job persistence."""

from __future__ import annotations

from pathlib import Path

from cma.executor.job import Job, JobStatus
from cma.executor.store import JobStore


def _make_job(spec_ref: str = "test", job_type: str = "t", **kwargs) -> Job:
    return Job.new(spec_ref=spec_ref, job_type=job_type, inputs=kwargs.get("inputs", {}))


class TestJobStore:
    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "x.db")
        assert store.get("cmaj_missing") is None

    def test_put_then_get_roundtrips(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "x.db")
        job = _make_job(inputs={"a": 1, "b": [1, 2, 3]})
        store.put(job)
        retrieved = store.get(job.id)
        assert retrieved is not None
        assert retrieved.id == job.id
        assert retrieved.spec_ref == job.spec_ref
        assert retrieved.inputs == {"a": 1, "b": [1, 2, 3]}
        assert retrieved.status is JobStatus.QUEUED

    def test_put_updates_status(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "x.db")
        job = _make_job()
        store.put(job)
        job.mark_running(pid=12345)
        store.put(job)
        retrieved = store.get(job.id)
        assert retrieved is not None
        assert retrieved.status is JobStatus.RUNNING
        assert retrieved.pid == 12345
        assert retrieved.started_at is not None

    def test_put_persists_result(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "x.db")
        job = _make_job()
        store.put(job)
        job.mark_running(pid=1)
        job.mark_succeeded({"metric_a": 1.3, "result_count": 250})
        store.put(job)
        retrieved = store.get(job.id)
        assert retrieved is not None
        assert retrieved.status is JobStatus.SUCCEEDED
        assert retrieved.result == {"metric_a": 1.3, "result_count": 250}

    def test_put_persists_error(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "x.db")
        job = _make_job()
        store.put(job)
        job.mark_failed("kaboom")
        store.put(job)
        retrieved = store.get(job.id)
        assert retrieved is not None
        assert retrieved.error == "kaboom"


class TestListJobs:
    def test_returns_newest_first(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "x.db")
        # Insert in chronological order; expect reverse order in result.
        import time

        ids = []
        for i in range(5):
            job = _make_job(spec_ref=f"s{i}")
            store.put(job)
            ids.append(job.id)
            time.sleep(0.01)  # ensure distinct submitted_at
        jobs = store.list_jobs()
        # Newest first → reversed insertion order
        assert next(j.id for j in jobs) == ids[-1]

    def test_filter_by_status(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "x.db")
        for i in range(3):
            job = _make_job(spec_ref=f"r{i}")
            store.put(job)
        for i in range(2):
            job = _make_job(spec_ref=f"d{i}")
            job.mark_running(pid=i)
            job.mark_succeeded({"ok": True})
            store.put(job)
        running = store.list_jobs(status=JobStatus.QUEUED)
        done = store.list_jobs(status=JobStatus.SUCCEEDED)
        assert len(running) == 3
        assert len(done) == 2

    def test_filter_by_job_type(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "x.db")
        store.put(_make_job(spec_ref="a", job_type="compute"))
        store.put(_make_job(spec_ref="b", job_type="research"))
        store.put(_make_job(spec_ref="c", job_type="compute"))
        computes = store.list_jobs(job_type="compute")
        assert len(computes) == 2
        assert all(j.job_type == "compute" for j in computes)

    def test_respects_limit(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "x.db")
        for i in range(20):
            store.put(_make_job(spec_ref=f"j{i}"))
        assert len(store.list_jobs(limit=5)) == 5
