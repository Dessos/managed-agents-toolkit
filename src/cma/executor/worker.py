"""Child worker process — runs ONE job and exits.

Invoked by :mod:`cma.executor.runner` as a fresh Python subprocess:

.. code-block:: bash

    python -m cma.executor.worker

The parent passes a JSON request via stdin:

.. code-block:: json

    {
        "adapter_path": "/abs/path/.managed-agents/adapters/local_executor.py",
        "handler_name": "example_long_job_handler",
        "inputs": {"dataset": "dataset-alpha", "chunk_size_steps": 100, ...}
    }

The worker:

1. Reads + parses stdin.
2. Imports the adapter module (with sys.path including the project's
   workspace_root for transitive imports).
3. Locates the named handler in JOB_HANDLERS.
4. Runs ``asyncio.run(handler(**inputs))``.
5. Writes ``{"status": "ok", "result": {...}}`` to stdout on success.
6. Writes ``{"status": "error", "error": "<msg>"}`` to stdout on failure.
7. Exits 0 (success) or 1 (failure).

Stderr is captured by the parent for diagnostics but is NOT used as
control signal — the JSON envelope on stdout is the contract.
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path
from typing import Any


def _emit_result(envelope: dict[str, Any]) -> None:
    """Write a single-line JSON envelope to stdout + flush."""
    sys.stdout.write(json.dumps(envelope) + "\n")
    sys.stdout.flush()


def _emit_error(error: str) -> None:
    _emit_result({"status": "error", "error": error})


def _import_adapter_for_worker(adapter_path: Path) -> Any:
    """Worker-side import of the operator's adapter.

    We import :mod:`cma.executor.adapter` lazily so this worker module can
    be loaded even before cma is installed — defensive but irrelevant in
    practice since the parent invoked us via cma.

    Adds the workspace_root (two levels above the adapter file) to
    ``sys.path`` so the adapter can import from the consumer project's
    source tree (e.g. ``from yourproject.compute import run_long_task``).
    """
    workspace_root = adapter_path.parent.parent.parent
    if str(workspace_root) not in sys.path:
        sys.path.insert(0, str(workspace_root))

    from cma.executor.adapter import load_adapter_module, validate_handlers

    module = load_adapter_module(adapter_path)
    validate_handlers(module)
    return module


async def _run_one(adapter_path: Path, handler_name: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Execute the named handler. Caller catches exceptions."""
    module = _import_adapter_for_worker(adapter_path)
    handlers = module.JOB_HANDLERS
    handler = handlers[handler_name]
    result = await handler(**inputs)
    if not isinstance(result, dict):
        raise TypeError(
            f"Handler {handler_name!r} returned {type(result).__name__}; "
            f"contract requires a dict (JSON-serializable)."
        )
    # Verify JSON-serializable. Catches numpy arrays etc. that snuck in.
    try:
        json.dumps(result)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            f"Handler {handler_name!r} result is not JSON-serializable: {exc}"
        ) from exc
    return result


def main() -> int:
    """Worker entry point. Exit 0 on success, 1 on failure."""
    try:
        raw_input = sys.stdin.read()
        request = json.loads(raw_input)
    except json.JSONDecodeError as exc:
        _emit_error(f"Could not parse stdin as JSON: {exc}")
        return 1

    try:
        adapter_path = Path(request["adapter_path"])
        handler_name = request["handler_name"]
        inputs = request["inputs"]
    except KeyError as exc:
        _emit_error(f"Missing required request key: {exc}")
        return 1

    try:
        result = asyncio.run(_run_one(adapter_path, handler_name, inputs))
    except Exception as exc:
        # Capture the full traceback to stderr for parent diagnostics, but
        # send a clean error message to stdout for the protocol contract.
        traceback.print_exc(file=sys.stderr)
        _emit_error(f"{type(exc).__name__}: {exc!s}")
        return 1

    _emit_result({"status": "ok", "result": result})
    return 0


if __name__ == "__main__":  # pragma: no cover - subprocess entrypoint
    sys.exit(main())
