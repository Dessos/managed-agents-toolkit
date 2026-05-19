"""Tests for cma.core.config — project config loader + validator."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cma.core.config import (
    BudgetConfig,
    LocalRef,
    McpServerSpec,
    TemplateRef,
    WebhookConfig,
    _expand_env_vars,
    load_project,
)

# ---------------------------------------------------------------------------
# Env var expansion
# ---------------------------------------------------------------------------


class TestEnvVarExpansion:
    def test_expands_top_level_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_VAR", "expanded")
        assert _expand_env_vars("${MY_VAR}") == "expanded"

    def test_expands_in_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOST", "example.com")
        out = _expand_env_vars({"url": "https://${HOST}/path"})
        assert out == {"url": "https://example.com/path"}

    def test_unknown_var_left_in_place(self) -> None:
        # Intentional: empty substitution would silently mask config errors.
        # Lint flags the unset var later.
        assert _expand_env_vars("${DEFINITELY_NOT_SET_VAR_XYZ}") == "${DEFINITELY_NOT_SET_VAR_XYZ}"

    def test_recursive_on_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("A", "1")
        monkeypatch.setenv("B", "2")
        assert _expand_env_vars(["${A}", "${B}", "static"]) == ["1", "2", "static"]


# ---------------------------------------------------------------------------
# Ref discriminator
# ---------------------------------------------------------------------------


class TestRef:
    def test_template_rejects_slash(self) -> None:
        with pytest.raises(ValueError, match="path separators"):
            TemplateRef(template="agents/foo")

    def test_template_accepts_simple_name(self) -> None:
        ref = TemplateRef(template="code-reviewer")
        assert ref.template == "code-reviewer"

    def test_local_accepts_path(self) -> None:
        ref = LocalRef(local="agents/foo.yaml")
        assert ref.local == "agents/foo.yaml"


# ---------------------------------------------------------------------------
# MCP server URL validation
# ---------------------------------------------------------------------------


class TestMcpServerSpec:
    def test_https_required(self) -> None:
        with pytest.raises(ValueError, match="HTTPS"):
            McpServerSpec(name="bad", url="http://insecure.example.com")

    def test_https_accepted(self) -> None:
        spec = McpServerSpec(name="ok", url="https://mcp.example.com/path")
        assert spec.url.startswith("https://")

    def test_template_placeholder_accepted(self) -> None:
        # ${VAR} is allowed pre-expansion; lint will catch unexpanded ones.
        spec = McpServerSpec(name="ok", url="${MCP_URL}")
        assert spec.url == "${MCP_URL}"


# ---------------------------------------------------------------------------
# Webhook config
# ---------------------------------------------------------------------------


class TestWebhookConfig:
    def test_https_required(self) -> None:
        with pytest.raises(ValueError, match="HTTPS"):
            WebhookConfig(endpoint="http://insecure.example.com")

    def test_https_accepted(self) -> None:
        cfg = WebhookConfig(endpoint="https://webhooks.example.com")
        assert cfg.endpoint == "https://webhooks.example.com"

    def test_default_subscribe_events(self) -> None:
        cfg = WebhookConfig(endpoint="https://x.example.com")
        assert "session.status_idled" in cfg.subscribe_events
        assert "vault_credential.refresh_failed" in cfg.subscribe_events


# ---------------------------------------------------------------------------
# Budget config
# ---------------------------------------------------------------------------


class TestBudgetConfig:
    def test_defaults_sane(self) -> None:
        cfg = BudgetConfig()
        assert cfg.daily_usd_cap > 0
        assert cfg.per_session_token_cap > 0

    def test_negative_cap_rejected(self) -> None:
        with pytest.raises(Exception):  # pydantic ValidationError
            BudgetConfig(daily_usd_cap=-1.0)

    def test_warn_at_pct_in_range(self) -> None:
        with pytest.raises(Exception):
            BudgetConfig(warn_at_pct=200)


# ---------------------------------------------------------------------------
# Full load_project
# ---------------------------------------------------------------------------


def _write_project(tmp_path: Path, *, project_name: str = "test-proj") -> Path:
    """Write a minimal valid .managed-agents/project.yaml; return repo root."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    cma_dir = workspace / ".managed-agents"
    cma_dir.mkdir()
    project_yaml = {
        "project": {
            "name": project_name,
            "workspace_root": str(workspace),
        },
        "defaults": {"model": "claude-opus-4-7"},
        "agents": [],
        "environments": [],
        "skills": [],
        "mcp_servers": [],
        "workflows": [],
    }
    (cma_dir / "project.yaml").write_text(yaml.safe_dump(project_yaml), encoding="utf-8")
    return workspace


class TestLoadProject:
    def test_loads_minimal_valid(self, tmp_path: Path) -> None:
        workspace = _write_project(tmp_path)
        config = load_project(workspace)
        assert config.project.name == "test-proj"
        assert config.project.workspace_root == workspace

    def test_loads_from_dir_or_file(self, tmp_path: Path) -> None:
        workspace = _write_project(tmp_path)
        from_dir = load_project(workspace)
        from_file = load_project(workspace / ".managed-agents" / "project.yaml")
        assert from_dir.project.name == from_file.project.name

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_project(tmp_path)

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        workspace = tmp_path / "repo"
        workspace.mkdir()
        cma_dir = workspace / ".managed-agents"
        cma_dir.mkdir()
        (cma_dir / "project.yaml").write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="Empty"):
            load_project(workspace)

    def test_workspace_root_must_exist(self, tmp_path: Path) -> None:
        workspace = tmp_path / "repo"
        workspace.mkdir()
        cma_dir = workspace / ".managed-agents"
        cma_dir.mkdir()
        project_yaml = {
            "project": {
                "name": "test",
                "workspace_root": str(tmp_path / "does-not-exist"),
            },
        }
        (cma_dir / "project.yaml").write_text(yaml.safe_dump(project_yaml), encoding="utf-8")
        with pytest.raises(Exception, match="does not exist"):
            load_project(workspace)
