"""Tests for cma.core.budget.

Covers ledger persistence, daily aggregation, preflight gating, kill-switch.
"""

from __future__ import annotations

from pathlib import Path

from cma.core.budget import (
    BudgetLedger,
    UsageSnapshot,
    estimate_cost_usd,
    preflight,
    should_kill,
)
from cma.core.config import BudgetConfig


class TestEstimateCost:
    def test_zero_usage_zero_cost(self) -> None:
        assert estimate_cost_usd("claude-opus-4-7", UsageSnapshot()) == 0.0

    def test_uses_5m_split_when_provided(self) -> None:
        # If 5m + 1h splits are populated, cache_creation_input_tokens is
        # ignored (the split takes precedence).
        usage = UsageSnapshot(
            cache_creation_input_tokens=1_000_000,  # would be 5m fallback
            cache_creation_5m_tokens=500_000,        # actual 5m
            cache_creation_1h_tokens=500_000,        # actual 1h
        )
        # Opus 4.7: 5m = $6.25/Mt × 0.5 = 3.125; 1h = $10/Mt × 0.5 = 5.0
        assert estimate_cost_usd("claude-opus-4-7", usage) == 3.125 + 5.0

    def test_falls_back_to_5m_when_split_absent(self) -> None:
        # Common SDK case: only cache_creation_input_tokens is reported.
        usage = UsageSnapshot(cache_creation_input_tokens=1_000_000)
        # Falls back to 5m write rate = $6.25/Mt
        assert estimate_cost_usd("claude-opus-4-7", usage) == 6.25


class TestBudgetLedger:
    def test_record_and_daily_spend(self, tmp_path: Path) -> None:
        ledger = BudgetLedger(tmp_path / "cma.db")
        ledger.record_usage(
            session_id="sesn_test1",
            project="myproj",
            model="claude-sonnet-4-6",
            usage=UsageSnapshot(input_tokens=1_000_000, output_tokens=100_000),
            status="idle",
        )
        # Sonnet base $3 + output $15/Mt × 0.1 = 3 + 1.5 = 4.5
        spend = ledger.daily_spend("myproj")
        assert spend > 4.4 and spend < 4.6

    def test_two_projects_isolated(self, tmp_path: Path) -> None:
        ledger = BudgetLedger(tmp_path / "cma.db")
        ledger.record_usage(
            session_id="sesn_a",
            project="proj_a",
            model="claude-sonnet-4-6",
            usage=UsageSnapshot(input_tokens=1_000_000),
            status="idle",
        )
        ledger.record_usage(
            session_id="sesn_b",
            project="proj_b",
            model="claude-sonnet-4-6",
            usage=UsageSnapshot(input_tokens=2_000_000),
            status="idle",
        )
        assert ledger.daily_spend("proj_a") < ledger.daily_spend("proj_b")

    def test_upsert_replaces_not_accumulates(self, tmp_path: Path) -> None:
        # The session ledger upserts on session_id — a later "idle" should
        # replace the earlier "running" snapshot, not double-count.
        ledger = BudgetLedger(tmp_path / "cma.db")
        ledger.record_usage(
            session_id="sesn_x",
            project="p",
            model="claude-sonnet-4-6",
            usage=UsageSnapshot(input_tokens=500_000),
            status="running",
        )
        ledger.record_usage(
            session_id="sesn_x",
            project="p",
            model="claude-sonnet-4-6",
            usage=UsageSnapshot(input_tokens=1_000_000),
            status="idle",
        )
        # Spend should be $3.0 (just the final snapshot), not $4.5 (sum of both).
        spend = ledger.daily_spend("p")
        assert spend < 3.1


class TestPreflight:
    def test_allows_under_cap(self, tmp_path: Path) -> None:
        ledger = BudgetLedger(tmp_path / "cma.db")
        ledger.record_usage(
            session_id="sesn_a",
            project="p",
            model="claude-sonnet-4-6",
            usage=UsageSnapshot(input_tokens=1_000_000),
            status="idle",
        )
        decision = preflight(ledger, project="p", config=BudgetConfig(daily_usd_cap=25.0))
        assert decision.allowed
        assert decision.remaining_usd > 20.0

    def test_denies_at_cap(self, tmp_path: Path) -> None:
        ledger = BudgetLedger(tmp_path / "cma.db")
        # Spend $30 worth: 10M input tokens on Sonnet at $3/Mt = $30
        ledger.record_usage(
            session_id="sesn_a",
            project="p",
            model="claude-sonnet-4-6",
            usage=UsageSnapshot(input_tokens=10_000_000),
            status="idle",
        )
        decision = preflight(ledger, project="p", config=BudgetConfig(daily_usd_cap=25.0))
        assert not decision.allowed
        assert "daily cap reached" in decision.reason


class TestKillSwitch:
    def test_kill_disabled_when_kill_on_breach_false(self) -> None:
        cfg = BudgetConfig(per_session_token_cap=1000, kill_on_breach=False)
        assert not should_kill(None, session_id="any", config=cfg, last_total_tokens=999999)  # type: ignore[arg-type]

    def test_kill_at_cap(self) -> None:
        cfg = BudgetConfig(per_session_token_cap=1000)
        assert should_kill(None, session_id="any", config=cfg, last_total_tokens=1000)  # type: ignore[arg-type]

    def test_kill_under_cap(self) -> None:
        cfg = BudgetConfig(per_session_token_cap=1000)
        assert not should_kill(None, session_id="any", config=cfg, last_total_tokens=999)  # type: ignore[arg-type]
