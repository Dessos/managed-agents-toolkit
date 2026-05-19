"""Starter adapter — local job handlers.

Drop this file into your project as ``.managed-agents/adapters/local_executor.py``
and edit the TODO sections for your real job handlers (`cma project init
--with-example` copies it into place automatically).

The toolkit's worker imports this file in a fresh Python subprocess per
job. JOB_HANDLERS maps handler names (referenced by job_specs/*.yaml's
``handler:`` field) to async callables.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Handler 1: compute_stats_handler (trivial validator)
# ---------------------------------------------------------------------------


async def compute_stats_handler(*, csv_path: str, delimiter: str = ",") -> dict[str, Any]:
    """Read a CSV and return basic statistics. Used to validate the pipeline.

    No consumer-specific imports — just pandas. If this handler returns a
    valid result over MCP, the executor + subprocess + adapter loop works
    end-to-end, and you can confidently wire your real handlers next.
    """
    # Defer the pandas import so the executor itself doesn't pay the
    # ~200ms pandas-import cost just to register the handler.
    import pandas as pd

    path = Path(csv_path)
    if not path.is_absolute():
        # Workspace_root was added to sys.path by the worker, but we resolve
        # paths from there explicitly.
        path = Path.cwd() / csv_path

    if not path.is_file():
        raise FileNotFoundError(f"CSV not found at {path}")

    # Run the blocking pandas call in a thread; keeps the event loop free.
    df = await asyncio.to_thread(pd.read_csv, path, delimiter=delimiter)

    return {
        "row_count": len(df),
        "column_count": len(df.columns),
        # int() cast: pandas .sum() returns numpy.int64, which JSON can't
        # serialize directly. Always cast numpy scalars to Python ints in
        # your handlers' return dicts.
        "null_counts": {col: int(df[col].isna().sum()) for col in df.columns},
    }


# ---------------------------------------------------------------------------
# Handler 2: example_long_job_handler (the long-running compute template)
# ---------------------------------------------------------------------------

# TODO(operator): replace these stubs with your project's actual registered
# objects. Keep the registry HERE in the adapter — the spec_ref / slug is
# what the cloud agent sees; the resolved object is what runs.
#
# Rename STRATEGY_REGISTRY to fit your domain (MODEL_REGISTRY,
# PIPELINE_REGISTRY, etc.) and update the slug → class mapping. Example:
#
#   from yourproject.compute.my_module import MyImplementation
#   STRATEGY_REGISTRY = {
#       "example-strategy-v1": MyImplementation,
#       "example-confirmed": MyImplementationConfirmed,
#   }
STRATEGY_REGISTRY: dict[str, Any] = {
    "example-strategy-v1": None,    # TODO: import your implementation class
    "example-confirmed": None,      # TODO: import your confirmed-variant class
    "example-ladder": None,         # TODO: import your laddering variant class
}


async def example_long_job_handler(
    *,
    strategy_name: str,
    dataset: str,
    start: str,
    end: str,
    chunk_size_steps: int = 100,
    validation_steps: int = 25,
) -> dict[str, Any]:
    """Run a long-running chunked compute and return ONLY summary metrics.

    The handler:
    1. Resolves strategy_name to a concrete object via STRATEGY_REGISTRY
       (operator-controlled mapping; rename the dict to fit your domain).
    2. Calls your project's long-running compute function on the dataset.
    3. Extracts ONLY the summary metrics declared in the spec's
       output_summary block.
    4. Returns the dict. Per-step state and proprietary parameters stay local.

    Cloud / Claude Code NEVER sees the resolved object, full intermediate
    state, or per-step values.
    """
    impl = STRATEGY_REGISTRY.get(strategy_name)
    if impl is None:
        raise ValueError(
            f"Unknown strategy_name {strategy_name!r}. "
            f"Edit STRATEGY_REGISTRY in adapters/local_executor.py to register it."
        )

    # TODO(operator): wire your real long-running call. The signature varies
    # by project; the example below is illustrative. Convert the result to
    # JSON-safe types before returning.
    #
    # from yourproject.compute import run_long_task
    # from datetime import datetime
    #
    # result = await run_long_task(
    #     impl=impl,
    #     dataset=dataset,
    #     start=datetime.fromisoformat(start),
    #     end=datetime.fromisoformat(end),
    #     chunk_size_steps=chunk_size_steps,
    #     validation_steps=validation_steps,
    # )
    #
    # # Build the summary dict — only declared fields, JSON-safe values.
    # sampled = _downsample(result.series, 100)
    # return {
    #     "metric_a": float(result.metric_a),
    #     "metric_b": float(result.metric_b),
    #     "metric_c": float(result.metric_c),
    #     "result_count": int(result.count),
    #     "windows_completed": int(result.window_count),
    # }

    raise NotImplementedError(
        "example_long_job_handler is a template — wire your real "
        "compute call in adapters/local_executor.py before using."
    )


# ---------------------------------------------------------------------------
# Helper: downsample a series so the output_summary stays compact
# ---------------------------------------------------------------------------


def _downsample(points: list[tuple[str, float]], target_count: int) -> list[dict[str, Any]]:
    """Take every Nth point so the result list has roughly target_count items.

    Use this when your real handler returns a long series (e.g. a
    performance curve, a loss curve, a per-iteration metric) and you want
    a viewable sample over MCP without sending tens of thousands of values.
    """
    if not points or len(points) <= target_count:
        return [{"ts": ts, "value": v} for ts, v in points]
    stride = max(1, len(points) // target_count)
    return [{"ts": ts, "value": v} for ts, v in points[::stride]]


# ---------------------------------------------------------------------------
# Required: JOB_HANDLERS export
# ---------------------------------------------------------------------------

JOB_HANDLERS = {
    "compute_stats_handler": compute_stats_handler,
    "example_long_job_handler": example_long_job_handler,
}
