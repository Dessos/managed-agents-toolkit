"""Tests for cma.core.pricing.

Verifies:
- Constants are non-negative (caught at import via __post_init__).
- Cost math matches the published per-Mt rates.
- Cache-read ratio is correct (0.1× base, per docs).
- Unknown model raises a clear error.
"""

from __future__ import annotations

import pytest

from cma.core.pricing import (
    HAIKU_4_5,
    OPUS_4_1,
    OPUS_4_7,
    SONNET_4_6,
    Pricing,
    for_model,
)


class TestPricingDataclass:
    def test_negative_value_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            Pricing(
                base_input=-1.0,
                cache_write_5m=0.0,
                cache_write_1h=0.0,
                cache_read=0.0,
                output=0.0,
            )

    def test_cost_uncached_only(self) -> None:
        # 1M base input on Opus 4.7 → $5
        assert OPUS_4_7.cost_usd(input_tokens=1_000_000) == pytest.approx(5.0)

    def test_cost_cache_read_ratio(self) -> None:
        # Docs: cache reads at 0.1× base. Opus base $5 → reads $0.50/Mt.
        per_mt = OPUS_4_7.cost_usd(cache_read_tokens=1_000_000)
        assert per_mt == pytest.approx(0.5)

    def test_cost_cache_write_5m_ratio(self) -> None:
        # Docs: 5m writes at 1.25× base. Opus base $5 → writes $6.25/Mt.
        per_mt = OPUS_4_7.cost_usd(cache_creation_5m_tokens=1_000_000)
        assert per_mt == pytest.approx(6.25)

    def test_cost_cache_write_1h_ratio(self) -> None:
        # Docs: 1h writes at 2.0× base. Opus base $5 → writes $10/Mt.
        per_mt = OPUS_4_7.cost_usd(cache_creation_1h_tokens=1_000_000)
        assert per_mt == pytest.approx(10.0)

    def test_cost_output_rate(self) -> None:
        # Docs: Opus output $25/Mt.
        per_mt = OPUS_4_7.cost_usd(output_tokens=1_000_000)
        assert per_mt == pytest.approx(25.0)

    def test_cost_combined(self) -> None:
        # Mix of all categories — verifies additive composition.
        cost = OPUS_4_7.cost_usd(
            input_tokens=100_000,            # 0.5
            cache_creation_5m_tokens=50_000,  # 0.3125
            cache_read_tokens=200_000,        # 0.1
            output_tokens=10_000,             # 0.25
        )
        assert cost == pytest.approx(0.5 + 0.3125 + 0.1 + 0.25)


class TestPricingTable:
    def test_opus_47_canonical(self) -> None:
        assert for_model("claude-opus-4-7") is OPUS_4_7

    def test_sonnet_46_canonical(self) -> None:
        assert for_model("claude-sonnet-4-6") is SONNET_4_6

    def test_haiku_45_canonical(self) -> None:
        assert for_model("claude-haiku-4-5") is HAIKU_4_5

    def test_opus_41_canonical(self) -> None:
        assert for_model("claude-opus-4-1") is OPUS_4_1

    def test_unknown_model_raises(self) -> None:
        with pytest.raises(KeyError, match="No pricing entry"):
            for_model("claude-mythos-99")
