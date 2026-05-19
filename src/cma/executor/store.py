"""SQLite-backed job state store for the local executor.

The store is the durable source of truth for "which jobs has the operator
submitted and what's their state?". The MCP server polls this on every
``get_job_status`` and ``get_job_result`` call; the runner mutates it as
subprocess lifecycle events happen.

Schema is intentionally narrow — we want the operator to be able to
``sqlite3 .state/executor.db 'SELECT * FROM jobs'`` and read the result
without needing toolkit knowledge.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cma.executor.job import Job, JobStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    spec_ref        TEXT NOT NULL,
    job_type        TEXT NOT NULL,
    inputs_json     TEXT NOT NULL,
    status          TEXT NOT NULL,
    submitted_at    TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    result_json     TEXT,
    error           TEXT,
    pid             INTEGER
);

CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs (status);
CREATE INDEX IF NOT EXISTS jobs_submitted_at_idx ON jobs (submitted_at);
"""


def _job_to_row(job: Job) -> tuple:
    """Pack a Job into the 11 columns the table expects."""
    return (
        job.id,
        job.spec_ref,
        job.job_type,
        json.dumps(job.inputs),
        job.status.value,
        job.submitted_at,
        job.started_at,
        job.finished_at,
        json.dumps(job.result) if job.result is not None else None,
        job.error,
        job.pid,
    )


def _row_to_job(row: tuple) -> Job:
    return Job(
        id=row[0],
        spec_ref=row[1],
        job_type=row[2],
        inputs=json.loads(row[3]),
        status=JobStatus(row[4]),
        submitted_at=row[5],
        started_at=row[6],
        finished_at=row[7],
        result=json.loads(row[8]) if row[8] else None,
        error=row[9],
        pid=row[10],
    )


class JobStore:
    """Append + update store for jobs. WAL mode for concurrent readers."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
        finally:
            conn.close()

    # -----------------------------------------------------------------
    # Mutators
    # -----------------------------------------------------------------

    def put(self, job: Job) -> None:
        """Upsert a job. Idempotent on (id)."""
        row = _job_to_row(job)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, spec_ref, job_type, inputs_json, status,
                    submitted_at, started_at, finished_at, result_json,
                    error, pid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at,
                    result_json=excluded.result_json,
                    error=excluded.error,
                    pid=excluded.pid
                """,
                row,
            )

    # -----------------------------------------------------------------
    # Readers
    # -----------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        job_type: str | None = None,
        limit: int = 100,
    ) -> list[Job]:
        """List jobs, newest first.

        :param status: Filter by status if given.
        :param job_type: Filter by job_type if given.
        :param limit: Cap result count. Default 100.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if job_type is not None:
            clauses.append("job_type = ?")
            params.append(job_type)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT * FROM jobs " + where
            + " ORDER BY submitted_at DESC LIMIT ?"
        )
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_job(r) for r in rows]
