# CoinPilot Trade Rules

These rules are advisory only. CoinPilot currently remains a read-only public
market monitor and technical-analysis dashboard.

No live trading is enabled. No Binance API key is required for these rules. No
account access, buy/sell orders, order endpoints, futures, margin, leverage, or
withdrawals are allowed.

Live Spot trading remains forbidden unless Michael explicitly says:

```text
enable live spot trading.
```

## Indicator Set

CoinPilot tracks these local technical indicators from stored public candle
data:

- RSI 14: momentum and overbought/oversold pressure.
- Bollinger Bands 20/2: volatility envelope and price extension.
- EMA 20: current direction.
- SMA 50: bigger direction.
- MACD 12/26/9: momentum direction and trend confirmation.
- Volume: participation and breakout confirmation.
- ATR 14: risk control and expected price range.

These indicators are not automatic signals. They are inputs for human review.

Simple interpretation:

- EMA 20 = current direction.
- SMA 50 = bigger direction.
- MACD = momentum.
- RSI = too hot or too weak.
- Bollinger Bands = volatility and breakout context.
- Volume = confirmation.
- ATR = risk control.

## Long Watchlist Rule

Consider a Spot-only long setup only when most of these conditions agree:

- Price is above EMA 20 and SMA 50 on the selected trading interval.
- MACD line is above the signal line, or improving from negative momentum.
- RSI is not extremely overbought. Prefer neutral strength over emotional
  extension.
- Price is not chasing far above the upper Bollinger Band unless a separate
  breakout plan is written.
- Volume is near or above its recent average on breakout attempts.
- ATR-based risk is small enough for the planned stop size and position size.
- Higher timeframe context does not conflict with the trade idea.

If the indicators conflict, the default action is wait.

## Avoid Rule

Avoid new long entries when:

- RSI is above 70 and price is extended above the upper Bollinger Band.
- Price is below both EMA 20 and SMA 50.
- MACD is bearish and still weakening.
- Volume does not confirm the move.
- ATR is too wide for the planned risk.
- The candle is unusually wide and likely driven by news or liquidation.
- The setup depends on hope, fear of missing out, or averaging down.

## Risk Rules

Before any future live Spot trade exists, define these limits in writing:

- Maximum position size per coin.
- Maximum total account exposure.
- Maximum daily loss.
- Stop-loss or invalidation level.
- Take-profit or exit review rule.
- Maximum number of open positions.
- Rule for stopping the bot during unusual market behavior.

Do not run multiple bots against the same live account. Do not mix manual trades
with bot-managed positions unless the bot state design explicitly supports it.

## Future Live Spot Gate

Before live Spot trading can be considered:

- Public monitor must remain stable.
- Telegram alerts must remain stable.
- Candle collection and dashboard must remain stable.
- Dry-run trading must be tested first.
- API key must be minimum-permission, Spot-only, and IP-whitelisted.
- Withdrawal permission remains forbidden forever.

Any future trading code must be reviewed separately and must not be hidden inside
dashboard, logging, indicator, test, or documentation changes.
