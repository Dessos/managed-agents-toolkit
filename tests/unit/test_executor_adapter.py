"""Tests for cma.executor.adapter — discovery + validation of operator adapters."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from cma.executor.adapter import (
    find_adapter_path,
    find_handler,
    load_adapter_module,
    validate_handlers,
)


def _write_adapter(tmp_path: Path, body: str) -> Path:
    """Create .managed-agents/adapters/local_executor.py with given body."""
    p = tmp_path / ".managed-agents" / "adapters"
    p.mkdir(parents=True)
    adapter = p / "local_executor.py"
    adapter.write_text(dedent(body), encoding="utf-8")
    return adapter


class TestFindAdapterPath:
    def test_returns_path_when_exists(self, tmp_path: Path) -> None:
        adapter = _write_adapter(tmp_path, "JOB_HANDLERS = {}\n")
        assert find_adapter_path(tmp_path) == adapter

    def test_raises_when_missing(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match=r"local_executor\.py"):
            find_adapter_path(tmp_path)


class TestLoadAdapterModule:
    def test_loads_simple_module(self, tmp_path: Path) -> None:
        adapter = _write_adapter(
            tmp_path,
            """
            import asyncio
            async def my_handler(**kwargs):
                return {"ok": True}
            JOB_HANDLERS = {"my_handler": my_handler}
            """,
        )
        module = load_adapter_module(adapter)
        assert hasattr(module, "JOB_HANDLERS")
        assert "my_handler" in module.JOB_HANDLERS


class TestValidateHandlers:
    def _module_with(self, tmp_path: Path, body: str) -> object:
        adapter = _write_adapter(tmp_path, body)
        return load_adapter_module(adapter)

    def test_accepts_valid_async_handler(self, tmp_path: Path) -> None:
        module = self._module_with(
            tmp_path,
            """
            async def h(**kwargs):
                return {"ok": True}
            JOB_HANDLERS = {"h": h}
            """,
        )
        handlers = validate_handlers(module)
        assert "h" in handlers

    def test_rejects_missing_handlers_dict(self, tmp_path: Path) -> None:
        module = self._module_with(tmp_path, "x = 1\n")
        with pytest.raises(AttributeError, match="JOB_HANDLERS"):
            validate_handlers(module)

    def test_rejects_non_dict(self, tmp_path: Path) -> None:
        module = self._module_with(tmp_path, "JOB_HANDLERS = ['not', 'a', 'dict']\n")
        with pytest.raises(TypeError, match="must be a dict"):
            validate_handlers(module)

    def test_rejects_empty_dict(self, tmp_path: Path) -> None:
        module = self._module_with(tmp_path, "JOB_HANDLERS = {}\n")
        with pytest.raises(ValueError, match="empty"):
            validate_handlers(module)

    def test_rejects_sync_handler(self, tmp_path: Path) -> None:
        # Sync handlers don't match the async contract; we want loud failure
        # to prevent the worker from blocking the event loop.
        module = self._module_with(
            tmp_path,
            """
            def sync_handler(**kwargs):
                return {"ok": True}
            JOB_HANDLERS = {"sync_handler": sync_handler}
            """,
        )
        with pytest.raises(TypeError, match="not async"):
            validate_handlers(module)

    def test_rejects_non_callable(self, tmp_path: Path) -> None:
        module = self._module_with(
            tmp_path,
            "JOB_HANDLERS = {'broken': 'not a callable'}\n",
        )
        with pytest.raises(TypeError, match="not callable"):
            validate_handlers(module)


class TestFindHandler:
    def test_returns_named_handler(self, tmp_path: Path) -> None:
        _write_adapter(
            tmp_path,
            """
            async def alpha(**kwargs):
                return {"a": 1}
            async def beta(**kwargs):
                return {"b": 2}
            JOB_HANDLERS = {"alpha": alpha, "beta": beta}
            """,
        )
        handler = find_handler(tmp_path, "alpha")
        assert handler.__name__ == "alpha"

    def test_raises_on_unknown_handler(self, tmp_path: Path) -> None:
        _write_adapter(
            tmp_path,
            """
            async def alpha(**kwargs):
                return {}
            JOB_HANDLERS = {"alpha": alpha}
            """,
        )
        with pytest.raises(KeyError, match="not found in adapter"):
            find_handler(tmp_path, "missing")
