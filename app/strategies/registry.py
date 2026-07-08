"""Strategy registry for deterministic CoinPilot algorithms."""

from __future__ import annotations

from typing import Protocol

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.breakout_scout import CoinPilotBreakoutScout
from app.strategies.claude_breakout_hunter import ClaudeBreakoutHunter
from app.strategies.claude_breakout_hunter_v2 import ClaudeBreakoutHunterV2
from app.strategies.claude_dip_accumulator import ClaudeDipAccumulator
from app.strategies.claude_mean_reversion_sniper import ClaudeMeanReversionSniper
from app.strategies.claude_trend_rider import ClaudeTrendRider
from app.strategies.gear_shifting_algo import CoinPilotGearShiftingAlgo
from app.strategies.gear_shifting_algo_v4 import CoinPilotGearShiftingAlgoV4
from app.strategies.grid_accumulation_scalper import CoinPilotGridAccumulationScalper
from app.strategies.helpers import clean_user_label
from app.strategies.micyu_bear_accumulation_period import MicyuBearAccumulationPeriod
from app.strategies.no_martingale_guard import CoinPilotNoMartingaleGuard
from app.strategies.reclaim import CoinPilotReclaim
from app.strategies.trend_guard import CoinPilotTrendGuard
from app.strategies.types import StrategyDecision, StrategyDefinition
from app.strategies.ultimate_mathematical_machine_v5 import UltimateMathematicalMachineV5


class Strategy(Protocol):
    definition: StrategyDefinition

    def evaluate(
        self,
        *,
        symbol: str,
        summary: MultiTimeframeSignalSummary,
        guides_by_interval: dict[str, TechnicalSignalGuide],
        user_label: str,
    ) -> StrategyDecision:
        ...


STRATEGY_REGISTRY: tuple[Strategy, ...] = (
    CoinPilotTrendGuard(),
    CoinPilotBreakoutScout(),
    CoinPilotReclaim(),
    CoinPilotNoMartingaleGuard(),
    CoinPilotGridAccumulationScalper(),
    CoinPilotGearShiftingAlgo(),
    CoinPilotGearShiftingAlgoV4(),
    UltimateMathematicalMachineV5(),
    ClaudeMeanReversionSniper(),
    ClaudeTrendRider(),
    ClaudeBreakoutHunter(),
    ClaudeBreakoutHunterV2(),
    ClaudeDipAccumulator(),
    MicyuBearAccumulationPeriod(),
)

STRATEGIES = tuple(strategy.definition for strategy in STRATEGY_REGISTRY)


def strategy_by_slug(slug: str) -> StrategyDefinition:
    for strategy in STRATEGIES:
        if strategy.slug == slug:
            return strategy
    return STRATEGIES[0]


def build_strategy_decisions(
    *,
    symbol: str,
    summary: MultiTimeframeSignalSummary,
    guides_by_interval: dict[str, TechnicalSignalGuide],
    user_label: str = "",
) -> tuple[StrategyDecision, ...]:
    """Evaluate every deterministic strategy for one symbol."""

    label = clean_user_label(user_label)
    return tuple(
        strategy.evaluate(
            symbol=symbol,
            summary=summary,
            guides_by_interval=guides_by_interval,
            user_label=label,
        )
        for strategy in STRATEGY_REGISTRY
    )


def build_strategy_decision(
    *,
    strategy: StrategyDefinition,
    symbol: str,
    summary: MultiTimeframeSignalSummary,
    guides_by_interval: dict[str, TechnicalSignalGuide],
    user_label: str = "",
    is_in_position: bool = False,
    active_gear: int = 0,
) -> StrategyDecision:
    """Evaluate one deterministic strategy for one symbol."""

    label = clean_user_label(user_label)
    for registered_strategy in STRATEGY_REGISTRY:
        if registered_strategy.definition.slug == strategy.slug:
            if strategy.slug == "coinpilot_gear_shifting_algo_v4":
                return registered_strategy.evaluate(
                    symbol=symbol,
                    summary=summary,
                    guides_by_interval=guides_by_interval,
                    user_label=label,
                    is_in_position=is_in_position,
                    active_gear=active_gear,
                )
            return registered_strategy.evaluate(
                symbol=symbol,
                summary=summary,
                guides_by_interval=guides_by_interval,
                user_label=label,
            )
    return STRATEGY_REGISTRY[0].evaluate(
        symbol=symbol,
        summary=summary,
        guides_by_interval=guides_by_interval,
        user_label=label,
    )
