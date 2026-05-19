"""Budget controller — daily + per-session spend ceilings with kill switch.

The controller has three jobs:

1. **Pre-flight gate**: refuse to start a new session if today's spend is
   already at or above the daily cap.
2. **Live monitoring**: each ``session.status_idled`` event arriving via
   webhook (or observed via stream) is checked against the per-session cap;
   over-budget sessions get auto-interrupted + archived.
3. **Reporting**: ``cma metrics budget`` reads the ledger and reports.

The ledger is a tiny SQLite database. Per-project, not global — separate
budgets per consumer project.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cma.core.config import BudgetConfig
from cma.core.pricing import for_model

# ---------------------------------------------------------------------------
# Cost helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    """One session's reported usage, in the SDK's shape."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    # Optional split for 1h vs 5m cache writes. The SDK currently reports
    # ``cache_creation_input_tokens`` as a single field. If a workflow uses
    # ``cache_ttl: 1h``, the toolkit attributes the writes to ``cache_1h``
    # at telemetry-emit time. Both default to 0; the sum should equal
    # ``cache_creation_input_tokens``.
    cache_creation_5m_tokens: int = 0
    cache_creation_1h_tokens: int = 0


def estimate_cost_usd(model_id: str, usage: UsageSnapshot) -> float:
    """Compute USD cost from a usage snapshot for the given model.

    If the snapshot didn't split cache creation by TTL, attributes the full
    ``cache_creation_input_tokens`` to 5-minute writes (the cheaper option,
    which is the SDK default). Caller can pass a pre-split snapshot if it
    has better info.
    """
    pricing = for_model(model_id)
    five_minute = usage.cache_creation_5m_tokens or usage.cache_creation_input_tokens
    one_hour = usage.cache_creation_1h_tokens
    return pricing.cost_usd(
        input_tokens=usage.input_tokens,
        cache_creation_5m_tokens=five_minute,
        cache_creation_1h_tokens=one_hour,
        cache_read_tokens=usage.cache_read_input_tokens,
        output_tokens=usage.output_tokens,
    )


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    project         TEXT NOT NULL,
    model           TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    last_updated_at TEXT NOT NULL,
    status          TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_creation_5m INTEGER NOT NULL DEFAULT 0,
    cache_creation_1h INTEGER NOT NULL DEFAULT 0,
    cache_read      INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL    NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS sessions_project_started_idx
    ON sessions (project, started_at);

CREATE INDEX IF NOT EXISTS sessions_status_idx
    ON sessions (status);
"""


class BudgetLedger:
    """SQLite-backed spend ledger.

    File-per-project. The DB file lives at ``.managed-agents/.state/cma.db``
    under the consumer project (gitignored).
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Connection context manager. WAL for concurrent readers + writers."""
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
        finally:
            conn.close()

    def record_usage(
        self,
        *,
        session_id: str,
        project: str,
        model: str,
        usage: UsageSnapshot,
        status: str,
    ) -> float:
        """Upsert a session's usage; return the session's total cost in USD."""
        cost = estimate_cost_usd(model, usage)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO sessions
                    (session_id, project, model, started_at, last_updated_at, status,
                     input_tokens, output_tokens, cache_creation_5m, cache_creation_1h,
                     cache_read, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    last_updated_at=excluded.last_updated_at,
                    status=excluded.status,
                    input_tokens=excluded.input_tokens,
                    output_tokens=excluded.output_tokens,
                    cache_creation_5m=excluded.cache_creation_5m,
                    cache_creation_1h=excluded.cache_creation_1h,
                    cache_read=excluded.cache_read,
                    cost_usd=excluded.cost_usd
                """,
                (
                    session_id,
                    project,
                    model,
                    now,
                    now,
                    status,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cache_creation_5m_tokens
                    or usage.cache_creation_input_tokens,
                    usage.cache_creation_1h_tokens,
                    usage.cache_read_input_tokens,
                    cost,
                ),
            )
        return cost

    def daily_spend(self, project: str, *, day: str | None = None) -> float:
        """Sum of ``cost_usd`` for all sessions on *day* (UTC date)."""
        day_str = day or datetime.now(UTC).date().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0.0) FROM sessions
                WHERE project = ? AND substr(started_at, 1, 10) = ?
                """,
                (project, day_str),
            ).fetchone()
        return float(row[0])

    def session_cost(self, session_id: str) -> float:
        """Return the current cost for *session_id*, or 0.0 if unknown."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(cost_usd, 0.0) FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return float(row[0]) if row else 0.0


# ---------------------------------------------------------------------------
# Decision helpers
# ---------------------------------------------------------------------------


class BudgetDecision:
    """Result of a budget check."""

    __slots__ = ("allowed", "reason", "remaining_usd")

    def __init__(self, *, allowed: bool, reason: str, remaining_usd: float) -> None:
        self.allowed = allowed
        self.reason = reason
        self.remaining_usd = remaining_usd

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return f"BudgetDecision(allowed={self.allowed}, reason={self.reason!r}, remaining={self.remaining_usd})"


def preflight(ledger: BudgetLedger, *, project: str, config: BudgetConfig) -> BudgetDecision:
    """Return whether a new session may be started, given today's spend."""
    spent = ledger.daily_spend(project)
    remaining = config.daily_usd_cap - spent
    if spent >= config.daily_usd_cap:
        return BudgetDecision(
            allowed=False,
            reason=(
                f"daily cap reached: spent ${spent:.2f} of ${config.daily_usd_cap:.2f} "
                f"(project={project})"
            ),
            remaining_usd=remaining,
        )
    return BudgetDecision(
        allowed=True,
        reason=f"under cap: ${remaining:.2f} of ${config.daily_usd_cap:.2f} remaining",
        remaining_usd=remaining,
    )


def should_kill(
    ledger: BudgetLedger,
    *,
    session_id: str,
    config: BudgetConfig,
    last_total_tokens: int,
) -> bool:
    """Return True if the per-session token cap has been exceeded."""
    if not config.kill_on_breach:
        return False
    return last_total_tokens >= config.per_session_token_cap
