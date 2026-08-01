"""
Token and cost accounting for a run.

Every model call goes through `agents.llm_client`, so that is where the `usage`
returned by the API is recorded, and here is where it is accumulated per model.
At the end of the run the pipeline reports how many calls were made, how many
tokens were spent, what it cost and how much the prompt cache saved.

The cache saving is not an estimate: every response reports how many input
tokens came from the cache (`cached_tokens`), and those are billed at a fraction
of the normal price. The saving is the difference between what they would have
cost at full price and what they cost cached.

There is a single global accumulator because one pipeline runs at a time; the
orchestrator resets it on start.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Prices in US dollars per million tokens (July 2026). A model missing from this
# table is still counted in tokens, just without cost.
#
# The Claude ones (step 5) come from Anthropic's table; the "cached" price is the
# cache-read price, ~0.1x the input. Sonnet 5 has a launch discount running until
# 2026-08-31 that this table does NOT model: for that model the reported cost
# lands above the real one.
PRICING: Dict[str, Tuple[float, float, float]] = {
    #                        input    cached    output
    # OpenAI (steps 1 to 4)
    "gpt-4.1":              (2.00,     0.50,     8.00),
    "gpt-4.1-mini":         (0.40,     0.10,     1.60),
    "gpt-4.1-nano":         (0.10,     0.025,    0.40),
    "gpt-4o":               (2.50,     1.25,    10.00),
    "gpt-4o-mini":          (0.15,     0.075,    0.60),
    "gpt-5":                (1.25,     0.125,   10.00),
    # Anthropic (step 5)
    "claude-fable-5":       (10.00,    1.00,    50.00),
    "claude-opus-5":        (5.00,     0.50,    25.00),
    "claude-opus-4-8":      (5.00,     0.50,    25.00),
    "claude-opus-4-7":      (5.00,     0.50,    25.00),
    "claude-sonnet-5":      (3.00,     0.30,    15.00),
    "claude-sonnet-4-6":    (3.00,     0.30,    15.00),
    "claude-haiku-4-5":     (1.00,     0.10,     5.00),
}


def pricing_for(model: str) -> Optional[Tuple[float, float, float]]:
    """
    Prices for the model, tolerating date suffixes (`gpt-4.1-mini-2025-04-14`).

    Returns:
        (input, cached, output) per million tokens, or None if unknown.
    """
    if model in PRICING:
        return PRICING[model]
    # Longest name that is a prefix of the model: keeps "gpt-4.1" from beating
    # "gpt-4.1-mini" when the model is "gpt-4.1-mini-2025-04-14".
    candidates = [name for name in PRICING if model.startswith(name)]
    return PRICING[max(candidates, key=len)] if candidates else None


@dataclass
class ModelUsage:
    """Accumulated consumption for one model."""

    model: str
    calls: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0

    @property
    def fresh_tokens(self) -> int:
        """Input tokens that were paid at full price."""
        return max(self.input_tokens - self.cached_tokens, 0)

    @property
    def cache_hit_rate(self) -> float:
        return self.cached_tokens / self.input_tokens if self.input_tokens else 0.0

    @property
    def cost(self) -> Optional[float]:
        """Cost in US dollars, or None if the model's price is unknown."""
        prices = pricing_for(self.model)
        if prices is None:
            return None
        p_in, p_cached, p_out = prices
        return (
            self.fresh_tokens * p_in
            + self.cached_tokens * p_cached
            + self.output_tokens * p_out
        ) / 1_000_000

    @property
    def cost_without_cache(self) -> Optional[float]:
        """What it would have cost if nothing had hit the cache."""
        prices = pricing_for(self.model)
        if prices is None:
            return None
        p_in, _, p_out = prices
        return (self.input_tokens * p_in + self.output_tokens * p_out) / 1_000_000

    @property
    def cache_savings(self) -> Optional[float]:
        without_cache, with_cache = self.cost_without_cache, self.cost
        if without_cache is None or with_cache is None:
            return None
        return without_cache - with_cache

    def as_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "cached_tokens": self.cached_tokens,
            "fresh_tokens": self.fresh_tokens,
            "output_tokens": self.output_tokens,
            "cache_hit_rate": round(self.cache_hit_rate, 4),
            "cost_usd": None if self.cost is None else round(self.cost, 6),
            "cache_savings_usd": (
                None if self.cache_savings is None else round(self.cache_savings, 6)
            ),
            "priced": pricing_for(self.model) is not None,
        }


