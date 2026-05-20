---
type: decision
status: active
created: 2026-05-19T00:00:00Z
updated: 2026-05-20T00:00:00Z
tags: [adr, security, credentials, patterns]
related: []
confidence: high
source: "CHANGELOG.md v0.3.0 'Decisions baked in' section"
---

# API key resolution chain — env → helper → settings.json, with sentinel detection

## Context

`cma.api.client.get_client()` needs a real Anthropic API key to instantiate the SDK client. Operators store secrets in different places per OS — Windows uses PowerShell `SecretManagement`, macOS uses Keychain, Linux uses `libsecret`. CLI tooling typically pulls from environment variables.

Two problems at v0.3.0:
1. **Plaintext-in-env** is the default for many tools and an anti-pattern for long-lived secrets — encourages copying to `.env` files which get committed by accident.
2. **Sentinel values** like `sk-ant-..`, `sk-ant-placeholder`, `sk-ant-xxxxxxxx` are common in operator workflows (defensive placeholder so the env var "exists" but doesn't contain a real key). If the toolkit naively reads them, they reach the API and fail confusingly.

The operator also runs Claude Code with `apiKeyHelper` in `~/.claude/settings.json` — a shell-command field that produces the API key on demand. Sharing that helper convention reduces operator configuration burden.

## Options considered

1. **Env var only** — read `ANTHROPIC_API_KEY` and use it. Simple. **Rejected**: plaintext-in-env anti-pattern; no graceful sentinel handling.
2. **Helper command only** — require operator to set `CMA_API_KEY_HELPER` to a shell command that outputs the key. **Rejected**: no fallback if helper isn't configured; breaks existing workflows that rely on env vars.
3. **Resolution chain** — try env var first, fall through to a helper command env var, fall through to Claude Code's `apiKeyHelper` field in `~/.claude/settings.json`. Detect and reject sentinels at each step before moving on. **Chosen.**

## Decision

**`cma.api.client._api_key()` resolves the Anthropic API key via a three-step chain:**

1. `ANTHROPIC_API_KEY` env var, if present AND not a recognized sentinel.
2. `CMA_API_KEY_HELPER` env var (a shell command), if set — execute, capture stdout, validate.
3. `apiKeyHelper` field in `~/.claude/settings.json`, if present — same semantics as #2, shared with Claude Code.

**Sentinel detection** rejects keys < 30 chars, with wrong prefix, or matching known decoys (`sk-ant-..`, `sk-ant-placeholder`, `sk-ant-xxxxxxxx`). Rejected values fall through to the next step in the chain.

## Rationale

- **The chain accommodates operator workflows in priority order**: explicit env var (intentional override) → operator-specific helper → shared Claude Code helper.
- **Sentinel handling preserves defensive workflows**: operators who keep `sk-ant-..` in their shell config don't accidentally hit the API with garbage; they get clear "no key found" errors instead.
- **`lru_cache` on the resolution** means we pay the helper-shell cost once per process lifetime — secret rotation requires a restart, which matches the "rotate-and-restart" discipline anyway.
- **Sharing the Claude Code convention reduces drift**: the operator's apiKeyHelper Just Works for cma without separate configuration.
- **No mutation of Claude Code's `settings.json`**: cma reads but doesn't write. This avoids surprising Claude Code with cma-specific keys and keeps responsibility clean.
- **Shell execution by design**: `apiKeyHelper` is a shell command (matches Claude Code's pattern). Operator-controlled sources only; shell-injection risk bounded by trust boundary.

## Trade-offs accepted

- **Secret rotation requires restart**: `lru_cache` caches the key for the process lifetime. Long-running daemons (`cma executor serve`) need a restart after rotation. Documented in `docs/api-key-storage.md`. Mitigation: rotation is rare; restarts are cheap.
- **Helper command latency**: each cold-start hits the OS keychain via the helper, which can take 100-500ms on Windows. Cached after the first call.
- **No web-of-trust on helper output**: the toolkit trusts the helper's stdout. If the helper is compromised, the key is compromised — same model as Claude Code itself.

## Revisit trigger

Revisit if:
- Anthropic ships short-lived API tokens (e.g., OAuth-style) — then the cache-for-process-lifetime needs to become cache-with-TTL.
- A new sentinel pattern appears in the wild (add to the rejected-prefix list).
- A consumer asks to bypass the sentinel check (e.g., for a test fixture that intentionally exercises the failure path).

## Related

- [`src/cma/api/client.py`](../../../src/cma/api/client.py) — `_api_key()` + `_run_helper()` + `_looks_like_real_key()`
- [`docs/api-key-storage.md`](../../api-key-storage.md) — operator setup walkthrough for SecretManagement / Keychain / libsecret
- `CHANGELOG.md:188-211` — v0.3.0 release notes
- 26 unit tests in `tests/unit/test_api_key_helper.py` covering every sentinel pattern + all three resolution sources
