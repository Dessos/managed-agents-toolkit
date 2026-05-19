"""FastAPI app — accepts Anthropic webhook deliveries, runs the kill switch.

Flow per delivery:

1. Read the raw body and the three Svix headers.
2. Verify the signature via :mod:`cma.webhook.signature`. Fail closed (400)
   on any signature error — fail-closed is the only safe posture for
   credential-bearing payloads.
3. Parse the envelope. Telemetry-log every type, even types the toolkit
   doesn't act on (forensic record).
4. For ``session.status_idled`` events: reconcile usage with the budget
   ledger, compute :class:`BudgetState`, call the operator-authored
   :func:`cma.webhook.policy.kill_switch_policy`, dispatch via
   :class:`cma.webhook.actions.Actions`.
5. Always return ``204 No Content`` on success — webhooks shouldn't carry
   response bodies the sender will act on.

Telemetry domain is ``cma.webhook``. Payloads are redacted by
:mod:`cma.telemetry.jsonl` before disk write, so a stray secret in a
forwarded ``data`` blob doesn't end up in the log.

Importing this module requires fastapi. The ``cma.webhook`` package does
NOT auto-import it — operators who only need the signature / event models
can use those without the ``[webhook]`` extra installed.
"""

import os
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response

from cma.core.budget import BudgetLedger, UsageSnapshot
from cma.core.config import BudgetConfig, ProjectConfig
from cma.telemetry.jsonl import TelemetryEmitter
from cma.webhook.actions import ActionContext, Actions, NullActions
from cma.webhook.events import (
    EVENT_TYPE_SESSION_STATUS_IDLED,
    WebhookEnvelope,
    parse_session_status_idled,
)
from cma.webhook.policy import (
    BudgetState,
    KillSwitchAction,
    kill_switch_policy,
)
from cma.webhook.signature import (
    WebhookHeaders,
    WebhookSignatureError,
    verify_signature,
)

PolicyFn = Callable[[BudgetState, BudgetConfig], KillSwitchAction]


def _read_secret(env_var: str) -> str:
    """Read the signing secret from the env var, fail if unset/empty."""
    secret = os.environ.get(env_var, "").strip()
    if not secret:
        raise RuntimeError(
            f"webhook signing secret env var {env_var!r} is unset or empty"
        )
    return secret


def _compute_state(
    *,
    project: str,
    session_id: str,
    model: str,
    usage_snapshot: UsageSnapshot,
    ledger: BudgetLedger,
    budget: BudgetConfig,
) -> BudgetState:
    """Reconcile usage with the ledger and produce a :class:`BudgetState`.

    Calls :meth:`BudgetLedger.record_usage` — that's the only place the
    ledger is mutated by the receiver. The status string passed in is
    ``"idled"`` (matches the event type).
    """
    ledger.record_usage(
        session_id=session_id,
        project=project,
        model=model,
        usage=usage_snapshot,
        status="idled",
    )
    session_cost = ledger.session_cost(session_id)
    daily_spend = ledger.daily_spend(project)
    total_tokens = (
        usage_snapshot.input_tokens
        + usage_snapshot.output_tokens
        + usage_snapshot.cache_creation_input_tokens
        + usage_snapshot.cache_read_input_tokens
    )
    return BudgetState(
        session_id=session_id,
        project=project,
        session_cost_usd=session_cost,
        session_total_tokens=total_tokens,
        daily_spend_usd=daily_spend,
        daily_cap_usd=budget.daily_usd_cap,
        per_session_token_cap=budget.per_session_token_cap,
        over_session_cap=total_tokens >= budget.per_session_token_cap,
        over_daily_cap=daily_spend >= budget.daily_usd_cap,
    )


def _dispatch(
    action: KillSwitchAction,
    ctx: ActionContext,
    actions: Actions,
) -> None:
    """Translate a policy decision into action-executor calls."""
    if action is KillSwitchAction.IGNORE:
        return
    if action is KillSwitchAction.NOTIFY_ONLY:
        actions.notify(ctx)
        return
    if action is KillSwitchAction.CANCEL_SESSION:
        actions.cancel_session(ctx)
        actions.notify(ctx)
        return
    if action is KillSwitchAction.CANCEL_PROJECT:
        actions.cancel_project_sessions(ctx)
        actions.notify(ctx)
        return
    if action is KillSwitchAction.EXHAUST_BUDGET:
        actions.cancel_session(ctx)
        actions.mark_budget_exhausted(ctx)
        actions.notify(ctx)
        return


