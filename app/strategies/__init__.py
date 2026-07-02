"""Deterministic strategy package for CoinPilot.

All strategies are advisory or dry-run only. They must not call AI services,
access Binance order endpoints, or place real orders.
"""

from app.strategies.registry import (
    STRATEGIES,
    build_strategy_decision,
    build_strategy_decisions,
    strategy_by_slug,
)
from app.strategies.types import StrategyDecision, StrategyDefinition

__all__ = (
    "STRATEGIES",
    "StrategyDecision",
    "StrategyDefinition",
    "build_strategy_decision",
    "build_strategy_decisions",
    "strategy_by_slug",
)
