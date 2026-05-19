"""Tests for cma.webhook.policy.kill_switch_policy.

Observe-first policy:

* under any cap          → IGNORE
* over cap + kill=True   → CANCEL_SESSION
* over cap + kill=False  → NOTIFY_ONLY

These are the four state combinations the operator chose. Sibling-session
collateral (CANCEL_PROJECT, EXHAUST_BUDGET) is intentionally never used.
"""

from __future__ import annotations

from cma.core.config import BudgetConfig
from cma.webhook.policy import BudgetState, KillSwitchAction, kill_switch_policy


def _state(
    *, over_session_cap: bool = False, over_daily_cap: bool = False
) -> BudgetState:
    """Build a BudgetState varying only the two flags under test."""
    return BudgetState(
        session_id="sess_test",
        project="test-proj",
        session_cost_usd=1.0,
        session_total_tokens=1000,
        daily_spend_usd=2.0,
        daily_cap_usd=10.0,
        per_session_token_cap=1_000_000,
        over_session_cap=over_session_cap,
        over_daily_cap=over_daily_cap,
    )


_KILL_ON = BudgetConfig(
    daily_usd_cap=10.0,
    per_session_token_cap=1_000_000,
    warn_at_pct=80,
    kill_on_breach=True,
)
_KILL_OFF = BudgetConfig(
    daily_usd_cap=10.0,
    per_session_token_cap=1_000_000,
    warn_at_pct=80,
    kill_on_breach=False,
)


class TestUnderCap:
    def test_under_cap_kill_on_returns_ignore(self) -> None:
        assert kill_switch_policy(_state(), _KILL_ON) is KillSwitchAction.IGNORE

    def test_under_cap_kill_off_returns_ignore(self) -> None:
        # No notification spam on routine idles even when the kill switch is
        # off — under-cap events stay silent.
        assert kill_switch_policy(_state(), _KILL_OFF) is KillSwitchAction.IGNORE


class TestOverCapKillOn:
    def test_over_session_cap_cancels_session(self) -> None:
        s = _state(over_session_cap=True)
        assert kill_switch_policy(s, _KILL_ON) is KillSwitchAction.CANCEL_SESSION

    def test_over_daily_cap_cancels_session(self) -> None:
        s = _state(over_daily_cap=True)
        assert kill_switch_policy(s, _KILL_ON) is KillSwitchAction.CANCEL_SESSION

    def test_over_both_caps_cancels_session(self) -> None:
        s = _state(over_session_cap=True, over_daily_cap=True)
        assert kill_switch_policy(s, _KILL_ON) is KillSwitchAction.CANCEL_SESSION


class TestOverCapKillOff:
    def test_over_session_cap_notify_only(self) -> None:
        s = _state(over_session_cap=True)
        assert kill_switch_policy(s, _KILL_OFF) is KillSwitchAction.NOTIFY_ONLY

    def test_over_daily_cap_notify_only(self) -> None:
        s = _state(over_daily_cap=True)
        assert kill_switch_policy(s, _KILL_OFF) is KillSwitchAction.NOTIFY_ONLY


class TestNonDestructive:
    def test_policy_never_returns_cancel_project(self) -> None:
        # Sibling sessions are never collateral damage in this policy.
        for s in [
            _state(),
            _state(over_session_cap=True),
            _state(over_daily_cap=True),
            _state(over_session_cap=True, over_daily_cap=True),
        ]:
            for cfg in [_KILL_ON, _KILL_OFF]:
                assert kill_switch_policy(s, cfg) is not KillSwitchAction.CANCEL_PROJECT
                assert kill_switch_policy(s, cfg) is not KillSwitchAction.EXHAUST_BUDGET


class TestBudgetStateHelper:
    def test_is_breach_false_when_neither(self) -> None:
        assert _state().is_breach() is False

    def test_is_breach_true_when_session(self) -> None:
        assert _state(over_session_cap=True).is_breach() is True

    def test_is_breach_true_when_daily(self) -> None:
        assert _state(over_daily_cap=True).is_breach() is True

    def test_is_breach_true_when_both(self) -> None:
        assert _state(over_session_cap=True, over_daily_cap=True).is_breach() is True
