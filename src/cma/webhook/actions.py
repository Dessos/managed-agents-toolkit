"""Action executor — what actually happens when the policy says "kill".

The :class:`Actions` protocol is the seam between the policy decision and
the side-effecting world (SDK calls, telemetry, persisted state). Three
implementations ship:

* :class:`NullActions` — log + telemetry only. Default for the operator's
  current Max-only setup where the SDK can't be called (no API credit).
* :class:`SdkActions` — production wiring once API credit is available.
  Cancels sessions / archives via :class:`cma.api.sessions.Sessions`.
* :class:`CapturingActions` — test helper. Records every call.

Adding a new action type? Add the method to the protocol AND to all three
implementations. mypy will tell you if you missed one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ActionContext:
    """Context object passed to every action call."""

    session_id: str
    project: str
    reason: str


class Actions(Protocol):
    """The side-effecting surface the policy may invoke."""

    def cancel_session(self, ctx: ActionContext) -> None:
        """Interrupt a single session."""

    def cancel_project_sessions(self, ctx: ActionContext) -> None:
        """Interrupt every active session in this project."""

    def mark_budget_exhausted(self, ctx: ActionContext) -> None:
        """Soft-lock the project — future ``cma session start`` will refuse."""

    def notify(self, ctx: ActionContext) -> None:
        """Notify the operator (channel-dependent; may be a no-op)."""


class NullActions:
    """No-op implementation. Records nothing, calls nothing.

    Use this when the toolkit can't actually act (no API credit, no
    notification channel configured). Policy decisions still log via
    telemetry inside the receiver; this class just no-ops the side effect.
    """

    def cancel_session(self, ctx: ActionContext) -> None:
        return None

    def cancel_project_sessions(self, ctx: ActionContext) -> None:
        return None

    def mark_budget_exhausted(self, ctx: ActionContext) -> None:
        return None

    def notify(self, ctx: ActionContext) -> None:
        return None


@dataclass(slots=True)
class CapturingActions:
    """Test helper — records the sequence of calls for assertions."""

    calls: list[tuple[str, ActionContext]] = field(default_factory=list)

    def cancel_session(self, ctx: ActionContext) -> None:
        self.calls.append(("cancel_session", ctx))

    def cancel_project_sessions(self, ctx: ActionContext) -> None:
        self.calls.append(("cancel_project_sessions", ctx))

    def mark_budget_exhausted(self, ctx: ActionContext) -> None:
        self.calls.append(("mark_budget_exhausted", ctx))

    def notify(self, ctx: ActionContext) -> None:
        self.calls.append(("notify", ctx))

    def names(self) -> list[str]:
        """Return just the method names called, in order."""
        return [name for name, _ in self.calls]


# SdkActions intentionally NOT implemented in this slice. The operator
# runs Max-only (no API credit); building + testing it would be dead
# code. It lands in the same release that the operator first uses with
# API credit. See docs/webhook-receiver.md.
__all__ = ["ActionContext", "Actions", "CapturingActions", "NullActions"]
