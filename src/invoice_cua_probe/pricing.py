"""Rough USD estimates for Gemini Computer Use token usage.

Rates match the promotional paid-tier prices invoice-agent recorded from the
Gemini Developer API pricing page on 2026-11-02 (USD per 1M tokens). Computer
Use is billed as ordinary tokens for the chosen model. Prices move. This is
an estimate for the report, not an invoice.
"""

from __future__ import annotations

from dataclasses import dataclass

PRICING_READ_ON = "2026-11-02"


@dataclass(frozen=True)
class Price:
    input_per_m: float
    output_per_m: float
    cached_per_m: float | None
    note: str = ""


PRICES: dict[str, Price] = {
    "gemini-3.8-flash": Price(0.75, 3.75, 0.075, "promotional through 2026-12-31; listed as $1.50 / $7.50 after"),
    "gemini-3.5-flash-lite": Price(0.30, 2.50, 0.03, ""),
    "gemini-4-argon": Price(2.00, 10.00, 0.10, "promotional through 2026-11-30"),
    "gemini-3-flash-preview": Price(0.50, 3.00, 0.05, ""),
}


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    thought_tokens: int = 0,
    cached_tokens: int = 0,
) -> float | None:
    """Cached tokens are treated as a subset of input tokens. Thinking tokens use the output rate."""

    price = PRICES.get(model)
    if price is None:
        return None
    cached = min(max(cached_tokens, 0), max(input_tokens, 0)) if price.cached_per_m is not None else 0
    fresh = max(input_tokens, 0) - cached
    cost = fresh * price.input_per_m + (max(output_tokens, 0) + max(thought_tokens, 0)) * price.output_per_m
    if cached:
        cost += cached * (price.cached_per_m or 0)
    return cost / 1_000_000
