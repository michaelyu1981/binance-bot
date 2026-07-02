"""Shared types for deterministic CoinPilot strategies."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyDefinition:
    slug: str
    name: str
    style: str
    description: str


@dataclass(frozen=True)
class StrategyDecision:
    strategy: StrategyDefinition
    user_label: str
    symbol: str
    verdict: str
    score: int
    risk_level: str
    mode: str
    thesis: str
    triggers: tuple[str, ...]
    invalidation: tuple[str, ...]
    reasons: tuple[str, ...]
