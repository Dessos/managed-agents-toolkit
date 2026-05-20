"""Hook 4 — block ``Stop`` if architectural keywords appeared in transcript without a vault write.

Wired as ``Stop`` in ``.claude/settings.json``.

The Claude Code Stop hook fires when the model finishes responding. We use it
as a final safety net: if the session deliberated architectural choices but
nothing was written to ``docs/vault/{decisions,learnings}/``, the operator
probably wants an ADR. Block Stop with a reminder so the operator (or the
agent, if `acceptEdits` mode) can address it.

Critical: respect ``stop_hook_active`` from the hook input. Claude Code's
8-block cap will eventually override us, but a polite hook respects the
retry signal BEFORE the cap kicks in. Otherwise we burn 7 retries on what's
already a "I heard you, moving on" situation.

Spec (from plan v4.1):
1. Read stdin JSON; if ``stop_hook_active=true`` → exit 0 (don't keep blocking).
2. Scan transcript at ``data["transcript_path"]`` for architectural keywords.
3. Check for any vault write under ``docs/vault/{decisions,learnings}/`` this session.
4. If keyword density ≥ 2 distinct AND no vault write → block with JSON.
5. Bypass: ``CMA_VAULT_BYPASS`` or ``CMA_VAULT_STOP_BYPASS``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from cma.vault._telemetry import log_event

BYPASS_MASTER_ENV = "CMA_VAULT_BYPASS"
BYPASS_STOP_ENV = "CMA_VAULT_STOP_BYPASS"

#: Words that, when ≥ 2 distinct appear in transcript, suggest architectural
#: deliberation. Calibrated to catch real design conversations without too
#: many false positives (e.g., "architecture" alone in a bug report won't
#: trigger — we need a second distinct word).
KEYWORDS: frozenset[str] = frozenset(
    {
        "architecture",
        "architectural",
        "design decision",
        "invariant",
        "contract",
        "trade-off",
        "tradeoff",
        "commitment",
        "rationale",
        "deliberated",
        "alternatives",
    }
)

DEFAULT_VAULT_WRITE_PATTERNS = ("docs/vault/decisions/", "docs/vault/learnings/")


def _read_hook_input() -> dict:
    """Read Stop hook input from stdin."""
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _bypass_active() -> str | None:
    for var in (BYPASS_MASTER_ENV, BYPASS_STOP_ENV):
        if os.environ.get(var):
            return var
    return None


def scan_keywords(transcript_text: str, keywords: frozenset[str] = KEYWORDS) -> set[str]:
    """Return distinct keywords present in transcript (case-insensitive)."""
    text = transcript_text.lower()
    found: set[str] = set()
    for kw in keywords:
        # Use word boundaries for single-word keywords; substring for phrases.
        if " " in kw or "-" in kw:
            if kw in text:
                found.add(kw)
        else:
            if re.search(rf"\b{re.escape(kw)}\b", text):
                found.add(kw)
    return found


def read_transcript(path: Path) -> str:
    """Read a JSONL transcript and return concatenated text content.

    Each line is a JSON event. We extract the text fields from message
    events (the model's responses + user prompts).
    """
    if not path.is_file():
        return ""
    chunks: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Many transcript formats use {"role": ..., "content": [{...}]} or
            # {"message": {"content": "..."}}. Walk a few common shapes.
            chunks.append(_extract_text(event))
    except OSError:
        return ""
    return "\n".join(chunks)


def _extract_text(event: object) -> str:
    """Best-effort recursive text extraction from a transcript event."""
    if isinstance(event, str):
        return event
    if isinstance(event, dict):
        # Common: {"content": [...]} or {"text": "..."} or {"message": {...}}
        out: list[str] = []
        for key in ("text", "content", "message"):
            if key in event:
                out.append(_extract_text(event[key]))
        # Fall back: any string-valued fields.
        if not out:
            for v in event.values():
                if isinstance(v, str):
                    out.append(v)
        return "\n".join(s for s in out if s)
    if isinstance(event, list):
        return "\n".join(_extract_text(item) for item in event)
    return ""


def vault_files_written_in_session(
    transcript_text: str,
    patterns: tuple[str, ...] = DEFAULT_VAULT_WRITE_PATTERNS,
) -> list[str]:
    """Find paths matching vault-write patterns mentioned in the transcript.

    Heuristic: the transcript contains tool-use blocks for Write/Edit/MultiEdit
    with file paths. We don't have structured access to those here (the Stop
    hook receives just the transcript path); so we scan for path patterns.

    Patterns are matched anywhere in the transcript text — this is a
    deliberate over-match: if Claude *talked about* writing a vault file but
    didn't, we'd still pass. That's fine; the goal is preventing accidental
    "we discussed it but never wrote anything", not catching liars.
    """
    found: list[str] = []
    for pat in patterns:
        # Match the pattern + a non-whitespace tail (filename).
        for m in re.finditer(rf"{re.escape(pat)}[\w\-./]+\.md", transcript_text):
            found.append(m.group(0))
    return found


def evaluate(input_data: dict) -> tuple[int, str]:
    """Return ``(exit_code, json_block_string)``.

    Exit 0 + empty string → allow.
    Exit 0 + JSON string  → block via JSON (Stop's contract per hooks.md).

    Per the hooks reference: Stop hooks block by EXIT 0 with JSON
    ``{"decision": "block", "reason": "..."}``. NOT exit code 2 (that's for
    PreToolUse). Mixing is explicitly called out as a footgun.
    """
    transcript_path_str = input_data.get("transcript_path", "")
    if not transcript_path_str:
        return 0, ""

    transcript_path = Path(transcript_path_str)
    transcript_text = read_transcript(transcript_path)
    if not transcript_text:
        return 0, ""

    kw_found = scan_keywords(transcript_text)
    if len(kw_found) < 2:
        return 0, ""

    vault_writes = vault_files_written_in_session(transcript_text)
    if vault_writes:
        return 0, ""

    # Violation: ≥2 keywords + no vault write.
    kws_sorted = sorted(kw_found)
    reason = (
        f"This session touched architectural discussion (keywords: "
        f"{', '.join(kws_sorted)}) but no file was written under "
        f"docs/vault/decisions/ or docs/vault/learnings/. "
        f"Consider writing an ADR via docs/vault/_templates/decision.md, "
        f"or set CMA_VAULT_STOP_BYPASS=1 to skip this check (logged)."
    )
    block_json = json.dumps({"decision": "block", "reason": reason})
    return 0, block_json


def main() -> int:
    input_data = _read_hook_input()

    # CRITICAL: respect stop_hook_active to avoid the 8-block cap. If we've
    # already blocked once and the model is retrying, let them through.
    if input_data.get("stop_hook_active"):
        log_event(hook="check_session_writes", outcome="skip", reason="stop_hook_active")
        return 0

    bypass_var = _bypass_active()
    if bypass_var:
        log_event(
            hook="check_session_writes",
            outcome="bypass",
            bypass_var=bypass_var,
        )
        return 0

    exit_code, block_json = evaluate(input_data)
    if block_json:
        print(block_json)  # stdout, parsed by Claude Code
        log_event(hook="check_session_writes", outcome="block")
    else:
        log_event(hook="check_session_writes", outcome="allow")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
