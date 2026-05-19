"""Kill-switch policy — the decision function called on every session idle.

This module deliberately ships with the policy **unimplemented**. The
choice has real consequences for any multi-session workflow (collateral
damage to sibling sessions vs. budget-overrun risk) and the operator must
own it explicitly. See :func:`kill_switch_policy` below for the contract
and `docs/webhook-receiver.md` for the trade-offs writeup.

The receiver computes a :class:`BudgetState` snapshot, calls the policy,
then dispatches the returned :class:`KillSwitchAction` via the configured
:class:`cma.webhook.actions.Actions` implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from cma.core.config import BudgetConfig


class KillSwitchAction(StrEnum):
    """Discrete outcomes the policy may return.

    Inheriting from ``str`` so the enum serializes cleanly into telemetry
    JSONL without a custom encoder.
    """

    #: Under all limits — receiver does nothing.
    IGNORE = "ignore"

    #: Over a limit, but only log + emit telemetry. No API action.
    #: Useful while the operator is observing the receiver's behavior
    #: before turning on destructive actions.
    NOTIFY_ONLY = "notify_only"

    #: Cancel the single session that triggered the event.
    CANCEL_SESSION = "cancel_session"

    #: Cancel EVERY active session in the project. Use when one session
    #: blowing through its budget likely means a runaway condition that
    #: affects siblings too (shared bug, infinite-tool-call loop, etc.).
    CANCEL_PROJECT = "cancel_project"

    #: Cancel the breaching session AND soft-lock the project — future
    #: ``cma session start`` will refuse until the operator clears the
    #: flag. Heaviest response; reserve for hard-cap breaches.
    EXHAUST_BUDGET = "exhaust_budget"


@dataclass(frozen=True, slots=True)
class BudgetState:
    """Snapshot the receiver hands to the policy.

    All values are computed BEFORE the policy is called. The policy is a
    pure function: ``BudgetState + BudgetConfig -> KillSwitchAction``.
    No I/O, no SDK calls — those happen in the action executor based on
    the policy's return value.
    """

    #: Session that emitted the event.
    session_id: str

    #: Project slug (matches :attr:`cma.core.config.ProjectMeta.name`).
    project: str

    #: USD spend on this session so far (cumulative across all events).
    session_cost_usd: float

    #: Total tokens reported for this session (input + output + cache).
    session_total_tokens: int

    #: USD spend across the project for today (UTC date of event arrival).
    daily_spend_usd: float

    #: Configured daily cap. Cached on the snapshot for one-shot logging.
    daily_cap_usd: float

    #: Configured per-session token cap.
    per_session_token_cap: int

    #: True iff ``session_total_tokens >= per_session_token_cap``.
    over_session_cap: bool

    #: True iff ``daily_spend_usd >= daily_cap_usd``.
    over_daily_cap: bool

    def is_breach(self) -> bool:
        """Convenience: any cap exceeded."""
        return self.over_session_cap or self.over_daily_cap


def kill_switch_policy(state: BudgetState, config: BudgetConfig) -> KillSwitchAction:
    """Decide what to do when a session reports idle.

    Called by the webhook receiver on every ``session.status_idled`` event,
    after :class:`BudgetState` has been computed from the event payload and
    the ledger. Pure function — return a :class:`KillSwitchAction` and the
    receiver dispatches the side effect.

    Considerations the operator weighs when implementing this:

    * **Collateral damage.** ``CANCEL_PROJECT`` interrupts sibling sessions
      that may not have misbehaved. Right call if one bug is likely
      reproducing across sessions; wrong call if sessions are independent
      experiments.
    * **Soft-lock vs. hard-lock.** ``EXHAUST_BUDGET`` is the heaviest
      response and requires manual operator clear-down. Use it when
      "spend keeps happening" is the failure mode you most fear.
    * **Observe-then-act.** ``NOTIFY_ONLY`` is a defensible starting point.
      The receiver still logs every decision to telemetry; promote to a
      destructive action once you've watched the log for a few days and
      confirmed the trigger conditions are tight.
    * **Honor ``config.kill_on_breach``.** If it's ``False``, the operator
      has globally disabled the kill switch — the policy should not return
      a destructive action regardless of breach state.

    :param state: Snapshot of session + daily spend at event-arrival time.
    :param config: The project's :class:`BudgetConfig`.
    :returns: One of the :class:`KillSwitchAction` enum values.
    """
    # Observe-first stance: BudgetConfig.kill_on_breach is the master switch.
    # When it's off, the receiver still emits the breach to telemetry via
    # NOTIFY_ONLY so the operator can watch decisions accumulate before
    # flipping the toggle. Sibling sessions are never collateral damage.
    if not state.is_breach():
        return KillSwitchAction.IGNORE
    if config.kill_on_breach:
        return KillSwitchAction.CANCEL_SESSION
    return KillSwitchAction.NOTIFY_ONLY
