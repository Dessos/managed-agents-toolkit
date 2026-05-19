"""Discover + load the operator's job handler adapter.

Per the architectural decision (hybrid YAML spec + Python adapter): each
consumer project ships ``.managed-agents/adapters/local_executor.py`` that
exports ``JOB_HANDLERS: dict[str, Callable[..., Awaitable[dict[str, Any]]]]``.

The adapter is plain operator-authored Python — runs in the same Python
process as the executor IF imported, but the actual job dispatch goes
through a subprocess (per :mod:`cma.executor.runner`). So the import here
is for VALIDATION only: confirm the adapter exists, exposes the right
shape, and can be imported. The subprocess worker re-imports it fresh.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

# Type alias for the handler shape we expect.
JobHandler = Callable[..., Awaitable[dict[str, Any]]]


# Sentinel module name used when importing the adapter file dynamically.
# Distinct so we don't collide with anything in the cma toolkit's own
# namespace.
_ADAPTER_MODULE_NAME = "_cma_consumer_adapter"


def find_adapter_path(workspace_root: Path | str) -> Path:
    """Return the canonical adapter path under a consumer project root.

    Raises :class:`FileNotFoundError` if the file is missing — every
    project that uses the executor MUST author this file.
    """
    candidate = Path(workspace_root) / ".managed-agents" / "adapters" / "local_executor.py"
    if not candidate.is_file():
        raise FileNotFoundError(
            f"Adapter not found at {candidate}. Create it with a "
            f"JOB_HANDLERS dict mapping handler names to async callables. "
            f"See docs/local-executor.md for the contract."
        )
    return candidate


def load_adapter_module(path: Path | str) -> Any:
    """Import the adapter file as a module without altering sys.path.

    We use ``importlib.util.spec_from_file_location`` so the operator's
    layout is unconstrained — no need to be on PYTHONPATH, no need for an
    ``__init__.py``, no risk of name collision with other modules.

    Note: the module IS inserted into ``sys.modules`` under
    :data:`_ADAPTER_MODULE_NAME` so that pickling closures / dataclasses
    defined in the adapter works correctly.
    """
    p = Path(path)
    spec = importlib.util.spec_from_file_location(_ADAPTER_MODULE_NAME, p)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not build import spec for adapter at {p}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_ADAPTER_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def validate_handlers(module: Any) -> dict[str, JobHandler]:
    """Verify the adapter module exports ``JOB_HANDLERS`` with the right shape.

    Returns the validated dict for direct use.

    Raises :class:`AttributeError` if ``JOB_HANDLERS`` is missing.
    Raises :class:`TypeError` if ``JOB_HANDLERS`` is not a dict, or if any
    handler is not an async callable.
    """
    handlers = getattr(module, "JOB_HANDLERS", None)
    if handlers is None:
        raise AttributeError(
            "Adapter does not export JOB_HANDLERS. Define a dict mapping "
            "handler name → async callable at module top level."
        )
    if not isinstance(handlers, dict):
        raise TypeError(
            f"JOB_HANDLERS must be a dict; got {type(handlers).__name__}."
        )
    if not handlers:
        raise ValueError("JOB_HANDLERS is empty — no handlers to dispatch to.")

    bad: list[str] = []
    for name, handler in handlers.items():
        if not isinstance(name, str):
            bad.append(f"handler key {name!r} is not a string")
            continue
        if not callable(handler):
            bad.append(f"handler {name!r} is not callable")
            continue
        if not inspect.iscoroutinefunction(handler):
            # Most consumer handlers wrap `asyncio.run(...)` themselves, but
            # the contract we WANT is `async def handler(**inputs)`. Flag
            # sync handlers loudly.
            bad.append(
                f"handler {name!r} is not async (define as `async def`)"
            )
    if bad:
        raise TypeError(
            "JOB_HANDLERS contains invalid entries:\n  - "
            + "\n  - ".join(bad)
        )
    return handlers


def find_handler(workspace_root: Path | str, handler_name: str) -> JobHandler:
    """Convenience: load + validate the adapter, return the named handler.

    Raises :class:`KeyError` if the handler name isn't in JOB_HANDLERS.
    """
    adapter_path = find_adapter_path(workspace_root)
    module = load_adapter_module(adapter_path)
    handlers = validate_handlers(module)
    if handler_name not in handlers:
        raise KeyError(
            f"Handler {handler_name!r} not found in adapter. "
            f"Available: {sorted(handlers.keys())}."
        )
    return handlers[handler_name]
