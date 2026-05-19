# Starter cma example

A working example showing how to expose typed handlers to Managed Agents
cloud agents from any Python project. The simplest install path is
`cma project init --with-example --with-mcp-json` — this directory is
the source those flags copy from.

## Files in this starter

| File | Where it lands in your project |
|---|---|
| `job_specs/compute-stats.yaml` | `.managed-agents/job_specs/compute-stats.yaml` |
| `job_specs/example-long-job.yaml` | `.managed-agents/job_specs/example-long-job.yaml` |
| `adapters/local_executor.py` | `.managed-agents/adapters/local_executor.py` |

`cma project init --with-mcp-json` separately emits a substituted
`.mcp.json` at your project root from the bundled
`src/cma/templates/.mcp.json.example`.

## Quick start

1. **Run the scaffold**:
   ```bash
   cma project init --project-name my-project --with-example --with-mcp-json
   ```
2. **Edit `.managed-agents/adapters/local_executor.py`** — replace the
   placeholder imports with the real handlers from your project. The
   illustrative imports are commented out and marked `TODO`.
3. **Restart Claude Code** to pick up the new `.mcp.json`.
4. **Verify**: ask Claude Code in a session — "list the cma-local-executor tools".
5. **Run the trivial handler** to validate the pipeline:
   > Submit a job to spec_ref `compute-stats` with inputs
   > `{"csv_path": "data/some_file.csv"}`.

   This bypasses any consumer-specific code; it just validates that
   Claude Code → MCP → executor → subprocess → adapter → pandas → result
   all wire together.
6. **Run the real handler** once you've wired your project's logic into
   `example_long_job_handler`:
   > Run the example-long-job on dataset-alpha with chunk_size_steps=100.

## What each example demonstrates

### `compute-stats` (the trivial validator)

Reads a CSV, returns column count + row count + per-column null counts.
No consumer-specific imports. Validates the full path: Claude Code → MCP
→ executor → subprocess → adapter → pandas → result. ~30 LOC.

### `example-long-job` (the long-running compute template)

Skeleton for dispatching to a hypothetical
`yourproject.compute.run_long_task()` — pattern fits anything that
needs chunked input (training/validation windows, dataset shards,
walk-forward windows, etc.) and returns curated summary metrics. Has
TODOs where you fill in:

- Your registered-slug → resolved-object mapping (`STRATEGY_REGISTRY` is
  the example name; rename to whatever fits your domain — `MODEL_REGISTRY`,
  `PIPELINE_REGISTRY`, etc.)
- The real call to your project's long-running function
- Which summary metrics to extract for `output_summary` (never the raw
  intermediate state)

The handler is intentionally narrow: cloud / Claude Code sees only
summary metrics (`metric_a`, `metric_b`, `metric_c` in the template —
rename for your domain). Full intermediate state stays inside the
subprocess.

## Adapter contract (recap)

Every handler must:

- Be `async def` (the worker validates this).
- Accept `**inputs` matching the spec's `allowed_inputs` schema.
- Return a `dict[str, Any]` that's JSON-serializable.
- NEVER include proprietary source code, raw input data, parameter
  values, or per-step state in the return dict.
- NEVER print to stdout (the worker writes the JSON envelope there).

Failures should raise — the worker catches and reports back to the
cloud agent as a `failed` job with the exception message.
