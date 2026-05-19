"""JSONL telemetry emitter with credential redaction.

Every API call, every received event, and every workflow stage transition
appends one JSON object to the configured telemetry path. The line shape
mirrors the operator's existing hook telemetry so downstream JSONL tools
(grep, jq, custom dashboards) work interchangeably.

Redaction is non-optional: keys matching credential patterns are masked
before the line is written. This is the only defense against an agent
echoing a secret into a log we then forget to scrub.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Redaction patterns
# ---------------------------------------------------------------------------

# Keys whose values get masked regardless of content. Case-insensitive
# substring match — covers ``api_key``, ``apiKey``, ``API_KEY``, etc.
_REDACT_KEY_SUBSTRINGS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "api_key",
    "apikey",
    "auth",
    "credential",
    "private_key",
)

# Value prefixes that always indicate a credential, even under a benign key
# name. Match by case-sensitive prefix on string values.
_REDACT_VALUE_PREFIXES: tuple[str, ...] = (
    "whsec_",  # webhook signing secret
    "xoxp-",   # Slack user token
    "xoxe-",   # Slack refresh token
    "xoxb-",   # Slack bot token
    "Bearer ",  # bearer header
    "sk-",     # generic SDK key prefix (OpenAI / Anthropic shorter)
    "sk_",
    "lin_api_",  # Linear API key
    "ghp_",    # GitHub personal access token
    "github_pat_",
)

# Anthropic API key shape. Matches at the START of a value so that something
# like "Use API key ANTHROPIC_API_KEY in the call" stays readable while a
# raw key gets redacted.
_ANTHROPIC_KEY_RE = re.compile(r"^sk-ant-[a-zA-Z0-9_-]{20,}")


def _looks_like_secret_key(key: str) -> bool:
    """Return True if a dict key suggests its value is a secret."""
    lowered = key.lower()
    return any(needle in lowered for needle in _REDACT_KEY_SUBSTRINGS)


def _looks_like_secret_value(value: Any) -> bool:
    """Return True if a string value matches a credential prefix."""
    if not isinstance(value, str):
        return False
    if _ANTHROPIC_KEY_RE.match(value):
        return True
    return any(value.startswith(p) for p in _REDACT_VALUE_PREFIXES)


def redact(obj: Any) -> Any:
    """Recursively replace credential-looking values with ``"<redacted>"``.

    Operates on the *structure* — mappings, sequences, scalars. Returns a
    new object; doesn't mutate the input. Non-JSON-serialisable types are
    coerced to ``repr()`` strings (which themselves go through the value
    redactor).
    """
    if isinstance(obj, Mapping):
        return {
            k: ("<redacted>" if _looks_like_secret_key(str(k)) else redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact(item) for item in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return "<redacted>" if _looks_like_secret_value(obj) else obj
    # Fallback: stringify, then re-check (the repr might itself reveal a key).
    coerced = repr(obj)
    return "<redacted>" if _looks_like_secret_value(coerced) else coerced


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def default_telemetry_path(project_name: str = "default") -> Path:
    """Return the platform-appropriate default telemetry file.

    Order of precedence:

    1. ``$CMA_TELEMETRY_PATH`` if set
    2. Operator's ``O:/Temp/cma-<project>.jsonl`` if ``O:/Temp`` exists
       (matches the existing hf-hook pattern on the operator's Windows box)
    3. ``${TMPDIR}/cma-<project>.jsonl`` everywhere else
    """
    override = os.environ.get("CMA_TELEMETRY_PATH")
    if override:
        return Path(override)
    operator_temp = Path("O:/Temp")
    if operator_temp.is_dir():
        return operator_temp / f"cma-{project_name}.jsonl"
    return Path(tempfile.gettempdir()) / f"cma-{project_name}.jsonl"


# ---------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------


class TelemetryEmitter:
    """Append-only JSONL writer with redaction baked in.

    Stateless aside from the path; safe to instantiate per-call. Files are
    opened in append mode with line buffering so that a process crash mid-
    operation still leaves a complete previous line on disk.

    The :meth:`emit` method is the only public API.
    """

    def __init__(self, path: Path | str | None = None, *, project: str = "default"):
        self.path = Path(path) if path else default_telemetry_path(project)
        self.project = project
        # Ensure parent dir exists. Tolerate races (mkdir(parents, exist_ok)).
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(
        self,
        *,
        domain: str,
        action: str,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Append one structured line.

        :param domain: First half of the event type, e.g. ``"cma.api"``.
        :param action: Second half, e.g. ``"agent_create"``.
        :param extra: Additional fields. Redacted before write.
        """
        record: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
            "project": self.project,
            "domain": domain,
            "action": action,
        }
        if extra:
            record.update(redact(dict(extra)))
        # ``open()`` per emit is fine — Python's stdio is faster than the
        # serialization, and we want crash-resilience over throughput.
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")


# Module-level convenience emit — lazy to avoid filesystem side effects at
# import time. Construction (which mkdir's the parent) only happens on the
# first emit() call. Real code should still construct a TelemetryEmitter
# explicitly with project=... to get per-project file separation.
_default_emitter: TelemetryEmitter | None = None


def _get_default_emitter() -> TelemetryEmitter:
    """Return (and lazily construct) the module-level emitter."""
    global _default_emitter
    if _default_emitter is None:
        _default_emitter = TelemetryEmitter()
    return _default_emitter


def emit(
    *,
    domain: str,
    action: str,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Module-level shortcut around :meth:`TelemetryEmitter.emit`.

    Lazily constructs the default emitter on first call. No filesystem
    activity until the first emit.
    """
    _get_default_emitter().emit(domain=domain, action=action, extra=extra)