def build_app(
    config: ProjectConfig,
    *,
    ledger: BudgetLedger,
    telemetry: TelemetryEmitter | None = None,
    actions: Actions | None = None,
    policy: PolicyFn = kill_switch_policy,
    signing_secret: str | None = None,
) -> FastAPI:
    """Build the FastAPI app for a given project.

    :param config: Loaded :class:`ProjectConfig` (must include a
        :class:`WebhookConfig` block).
    :param ledger: Budget ledger; receiver mutates this on every event.
    :param telemetry: Telemetry emitter. Defaults to a project-scoped one.
    :param actions: Action executor. Defaults to :class:`NullActions`
        (safe for the operator's current Max-only setup).
    :param policy: Decision function. Defaults to the operator-authored
        :func:`kill_switch_policy` in :mod:`cma.webhook.policy`.
    :param signing_secret: Override the secret resolution path (tests
        pass this directly; production reads from
        :attr:`WebhookConfig.signing_secret_env`).
    """
    if config.webhook is None:
        raise RuntimeError(
            "ProjectConfig.webhook is None — declare a webhook: block in "
            "project.yaml before calling build_app."
        )

    secret = signing_secret if signing_secret is not None else _read_secret(
        config.webhook.signing_secret_env
    )
    project_name = config.project.name
    telemetry = telemetry or TelemetryEmitter(project=project_name)
    actions = actions or NullActions()
    subscribed = set(config.webhook.subscribe_events)

    app = FastAPI(
        title=f"cma-webhook-{project_name}",
        # Disable docs — public-facing endpoint, no reason to advertise.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.post("/webhook", status_code=204, response_model=None)
    async def receive(request: Request) -> Response:
        body = await request.body()
        try:
            headers = WebhookHeaders.from_mapping(dict(request.headers))
            verify_signature(body=body, headers=headers, secret=secret)
        except WebhookSignatureError as exc:
            telemetry.emit(
                domain="cma.webhook",
                action="signature_rejected",
                extra={"reason": str(exc)},
            )
            # 400 (not 401) — the request is well-formed at the HTTP level
            # but its signature doesn't match. Svix retries on 5xx, not 4xx,
            # so 400 here avoids retry storms on a misconfigured secret.
            raise HTTPException(status_code=400, detail="signature_invalid") from exc

        try:
            envelope = WebhookEnvelope.model_validate_json(body)
        except Exception as exc:
            telemetry.emit(
                domain="cma.webhook",
                action="envelope_invalid",
                extra={"error": str(exc)},
            )
            raise HTTPException(status_code=400, detail="envelope_invalid") from exc

        telemetry.emit(
            domain="cma.webhook",
            action="received",
            extra={
                "type": envelope.type,
                "subscribed": envelope.type in subscribed,
            },
        )

        if envelope.type != EVENT_TYPE_SESSION_STATUS_IDLED:
            # Other event types are forensic-logged but not policy-checked.
            return Response(status_code=204)

        idled = parse_session_status_idled(envelope)
        if idled.project is None or idled.model is None or idled.usage is None:
            # The event SHOULD carry these fields; if the backend omitted
            # them we can't act, but we don't 5xx because that triggers
            # retries. Log and move on.
            telemetry.emit(
                domain="cma.webhook",
                action="session_idled_incomplete",
                extra={"session_id": idled.session_id},
            )
            return Response(status_code=204)

        usage_snapshot = UsageSnapshot(
            input_tokens=idled.usage.input_tokens,
            output_tokens=idled.usage.output_tokens,
            cache_creation_input_tokens=idled.usage.cache_creation_input_tokens,
            cache_read_input_tokens=idled.usage.cache_read_input_tokens,
            cache_creation_5m_tokens=idled.usage.cache_creation_5m_tokens,
            cache_creation_1h_tokens=idled.usage.cache_creation_1h_tokens,
        )
        state = _compute_state(
            project=idled.project,
            session_id=idled.session_id,
            model=idled.model,
            usage_snapshot=usage_snapshot,
            ledger=ledger,
            budget=config.budget,
        )

        try:
            decision = policy(state, config.budget)
        except NotImplementedError as exc:
            # Surface the operator-facing "you must implement this" error
            # as a 500 so they notice during smoke-testing.
            telemetry.emit(
                domain="cma.webhook",
                action="policy_not_implemented",
                extra={"session_id": state.session_id, "error": str(exc)},
            )
            raise HTTPException(status_code=500, detail="policy_not_implemented") from exc

        telemetry.emit(
            domain="cma.webhook",
            action="policy_decision",
            extra={
                "session_id": state.session_id,
                "project": state.project,
                "decision": decision.value,
                "session_cost_usd": state.session_cost_usd,
                "session_total_tokens": state.session_total_tokens,
                "daily_spend_usd": state.daily_spend_usd,
                "over_session_cap": state.over_session_cap,
                "over_daily_cap": state.over_daily_cap,
            },
        )

        reason = (
            f"breach: over_session_cap={state.over_session_cap}, "
            f"over_daily_cap={state.over_daily_cap}, "
            f"session_cost=${state.session_cost_usd:.4f}, "
            f"daily_spend=${state.daily_spend_usd:.4f}"
        )
        ctx = ActionContext(
            session_id=state.session_id,
            project=state.project,
            reason=reason,
        )
        _dispatch(decision, ctx, actions)
        return Response(status_code=204)

    @app.get("/healthz", status_code=200)
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "project": project_name}

    return app
