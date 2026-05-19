"""Per-model pricing constants, in USD per million tokens.

Sourced from https://platform.claude.com/docs/en/build-with-claude/prompt-caching
(read 2026-05-18). Update this module whenever Anthropic publishes new prices.

The page lists five token categories per model:

* ``base_input`` — uncached input tokens
* ``cache_write_5m`` — input tokens written to a 5-minute-TTL cache
* ``cache_write_1h`` — input tokens written to a 1-hour-TTL cache
* ``cache_read`` — input tokens read from cache (any TTL)
* ``output`` — output tokens

Ratios at publish time (versus base input):

* 5-minute cache writes: 1.25×
* 1-hour cache writes: 2.0×
* Cache reads: 0.1×

The :class:`Pricing` dataclass stores absolute USD/Mt values, not ratios —
prices may change asymmetrically across categories.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Pricing:
    """USD per million tokens, per category.

    All values must be non-negative. The constructor validates this so that
    a typo in the constants table is caught at import time, not at billing
    time.
    """

    base_input: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float
    output: float

    def __post_init__(self) -> None:
        for name in ("base_input", "cache_write_5m", "cache_write_1h", "cache_read", "output"):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"Pricing.{name} must be non-negative; got {value}")

    def cost_usd(
        self,
        *,
        input_tokens: int = 0,
        cache_creation_5m_tokens: int = 0,
        cache_creation_1h_tokens: int = 0,
        cache_read_tokens: int = 0,
        output_tokens: int = 0,
    ) -> float:
        """Compute total USD cost from per-category token counts.

        Mirrors the ``usage`` field returned by the API. Caller is expected
        to split ``cache_creation_input_tokens`` into 5m vs 1h based on the
        ``cache_control.ttl`` applied at the breakpoint; the API itself does
        not surface the split, so callers may pass everything as 5m if they
        don't use 1h caching.
        """
        return (
            input_tokens * self.base_input
            + cache_creation_5m_tokens * self.cache_write_5m
            + cache_creation_1h_tokens * self.cache_write_1h
            + cache_read_tokens * self.cache_read
            + output_tokens * self.output
        ) / 1_000_000.0


# ---------------------------------------------------------------------------
# Constants — keep aligned with Anthropic's published pricing
# ---------------------------------------------------------------------------

# Prices reflect docs read on 2026-05-18. Source URL:
#   https://platform.claude.com/docs/en/build-with-claude/prompt-caching
#
# If you update these, also update CHANGELOG.md noting the date + URL fetched.
OPUS_4_7 = Pricing(
    base_input=5.0,
    cache_write_5m=6.25,
    cache_write_1h=10.0,
    cache_read=0.5,
    output=25.0,
)

OPUS_4_1 = Pricing(
    base_input=15.0,
    cache_write_5m=18.75,
    cache_write_1h=30.0,
    cache_read=1.5,
    output=75.0,
)

SONNET_4_6 = Pricing(
    base_input=3.0,
    cache_write_5m=3.75,
    cache_write_1h=6.0,
    cache_read=0.3,
    output=15.0,
)

HAIKU_4_5 = Pricing(
    base_input=1.0,
    cache_write_5m=1.25,
    cache_write_1h=2.0,
    cache_read=0.1,
    output=5.0,
)


# Model ID → pricing. The keys match Anthropic's canonical model identifiers.
# Aliases (e.g. ``claude-opus-4-7`` for the latest Opus) live alongside the
# explicit identifiers so that whichever form the caller uses works.
PRICING_TABLE: Mapping[str, Pricing] = {
    "claude-opus-4-7": OPUS_4_7,
    "claude-opus-4-6": OPUS_4_7,  # 4.6 priced same as 4.7 per docs
    "claude-opus-4-5": OPUS_4_7,
    "claude-opus-4-1": OPUS_4_1,
    "claude-sonnet-4-6": SONNET_4_6,
    "claude-sonnet-4-5": SONNET_4_6,
    "claude-haiku-4-5": HAIKU_4_5,
}


def for_model(model_id: str) -> Pricing:
    """Return the :class:`Pricing` for *model_id*.

    Raises :class:`KeyError` with a helpful message if the model isn't in the
    table — better to fail fast than to silently bill at a wrong rate.
    """
    if model_id not in PRICING_TABLE:
        raise KeyError(
            f"No pricing entry for model {model_id!r}. Add it to "
            f"cma/core/pricing.py and re-run after consulting the docs."
        )
    return PRICING_TABLE[model_id]