class UsageTracker:
    """Accumulates the consumption of every call in the run, per model."""

    def __init__(self) -> None:
        self._by_model: Dict[str, ModelUsage] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._by_model.clear()

    def record(self, model: str, usage: Any) -> None:
        """
        Add up the `usage` of one OpenAI response.

        There `input_tokens` is the total and `input_tokens_details.cached_tokens`
        is the part that came out of the cache.

        Tolerates responses without `usage` (the test stubs, for instance): in
        that case it adds nothing instead of breaking the run.
        """
        if usage is None:
            return
        try:
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            details = getattr(usage, "input_tokens_details", None)
            cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
        except (TypeError, ValueError):  # pragma: no cover — unexpected response
            logger.debug(f"No se pudo leer el usage de una respuesta de {model}")
            return

        self.record_tokens(model, input_tokens, cached, output_tokens)

    def record_anthropic(self, model: str, usage: Any) -> None:
        """
        Add up the `usage` of one Anthropic response, which counts differently.

        There `input_tokens` is ONLY what did not come out of the cache, and the
        cached tokens are reported apart: `cache_read_input_tokens` (reads, ~0.1x)
        and `cache_creation_input_tokens` (writes, 1.25x). Writes are added as
        full-price input, so the reported cost lands slightly below the real one
        when the cache is written.
        """
        if usage is None:
            return
        try:
            fresh = int(getattr(usage, "input_tokens", 0) or 0)
            cached = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            written = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        except (TypeError, ValueError):  # pragma: no cover — unexpected response
            logger.debug(f"No se pudo leer el usage de una respuesta de {model}")
            return

        self.record_tokens(model, fresh + written + cached, cached, output_tokens)

    def record_tokens(
        self,
        model: str,
        input_tokens: int,
        cached_tokens: int,
        output_tokens: int,
    ) -> None:
        """
        Add up already-normalized tokens: `input_tokens` is the input TOTAL and
        `cached_tokens` the part of that total that came out of the cache.
        """
        with self._lock:
            entry = self._by_model.setdefault(model, ModelUsage(model=model))
            entry.calls += 1
            entry.input_tokens += input_tokens
            entry.cached_tokens += cached_tokens
            entry.output_tokens += output_tokens

    def by_model(self) -> List[ModelUsage]:
        """Consumption per model, most expensive first."""
        with self._lock:
            entries = list(self._by_model.values())
        return sorted(entries, key=lambda e: (e.cost or 0, e.input_tokens), reverse=True)

    def totals(self) -> Dict[str, Any]:
        """Run totals. `cost_usd` is None if any model has no known price."""
        entries = self.by_model()
        priced = all(e.cost is not None for e in entries)
        total = lambda attr: sum(getattr(e, attr) for e in entries)  # noqa: E731
        input_tokens = total("input_tokens")
        cached = total("cached_tokens")
        return {
            "models": len(entries),
            "calls": total("calls"),
            "input_tokens": input_tokens,
            "cached_tokens": cached,
            "fresh_tokens": total("fresh_tokens"),
            "output_tokens": total("output_tokens"),
            "cache_hit_rate": round(cached / input_tokens, 4) if input_tokens else 0.0,
            "cost_usd": round(sum(e.cost or 0 for e in entries), 6) if priced else None,
            "cost_without_cache_usd": (
                round(sum(e.cost_without_cache or 0 for e in entries), 6) if priced else None
            ),
            "cache_savings_usd": (
                round(sum(e.cache_savings or 0 for e in entries), 6) if priced else None
            ),
        }

    def snapshot(self) -> Dict[str, Any]:
        """Full report, ready for the manifest and for the web UI."""
        return {"by_model": [e.as_dict() for e in self.by_model()], "totals": self.totals()}


tracker = UsageTracker()
