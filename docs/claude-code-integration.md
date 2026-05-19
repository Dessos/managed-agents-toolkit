# Using cma-local-executor with Claude Code

> Status: works **today** on Max-only subscriptions. No API credit required.
> Claude Code is the MCP client; the executor is the MCP server; they
> communicate over stdio.

## Why this is useful

You give Claude Code the ability to **submit jobs** that run on your machine
in isolated subprocesses, with parameter validation, audit logging, and
strict input/output contracts. Instead of:

- "Claude, please write code that runs my long compute X" (Claude generates
  code, you run it, copy-paste output back)

You get:

- "Claude, run job X with inputs Y" (Claude calls `submit_job`, gets the
  curated summary metrics back, can iterate on the spec_ref + inputs)

The cloud agent / Managed Agents path uses the same MCP tools. So whatever
you build here works identically when you eventually add Managed Agents.

## One-time setup

### 1. Install cma globally OR ensure it's on PATH

```bash
# From the cma repo
pip install -e .
# Verify
cma --version
```

If the `cma` script isn't on your PATH (Windows often warns at install
time), add the install location to PATH or use the full path in step 3.

### 2. Author job_specs + adapter in your consumer project

```
your-project/
└── .managed-agents/
    ├── adapters/
    │   └── local_executor.py        # exports JOB_HANDLERS dict
    ├── job_specs/
    │   └── *.yaml                    # one per submittable job
    └── local_mcp_bridge.yaml         # not used for stdio; only HTTP/Cloudflare path
```

See `src/cma/templates/starter/` in the cma repo for a realistic example
adapter you can crib from. Even faster: run `cma project init --with-example`
to materialize a substituted copy directly into your project's
`.managed-agents/` directory.

### 3. Wire .mcp.json in your project root

Copy `templates/.mcp.json.example` to your project root as `.mcp.json` and
edit:

```json
{
  "mcpServers": {
    "cma-local-executor": {
      "command": "cma",
      "args": [
        "executor", "stdio",
        "--workspace-root", "<ABSOLUTE_PATH_TO_YOUR_PROJECT>",
        "--project-name", "<YOUR_PROJECT_SLUG>"
      ]
    }
  }
}
```

Use ABSOLUTE paths for `--workspace-root` — Claude Code spawns this from
its own working directory, so relative paths break. On Windows, prefer
forward slashes (e.g. `C:/Users/you/code/myproject`) to avoid JSON
escape headaches.

### 4. Restart Claude Code

Claude Code picks up `.mcp.json` at session start. After restart:

- The executor subprocess is spawned per session.
- Tools become available: `submit_job`, `get_job_status`, `get_job_result`,
  `list_jobs`, `cancel_job`.
- They show up in Claude Code's MCP server list.

## Usage

In any Claude Code conversation:

> "Run the example-long-job on dataset-alpha with chunk_size_steps=100."

Claude Code looks at the available `cma-local-executor` tools, infers the
right `spec_ref` from your job_specs/ directory, calls `submit_job` with
appropriate inputs, then polls `get_job_status` until terminal, then
fetches `get_job_result`. You see the metrics; the proprietary source
never leaves your machine.

## How the IP boundary works

| Layer | Sees |
|---|---|
| Claude Code (the MCP client) | Tool names + descriptions + the input/output JSON of each call. NOT the adapter Python code. NOT the consumer's proprietary source. NOT raw data. |
| `cma executor stdio` (the MCP server in your subprocess) | JSON-RPC requests from Claude Code. Validates inputs against your job_spec schema. Dispatches to the adapter. |
| `python -m cma.executor.worker` (per-job subprocess) | The adapter Python, the handler function, full local data, the consumer project's proprietary source. |

Claude Code only learns what your job_spec declares it can learn — the
`spec_ref`, the `output_summary` keys, and whatever the handler returns.
If you don't put proprietary parameters in the returned dict, Claude Code
never sees them.

## Verifying it works

After restarting Claude Code with the new `.mcp.json`:

```text
You: Can you list the tools available from cma-local-executor?

Claude: I have access to:
  - submit_job(spec_ref, inputs)
  - get_job_status(job_id)
  - get_job_result(job_id)
  - list_jobs(status, job_type, limit)
  - cancel_job(job_id)
```

Then run a job manually:

```text
You: Submit a job to spec_ref 'hello-world' with empty inputs.
Claude: [calls submit_job, then poll, then result] Done. Result: {ok: true}.
```

If that works, your adapter + spec + Claude Code + executor pipeline is
operational.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `cma-local-executor` doesn't appear in Claude Code | `.mcp.json` not picked up | Restart Claude Code after editing `.mcp.json`. Check Claude Code's MCP server settings panel for parse errors. |
| Server starts but tools/list is empty | FastMCP didn't register tools | Look at stderr (Claude Code shows MCP server logs). Usually means an exception during `ExecutorServer.make()`. |
| `Failed to spawn cma` | `cma` not on PATH from Claude Code's launch env | Use absolute path in args, e.g. `"command": "C:/Python314/Scripts/cma.exe"` |
| `Setup error: Adapter not found at ...` | `.managed-agents/adapters/local_executor.py` missing | Author it from the starter (`cma project init --with-example`) or write your own. Empty `JOB_HANDLERS = {}` is rejected — must have at least one handler. |
| `JOB_HANDLERS contains invalid entries: ... not async` | Operator wrote `def handler(...)` instead of `async def` | All handlers must be `async def`. Wrap blocking work with `asyncio.to_thread()`. |
| Job stays QUEUED forever | Background task GC'd before completion | Should not happen (fixed in 0.2.0 via `_background_tasks` set). File an issue if you see this. |
| `get_job_result` returns `{error: "Job not found"}` | Different Claude Code session, different executor subprocess, fresh in-memory state | They share the SQLite DB though — the issue is likely a stale job_id. Use `list_jobs` to find current IDs. |

## Limitations

- **One adapter per consumer project.** All handlers live in
  `local_executor.py`. If you need separation, dispatch internally on
  `job_type`.
- **No streaming output.** Tools return final results only — there's no
  intermediate progress stream over MCP yet. (Polling `get_job_status` is
  the proxy.)
- **MCP notifications are best-effort.** The server emits
  `notifications/cma/job_status_changed` on terminal transitions via the
  injected `FastMCPSessionNotifier` (per-job session binding — the notification
  reaches only the session that submitted the job). Whether the client acts
  on it is client-dependent; `get_job_status` polling remains the
  authoritative contract.
- **Concurrency unbounded.** Each `submit_job` call spawns a subprocess
  with no global limit. If you submit 20 jobs, you get 20 Python
  processes. (Per-job-type concurrency limits are planned.)
- **Per-session isolation.** Different Claude Code sessions spawn separate
  executor subprocesses, but they share the same SQLite state DB.
  `list_jobs` shows all jobs from all sessions.
