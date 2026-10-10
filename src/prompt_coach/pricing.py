"""List prices and model-name matching for cost estimates. Stdlib only:
config.py imports this, and config loads on every hook call.

Prices are USD per million tokens and are list prices, not what anyone is
billed: `[cost] billed_fraction` scales them (DECISIONS.md 2026-10-10).
Matching is a glob on a normalised model name, most specific pattern first.
An unknown model gets no price -- never another model's price.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

# Anthropic's published first-party rates, from the pricing reference cached
# 2026-10-06. They go stale; the report footer and `doctor` show this date.
PRICE_TABLE_DATE = "2026-10-06"


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float  # 5-minute cache writes (1.25x input on Claude)
    cache_write_1h: float | None = None  # None = 2x input, Anthropic's 1-hour rate

    @property
    def cache_write_1h_rate(self) -> float:
        return self.cache_write_1h if self.cache_write_1h is not None else 2 * self.input


# First match wins, so specific versions come before family catch-alls.
# Older models (Opus 4.1 and earlier, Sonnet 3.x, Haiku 3.x) are left
# unpriced on purpose: their rates differ and guessing would be wrong.
DEFAULT_PRICES: tuple[tuple[str, Price], ...] = (
    ("claude-fable-5-1*", Price(10.0, 50.0, 0.25, 12.5)),
    ("claude-mythos-5-1*", Price(10.0, 50.0, 0.25, 12.5)),
    ("claude-fable-5*", Price(10.0, 50.0, 1.0, 12.5)),
    ("claude-mythos-5*", Price(10.0, 50.0, 1.0, 12.5)),
    ("claude-opus-5-5*", Price(4.0, 20.0, 0.2, 5.0)),
    ("claude-opus-5*", Price(5.0, 25.0, 0.5, 6.25)),
    ("claude-opus-4-[5-8]*", Price(5.0, 25.0, 0.5, 6.25)),
    ("claude-sonnet-5*", Price(2.0, 10.0, 0.2, 2.5)),
    ("claude-sonnet-4*", Price(3.0, 15.0, 0.3, 3.75)),
    ("claude-haiku-5-5*", Price(0.1, 0.5, 0.01, 0.125)),
    ("claude-haiku-4-5*", Price(1.0, 5.0, 0.1, 1.25)),
)

_DATE_SUFFIX = re.compile(r"(@|-)\d{8}$")


def normalise_model(name: str) -> str:
    """One spelling per model: lowercase, no provider prefix
    ("anthropic/", "openrouter/anthropic/"), no "@20251101"/"-20251101"
    snapshot suffix, and dots as hyphens ("claude-opus-5.5" is GitHub's
    spelling of "claude-opus-5-5")."""
    name = name.strip().lower().rsplit("/", 1)[-1]
    name = _DATE_SUFFIX.sub("", name)
    return name.replace(".", "-")


def price_for(model: str | None, prices: tuple[tuple[str, Price], ...]) -> Price | None:
    if not model:
        return None
    name = normalise_model(model)
    for pattern, price in prices:
        if fnmatch.fnmatchcase(name, normalise_model(pattern)):
            return price
    return None


def list_cost_usd(
    price: Price,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    cache_write_1h_tokens: int = 0,
) -> float:
    return (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_read_tokens * price.cache_read
        + cache_write_tokens * price.cache_write
        + cache_write_1h_tokens * price.cache_write_1h_rate
    ) / 1e6
