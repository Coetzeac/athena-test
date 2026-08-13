from __future__ import annotations

import math
from collections.abc import Iterable

from athena.models import ResearchMetrics


def calculate_metrics(outcomes_r: Iterable[float]) -> ResearchMetrics:
    outcomes = [float(value) for value in outcomes_r]
    if not outcomes:
        raise ValueError("at least one trade outcome is required")
    if any(not math.isfinite(value) for value in outcomes):
        raise ValueError("trade outcomes must be finite numbers")

    wins = [value for value in outcomes if value > 0]
    losses = [value for value in outcomes if value < 0]
    breakeven = len(outcomes) - len(wins) - len(losses)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = None if gross_loss == 0 else gross_profit / gross_loss

    equity = 0.0
    peak = 0.0
    maximum_drawdown = 0.0
    for value in outcomes:
        equity += value
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, peak - equity)

    return ResearchMetrics(
        sample_size=len(outcomes),
        wins=len(wins),
        losses=len(losses),
        breakeven=breakeven,
        hit_rate=len(wins) / len(outcomes),
        average_win_r=(sum(wins) / len(wins)) if wins else 0.0,
        average_loss_r=(sum(losses) / len(losses)) if losses else 0.0,
        expectancy_r=sum(outcomes) / len(outcomes),
        profit_factor=profit_factor,
        maximum_drawdown_r=maximum_drawdown,
    )


def wilson_lower_bound(wins: int, sample_size: int, z: float = 1.96) -> float:
    """Return the lower end of a two-sided 95% Wilson score interval."""
    if sample_size <= 0:
        return 0.0
    proportion = wins / sample_size
    denominator = 1 + (z * z / sample_size)
    centre = proportion + (z * z / (2 * sample_size))
    margin = z * math.sqrt(
        (proportion * (1 - proportion) / sample_size)
        + (z * z / (4 * sample_size * sample_size))
    )
    return max(0.0, (centre - margin) / denominator)

