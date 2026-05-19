"""End-to-end tests for cma.webhook.receiver.

Uses FastAPI's TestClient — no port binding, full request/response cycle
through the ASGI stack. Covers:

* Happy path: signed delivery + policy returns CANCEL_SESSION → 204 +
  expected actions fired.
* Signature failures → 400.
* Envelope failures → 400.
* Non-idled events → 204, no policy invocation.
* Idled event with missing project/model → 204, no policy invocation.
* Real (un-overridden) policy → 500 to surface "operator hasn't picked".
* Telemetry: policy_decision line emitted, payload structure asserted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest

# Skip whole module cleanly if fastapi extra isn't installed.
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from cma.core.budget import BudgetLedger
from cma.core.config import (
    BudgetConfig,
    ProjectConfig,
    ProjectMeta,
    TelemetryConfig,
    WebhookConfig,
)
from cma.telemetry.jsonl import TelemetryEmitter
from cma.webhook.actions import CapturingActions
from cma.webhook.events import EVENT_TYPE_SESSION_STATUS_IDLED
from cma.webhook.policy import BudgetState, KillSwitchAction
from cma.webhook.receiver import build_app

SECRET_RAW = b"super-secret-32-bytes-of-entropy"
SECRET = "whsec_" + base64.b64encode(SECRET_RAW).decode("ascii")


def _sign(body: bytes, msg_id: str, ts: str) -> str:
    """Produce the v1 signature header value."""
    to_sign = f"{msg_id}.{ts}.".encode() + body
    mac = hmac.new(SECRET_RAW, to_sign, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode("ascii")


@pytest.fixture
def project_config(tmp_path: Path) -> ProjectConfig:
    """Minimal ProjectConfig with workspace_root pointing at tmp_path."""
    return ProjectConfig(
        project=ProjectMeta(name="test-proj", workspace_root=tmp_path),
        budget=BudgetConfig(
            daily_usd_cap=10.0,
            per_session_token_cap=1_000_000,
            warn_at_pct=80,
            kill_on_breach=True,
        ),
        webhook=WebhookConfig(
            endpoint="https://example.test/webhook",
            signing_secret_env="CMA_TEST_WEBHOOK_SECRET",
        ),
        telemetry=TelemetryConfig(),
    )


@pytest.fixture
def ledger(tmp_path: Path) -> BudgetLedger:
    return BudgetLedger(tmp_path / "ledger.db")


@pytest.fixture
def telemetry(tmp_path: Path) -> TelemetryEmitter:
    return TelemetryEmitter(tmp_path / "telemetry.jsonl", project="test-proj")


@pytest.fixture
def actions() -> CapturingActions:
    return CapturingActions()


def _build_client(
    *,
    project: ProjectConfig,
    ledger: BudgetLedger,
    telemetry: TelemetryEmitter,
    actions: CapturingActions,
    policy=lambda state, cfg: KillSwitchAction.CANCEL_SESSION,
) -> TestClient:
    app = build_app(
        project,
        ledger=ledger,
        telemetry=telemetry,
        actions=actions,
        policy=policy,
        signing_secret=SECRET,
    )
    return TestClient(app)


def _idled_body(
    *,
    session_id: str = "sess_1",
    project: str = "test-proj",
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> bytes:
    return json.dumps(
        {
            "type": EVENT_TYPE_SESSION_STATUS_IDLED,
            "data": {
                "session_id": session_id,
                "project": project,
                "model": "claude-opus-4-7",
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
                "status": "idled",
            },
        }
    ).encode()


def _post(client: TestClient, body: bytes, *, ts: str = "1700000000",
          msg_id: str = "msg_1", sig_override: str | None = None):
    sig = sig_override if sig_override is not None else _sign(body, msg_id, ts)
    return client.post(
        "/webhook",
        content=body,
        headers={
            "webhook-id": msg_id,
            "webhook-timestamp": ts,
            "webhook-signature": sig,
            "content-type": "application/json",
        },
    )


class TestHappyPath:
    def test_signed_idled_event_returns_204(
        self,
        project_config: ProjectConfig,
        ledger: BudgetLedger,
        telemetry: TelemetryEmitter,
        actions: CapturingActions,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Use a fixed clock so the signature timestamp fits the window.
        import cma.webhook.signature as sig_mod
        monkeypatch.setattr(sig_mod.time, "time", lambda: 1700000000.0)
        client = _build_client(
            project=project_config, ledger=ledger,
            telemetry=telemetry, actions=actions,
        )
        resp = _post(client, _idled_body())
        assert resp.status_code == 204

    def test_policy_decision_dispatched(
        self,
        project_config: ProjectConfig,
        ledger: BudgetLedger,
        telemetry: TelemetryEmitter,
        actions: CapturingActions,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import cma.webhook.signature as sig_mod
        monkeypatch.setattr(sig_mod.time, "time", lambda: 1700000000.0)
        client = _build_client(
            project=project_config, ledger=ledger,
            telemetry=telemetry, actions=actions,
            policy=lambda s, c: KillSwitchAction.CANCEL_SESSION,
        )
        _post(client, _idled_body())
        assert "cancel_session" in actions.names()
        assert "notify" in actions.names()
        cancel_call = next(c for n, c in actions.calls if n == "cancel_session")
        assert cancel_call.session_id == "sess_1"
        assert cancel_call.project == "test-proj"

    def test_ignore_does_nothing(
        self,
        project_config: ProjectConfig,
        ledger: BudgetLedger,
        telemetry: TelemetryEmitter,
        actions: CapturingActions,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import cma.webhook.signature as sig_mod
        monkeypatch.setattr(sig_mod.time, "time", lambda: 1700000000.0)
        client = _build_client(
            project=project_config, ledger=ledger,
            telemetry=telemetry, actions=actions,
            policy=lambda s, c: KillSwitchAction.IGNORE,
        )
        _post(client, _idled_body())
        assert actions.names() == []

    def test_exhaust_budget_fires_all_three_actions(
        self,
        project_config: ProjectConfig,
        ledger: BudgetLedger,
        telemetry: TelemetryEmitter,
        actions: CapturingActions,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import cma.webhook.signature as sig_mod
        monkeypatch.setattr(sig_mod.time, "time", lambda: 1700000000.0)
        client = _build_client(
            project=project_config, ledger=ledger,
            telemetry=telemetry, actions=actions,
            policy=lambda s, c: KillSwitchAction.EXHAUST_BUDGET,
        )
        _post(client, _idled_body())
        assert actions.names() == [
            "cancel_session", "mark_budget_exhausted", "notify",
        ]


class TestBudgetStateComputation:
    def test_state_reflects_session_cost(
        self,
        project_config: ProjectConfig,
        ledger: BudgetLedger,
        telemetry: TelemetryEmitter,
        actions: CapturingActions,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: list[BudgetState] = []

        def capture_policy(state: BudgetState, cfg: BudgetConfig) -> KillSwitchAction:
            captured.append(state)
            return KillSwitchAction.IGNORE

        import cma.webhook.signature as sig_mod
        monkeypatch.setattr(sig_mod.time, "time", lambda: 1700000000.0)
        client = _build_client(
            project=project_config, ledger=ledger,
            telemetry=telemetry, actions=actions,
            policy=capture_policy,
        )
        _post(client, _idled_body(input_tokens=100, output_tokens=50))
        assert len(captured) == 1
        state = captured[0]
        assert state.session_id == "sess_1"
        assert state.project == "test-proj"
        assert state.session_total_tokens == 150
        assert state.session_cost_usd > 0  # opus pricing × non-zero tokens
        assert state.daily_cap_usd == 10.0

    def test_over_session_cap_flag(
        self,
        project_config: ProjectConfig,
        ledger: BudgetLedger,
        telemetry: TelemetryEmitter,
        actions: CapturingActions,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Lower the cap so this single event crosses it.
        project_config = project_config.model_copy(
            update={
                "budget": BudgetConfig(
                    daily_usd_cap=10.0,
                    per_session_token_cap=100,
                    warn_at_pct=80,
                    kill_on_breach=True,
                )
            }
        )
        captured: list[BudgetState] = []

        def capture_policy(state: BudgetState, cfg: BudgetConfig) -> KillSwitchAction:
            captured.append(state)
            return KillSwitchAction.IGNORE

        import cma.webhook.signature as sig_mod
        monkeypatch.setattr(sig_mod.time, "time", lambda: 1700000000.0)
        client = _build_client(
            project=project_config, ledger=ledger,
            telemetry=telemetry, actions=actions,
            policy=capture_policy,
        )
        _post(client, _idled_body(input_tokens=200, output_tokens=0))
        assert captured[0].over_session_cap is True


class TestSignatureFailures:
    def test_bad_signature_returns_400(
        self,
        project_config: ProjectConfig,
        ledger: BudgetLedger,
        telemetry: TelemetryEmitter,
        actions: CapturingActions,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import cma.webhook.signature as sig_mod
        monkeypatch.setattr(sig_mod.time, "time", lambda: 1700000000.0)
        client = _build_client(
            project=project_config, ledger=ledger,
            telemetry=telemetry, actions=actions,
        )
        resp = _post(client, _idled_body(), sig_override="v1,wrongsignature")
        assert resp.status_code == 400
        assert actions.names() == []

    def test_missing_headers_returns_400(
        self,
        project_config: ProjectConfig,
        ledger: BudgetLedger,
        telemetry: TelemetryEmitter,
        actions: CapturingActions,
    ) -> None:
        client = _build_client(
            project=project_config, ledger=ledger,
            telemetry=telemetry, actions=actions,
        )
        body = _idled_body()
        resp = client.post(
            "/webhook",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400


class TestEnvelopeFailures:
    def test_non_json_body_returns_400(
        self,
        project_config: ProjectConfig,
        ledger: BudgetLedger,
        telemetry: TelemetryEmitter,
        actions: CapturingActions,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import cma.webhook.signature as sig_mod
        monkeypatch.setattr(sig_mod.time, "time", lambda: 1700000000.0)
        client = _build_client(
            project=project_config, ledger=ledger,
            telemetry=telemetry, actions=actions,
        )
        body = b"not json"
        resp = _post(client, body)
        assert resp.status_code == 400


class TestNonIdledEvents:
    def test_other_event_type_returns_204_no_policy(
        self,
        project_config: ProjectConfig,
        ledger: BudgetLedger,
        telemetry: TelemetryEmitter,
        actions: CapturingActions,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = [False]

        def fail_policy(state: BudgetState, cfg: BudgetConfig) -> KillSwitchAction:
            called[0] = True
            return KillSwitchAction.IGNORE

        import cma.webhook.signature as sig_mod
        monkeypatch.setattr(sig_mod.time, "time", lambda: 1700000000.0)
        client = _build_client(
            project=project_config, ledger=ledger,
            telemetry=telemetry, actions=actions,
            policy=fail_policy,
        )
        body = json.dumps(
            {"type": "session.outcome_evaluation_ended", "data": {}}
        ).encode()
        resp = _post(client, body)
        assert resp.status_code == 204
        assert called[0] is False

    def test_idled_missing_fields_skips_policy(
        self,
        project_config: ProjectConfig,
        ledger: BudgetLedger,
        telemetry: TelemetryEmitter,
        actions: CapturingActions,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = [False]

        def fail_policy(state: BudgetState, cfg: BudgetConfig) -> KillSwitchAction:
            called[0] = True
            return KillSwitchAction.IGNORE

        import cma.webhook.signature as sig_mod
        monkeypatch.setattr(sig_mod.time, "time", lambda: 1700000000.0)
        client = _build_client(
            project=project_config, ledger=ledger,
            telemetry=telemetry, actions=actions,
            policy=fail_policy,
        )
        # Missing project/model/usage.
        body = json.dumps(
            {
                "type": EVENT_TYPE_SESSION_STATUS_IDLED,
                "data": {"session_id": "sess_partial"},
            }
        ).encode()
        resp = _post(client, body)
        assert resp.status_code == 204
        assert called[0] is False


class TestPolicyStubSafety:
    def test_notimplementederror_policy_returns_500(
        self,
        project_config: ProjectConfig,
        ledger: BudgetLedger,
        telemetry: TelemetryEmitter,
        actions: CapturingActions,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Defensive: if a future refactor re-introduces an unimplemented
        # stub for the policy, the receiver must surface it as 500 (not
        # silently no-op). Simulate by passing a policy that raises.
        import cma.webhook.signature as sig_mod
        monkeypatch.setattr(sig_mod.time, "time", lambda: 1700000000.0)

        def raises(state: BudgetState, cfg: BudgetConfig) -> KillSwitchAction:
            raise NotImplementedError("simulated stub")

        client = _build_client(
            project=project_config, ledger=ledger,
            telemetry=telemetry, actions=actions,
            policy=raises,
        )
        resp = _post(client, _idled_body())
        assert resp.status_code == 500
        assert actions.names() == []


class TestHealthz:
    def test_healthz_returns_200(
        self,
        project_config: ProjectConfig,
        ledger: BudgetLedger,
        telemetry: TelemetryEmitter,
        actions: CapturingActions,
    ) -> None:
        client = _build_client(
            project=project_config, ledger=ledger,
            telemetry=telemetry, actions=actions,
        )
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["project"] == "test-proj"


class TestTelemetry:
    def test_policy_decision_logged(
        self,
        project_config: ProjectConfig,
        ledger: BudgetLedger,
        tmp_path: Path,
        actions: CapturingActions,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import cma.webhook.signature as sig_mod
        monkeypatch.setattr(sig_mod.time, "time", lambda: 1700000000.0)
        telemetry_path = tmp_path / "telemetry.jsonl"
        telemetry = TelemetryEmitter(telemetry_path, project="test-proj")
        client = _build_client(
            project=project_config, ledger=ledger,
            telemetry=telemetry, actions=actions,
            policy=lambda s, c: KillSwitchAction.NOTIFY_ONLY,
        )
        _post(client, _idled_body())
        lines = telemetry_path.read_text(encoding="utf-8").splitlines()
        decisions = [
            json.loads(line)
            for line in lines
            if json.loads(line).get("action") == "policy_decision"
        ]
        assert len(decisions) == 1
        d = decisions[0]
        assert d["decision"] == "notify_only"
        assert d["session_id"] == "sess_1"
