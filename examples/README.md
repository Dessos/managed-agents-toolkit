# Examples

The starter example moved to [`src/cma/templates/starter/`](../src/cma/templates/starter/)
so it ships with the wheel.

- **To use it in your own project**, run `cma project init --with-example`
  after installing `claude-managed-agents`. The scaffold copies the starter's
  `job_specs/` and `adapters/` into your project's `.managed-agents/`
  directory, substituting your project name and workspace root.
- **To browse the source files directly**, follow the link above.

The `.mcp.json` template that used to live alongside the starter has been
consolidated into [`src/cma/templates/.mcp.json.example`](../src/cma/templates/.mcp.json.example).
`cma project init --with-mcp-json` emits a substituted copy at your project
root automatically.
