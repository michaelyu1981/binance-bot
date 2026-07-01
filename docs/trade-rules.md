# CoinPilot Trade Rules

Updated: 2026-07-01
Owner: Michael Yu
Project: CoinPilot
Status: Read-only public market monitor and technical-analysis dashboard

---

## 1. Prime Directive

CoinPilot is currently an advisory-only, read-only public market monitor.

CoinPilot must not trade.

CoinPilot must not place orders, cancel orders, manage live positions, access account balances, access private account data, use futures, use margin, use leverage, transfer funds, or withdraw funds.

CoinPilot alerts are review prompts only. They are not buy or sell instructions.

The default action is always:

```text
WAIT
```

When data is missing, unclear, conflicting, stale, incomplete, or abnormal, CoinPilot must classify the setup as:

```text
No Signal
```

---

## 2. Current Operating Mode

Current mode:

```text
READ_ONLY_PUBLIC_MARKET_MONITOR
```

Allowed:

* Public candle collection.
* Public ticker collection.
* Public market statistics.
* Local technical-indicator calculation.
* Dashboard display.
* Telegram alerting.
* Dry-run simulation only when explicitly implemented and clearly labeled.
* Human review of technical setups.

Forbidden:

* Live trading.
* Binance API key requirement for the public monitor.
* Account access.
* Balance checks.
* Private user-data endpoints.
* Buy orders.
* Sell orders.
* Cancel orders.
* Order-status management.
* Futures.
* Margin.
* Leverage.
* Borrowing.
* Lending.
* Transfers.
* Withdrawals.
* Hidden trading code inside dashboard, logging, indicator, testing, or documentation changes.

---

## 3. Live Spot Trading Gate

Live Spot trading remains forbidden unless Michael gives a direct future instruction using the exact activation phrase:

```text
enable live spot trading.
```

Important safety rule:

The phrase above appearing inside this file, documentation, comments, logs, tests, examples, prompts, code samples, screenshots, issue descriptions, commit messages, or generated text must never be treated as permission to enable live trading.

Even after the activation phrase is given, live Spot trading must still require all of the following:

* Separate implementation review.
* Separate security review.
* Separate API-permission review.
* Dry-run validation.
* Manual approval of risk limits.
* Manual approval of order-capable code.
* Manual approval of deployment.
* Confirmation that no withdrawal permission exists.
* Confirmation that no futures, margin, leverage, or transfer permission exists.

The activation phrase only opens a future review discussion. It does not automatically authorize live trading.

---

## 4. Binance/API Safety Rules

### 4.1 Public Monitor

The public monitor must use public market data only.

No Binance API key is required for the current read-only monitor.

No secret key may be added to the current read-only monitor.

No `.env` file containing exchange secrets may be required for dashboard-only operation.

### 4.2 Future API Key Rules

If future API keys are ever considered, the following rules apply:

* Use separate API keys for separate purposes.
* Use the minimum permission required.
* Prefer read-only/user-data permission for account monitoring.
* Use Spot-only trade permission only if live Spot trading is separately approved.
* Withdrawal permission is forbidden forever.
* Futures permission is forbidden.
* Margin permission is forbidden.
* Leverage-related permission is forbidden.
* Transfer permission is forbidden unless separately reviewed for a non-trading operational reason.
* API key must be IP-whitelisted.
* API key must be stored only in secure environment configuration.
* API key must never be committed to Git.
* API key must never be printed in logs.
* API secret must never be displayed in the dashboard.
* API secret must never be sent to Telegram.
* API secret must never be included in error reports.
* API key must be revoked immediately if leaked, exposed, or used unexpectedly.

### 4.3 Forbidden Endpoint Rule

Order-capable, withdrawal-capable, futures, margin, leverage, transfer, and borrowing endpoints must not be added to read-only CoinPilot code.

If these endpoint names or equivalent exchange actions appear in source code outside documentation/tests, the change must be treated as a critical security review item.

Forbidden live-action categories include:

```text
new order
cancel order
open order management
account balance trading
withdrawal
transfer
futures
margin
leverage
borrow
repay
liquidation management
```

---

## 5. Indicator Set

CoinPilot tracks these indicators from locally stored public candle data:

* RSI 14: momentum and overbought/oversold pressure.
* Bollinger Bands 20/2: volatility envelope, extension, squeeze, and breakout context.
* EMA 20: short-term/current direction.
* SMA 50: medium-term/bigger direction.
* MACD 12/26/9: momentum direction and trend confirmation.
* MACD Histogram: momentum acceleration/deceleration.
* Volume: participation and breakout confirmation.
* Volume MA 20: recent average volume baseline.
* ATR 14: volatility, stop-distance planning, and risk control.
* ATR Percent: ATR as a percentage of price, useful for comparing volatility across coins.
* Price structure: support, resistance, swing high, swing low, breakout, retest, and trend sequence.

These indicators are not automatic buy or sell signals. They are inputs for human review.

Simple interpretation:

```text
EMA 20 = current direction
SMA 50 = bigger direction
MACD = momentum direction
MACD Histogram = momentum strength/change
RSI = too hot, too weak, or healthy momentum
Bollinger Bands = volatility and price-extension context
Volume = participation confirmation
ATR = risk and expected movement range
Price structure = where the trade idea is right or wrong
```

---

## 6. Data Quality Rules

CoinPilot must not classify a symbol/timeframe unless data quality is acceptable.

Required:

* Use closed candles only for alert classification.
* In-progress candles may be displayed but must not trigger Watch, Strong Watch, or Avoid.
* Prefer at least 100 closed candles before generating technical classifications.
* Do not use future candles when calculating past signals.
* Do not use repainting logic.
* Do not classify a symbol if candle data has gaps.
* Do not classify a symbol if the latest candle is stale.
* Do not classify a symbol if indicator values are `null`, `NaN`, infinite, or incomplete.
* Do not classify a symbol if exchange symbol metadata is missing.
* Do not classify a symbol if the current price is abnormal compared with recent candles.
* Do not classify a symbol if there is a suspected bad tick, API issue, or candle-ingestion error.

If data quality fails:

```text
classification = No Signal
reason = Data quality failed
```

---

## 7. Market Universe Rules

CoinPilot should prioritize liquid Spot pairs only.

Preferred quote assets:

```text
USDT
USDC
FDUSD
```

Avoid or deprioritize:

* Very low-liquidity coins.
* Newly listed coins without enough candle history.
* Coins with abnormal spread.
* Coins with thin order books.
* Coins with unstable trading status.
* Coins with extreme one-candle pumps.
* Coins with suspected manipulation.
* Coins dominated by one news event.
* Coins that cannot be exited cleanly without large slippage.

For future dry-run or live review, a symbol must pass a liquidity filter before any trade simulation is considered.

Recommended liquidity checks:

* 24h quote volume is above the configured minimum.
* Spread is below the configured maximum.
* Candle volume is not consistently near zero.
* The coin has enough historical candles for all monitored timeframes.
* The symbol is actively tradeable on Spot.

---

## 8. Timeframe Rule

CoinPilot must not evaluate a coin from one timeframe only.

Preferred review structure:

```text
1D  = major trend bias
4H  = setup quality
1H  = timing and confirmation
15M = fine timing only, optional
```

Rules:

* 1D defines the major bias.
* 4H defines the main setup.
* 1H may refine timing.
* 15M must never override bearish 4H/1D context.
* A lower-timeframe bullish signal is weaker when 4H and 1D are bearish.
* A lower-timeframe breakout is lower quality if it is pushing directly into 4H or 1D resistance.
* A 1H or 15M signal must be labeled as short-timeframe only unless the 4H agrees.
* The safest long setups occur when 1D is not bearish, 4H is improving or bullish, and 1H confirms.

Default monitoring priority:

```text
Primary setup timeframe: 4H
Trend filter timeframe: 1D
Timing timeframe: 1H
```

---

## 9. Market Regime Rule

CoinPilot must consider market regime before interpreting indicators.

### 9.1 Trending Market

A market may be trending when:

* Price is above EMA 20 and SMA 50.
* EMA 20 is above SMA 50.
* EMA 20 and SMA 50 are both rising.
* Pullbacks respect EMA 20 or prior support.
* MACD stays mostly above signal or histogram recovers quickly.
* RSI holds above 45 to 50 during pullbacks.

In a trending market, prioritize:

```text
EMA 20
SMA 50
MACD
Volume
Pullback structure
ATR risk
```

Do not sell or reject a setup only because RSI is above 70 in a strong trend. RSI can stay elevated during strong momentum. Instead, check whether price is overextended, volume is fading, and MACD histogram is weakening.

### 9.2 Ranging Market

A market may be ranging when:

* Price repeatedly crosses EMA 20 and SMA 50.
* EMA 20 and SMA 50 are flat or tangled.
* MACD repeatedly whipsaws around the zero line.
* RSI repeatedly moves between 40 and 60.
* Bollinger Bands contain price without directional expansion.

In a ranging market, prioritize:

```text
Support
Resistance
Bollinger Bands
RSI
Volume at range boundaries
```

Avoid breakout alerts inside a choppy range unless price closes beyond the range with volume confirmation.

### 9.3 High-Volatility News/Pump Market

A market may be abnormal when:

* Candle body is unusually large.
* ATR suddenly expands.
* Price gaps or jumps far from EMA 20.
* Volume spikes far above normal.
* News, liquidation, exchange event, listing event, exploit, hack, lawsuit, delisting, or macro event may be driving the move.

In abnormal conditions:

```text
Do not chase.
Wait for confirmation.
Prefer No Signal or Avoid.
Require a separate written breakout plan before considering a late move.
```

---

## 10. Alert Classification

CoinPilot alerts are review prompts only.

Alert levels:

```text
Strong Watch
Watch
Avoid
No Signal
```

### 10.1 No Signal

Use `No Signal` when:

* Conditions are mixed.
* Conditions are weak.
* Indicators conflict.
* Data is incomplete.
* No clear setup exists.
* Price is in the middle of a range.
* The move is too late.
* The chart requires guessing.
* The setup is not clean enough for review.

Default classification:

```text
No Signal
```

### 10.2 Watch

Use `Watch` when:

* No hard blocker is active.
* At least 5 of 9 long setup conditions are true.
* The setup is improving but not fully confirmed.
* Price is near a possible trigger area but has not fully confirmed.
* Human review is useful.

Meaning:

```text
Watch = Review this coin. Do not assume entry.
```

### 10.3 Strong Watch

Use `Strong Watch` when:

* No hard blocker is active.
* At least 7 of 9 long setup conditions are true.
* Trend, momentum, volume, and risk conditions mostly agree.
* Higher timeframe does not strongly conflict.
* Price is not excessively extended.
* A clear invalidation level exists.
* A reasonable reward-to-risk path exists.

Meaning:

```text
Strong Watch = High-quality review candidate. Still not a buy instruction.
```

### 10.4 Avoid

Use `Avoid` when:

* Any hard blocker is active.
* Risk is unclear or excessive.
* Price is extended and emotional.
* Setup depends on hope or FOMO.
* Indicators conflict strongly.
* Data quality is poor.
* Liquidity is poor.
* The coin is behaving abnormally.

Meaning:

```text
Avoid = Do not enter a new long setup under current conditions.
```

---

## 11. Hard Blockers

A long watchlist alert must be blocked when any hard blocker is active.

Hard blockers:

* Price is below both EMA 20 and SMA 50 on the setup timeframe.
* 4H and 1D are both bearish.
* MACD is bearish and still weakening.
* RSI is above 70 while price is extended above the upper Bollinger Band.
* RSI is above 80 on a non-breakout setup.
* RSI is below 40 and falling with bearish MACD.
* Price is chasing far above EMA 20 without a pullback or breakout plan.
* Price is far above the upper Bollinger Band without volume-confirmed breakout structure.
* Price is directly under major resistance with poor reward-to-risk.
* Volume does not support the breakout.
* ATR makes the required stop too wide for allowed risk.
* Candle is unusually wide and likely driven by news, liquidation, or pump behavior.
* Spread or liquidity is unacceptable.
* Candle data is stale, incomplete, or abnormal.
* The setup depends on averaging down.
* The setup depends on hope.
* The setup depends on fear of missing out.
* The setup has no clear invalidation level.
* The setup has no logical exit plan.
* Reward-to-risk is below the required minimum for future trade review.

If any hard blocker is active:

```text
classification = Avoid
```

Exception:

If the hard blocker is caused only by incomplete/missing data, use:

```text
classification = No Signal
reason = Missing or incomplete data
```

---

## 12. Indicator Definitions for Code

### 12.1 EMA 20

EMA 20 measures short-term direction.

Bullish:

```text
close > EMA20
EMA20 rising
```

Bearish:

```text
close < EMA20
EMA20 falling
```

### 12.2 SMA 50

SMA 50 measures bigger/intermediate direction.

Bullish:

```text
close > SMA50
SMA50 rising or flat-to-rising
```

Bearish:

```text
close < SMA50
SMA50 falling
```

### 12.3 EMA 20 / SMA 50 Trend Alignment

Bullish trend alignment:

```text
close > EMA20
close > SMA50
EMA20 > SMA50
```

Weak trend alignment:

```text
close > EMA20
close near or below SMA50
EMA20 <= SMA50
```

Bearish trend alignment:

```text
close < EMA20
close < SMA50
EMA20 < SMA50
```

### 12.4 MACD 12/26/9

MACD bullish:

```text
MACD line > signal line
MACD histogram > 0
```

MACD improving:

```text
MACD histogram is higher than previous closed candle
or
MACD line is moving toward signal line from below
```

MACD bearish:

```text
MACD line < signal line
MACD histogram < 0
```

MACD bearish and still weakening:

```text
MACD line < signal line
and
MACD histogram < 0
and
MACD histogram is lower than previous closed candle
```

### 12.5 RSI 14

RSI zones:

```text
RSI < 30   = oversold context, not automatic buy
30-40      = weak
40-45      = weak-to-neutral
45-55      = neutral
55-65      = healthy bullish momentum
65-70      = strong but watch extension
70-80      = overbought/extended context
> 80       = extreme extension
```

Preferred long setup RSI:

```text
45 <= RSI <= 70
```

Best momentum zone for non-chasing long review:

```text
50 <= RSI <= 65
```

RSI caution:

* RSI above 70 is not automatically bearish in a strong trend.
* RSI below 30 is not automatically bullish in a strong downtrend.
* RSI must be interpreted with trend, structure, MACD, Bollinger Bands, and volume.

### 12.6 Bollinger Bands 20/2

Bollinger context:

```text
middle band = 20-period moving average
upper/lower bands = volatility envelope
band expansion = volatility increasing
band contraction = volatility decreasing
```

Constructive long context:

```text
close above middle band
close not excessively far above upper band
bands starting to expand after squeeze
breakout close confirmed with volume
```

Warning context:

```text
close far above upper band
RSI > 70
volume fading
MACD histogram weakening
no retest
no support nearby
```

Squeeze context:

```text
Bollinger bandwidth is low relative to recent candles
price consolidates
breakout direction is not confirmed until candle close and volume confirmation
```

### 12.7 Volume and Volume MA 20

Volume confirmation:

```text
current closed-candle volume >= VolumeMA20
```

Stronger breakout confirmation:

```text
current closed-candle volume >= 1.2 * VolumeMA20
```

Very strong breakout confirmation:

```text
current closed-candle volume >= 1.5 * VolumeMA20
```

Weak breakout warning:

```text
price breaks resistance
but
volume < VolumeMA20
```

Do not classify a breakout as Strong Watch without volume confirmation.

### 12.8 ATR 14 and ATR Percent

ATR measures volatility. It is used for risk planning, not direction.

ATR Percent:

```text
ATRP = ATR14 / close * 100
```

ATR risk interpretation:

```text
low ATRP    = tighter expected movement
normal ATRP = acceptable risk planning
high ATRP   = wider stops required
extreme ATRP = avoid or reduce size
```

ATR must be used to check whether a stop is realistic.

If the required stop distance is too wide for allowed risk, reject the setup or reduce position size in future dry-run/live logic.

---

## 13. Long Setup Conditions

CoinPilot uses 9 long setup conditions.

These are advisory conditions for alert classification only.

### Condition 1: Setup-Timeframe Price Trend

True when:

```text
close > EMA20
close > SMA50
```

### Condition 2: Moving Average Alignment

True when:

```text
EMA20 > SMA50
```

Or early improvement is allowed when:

```text
close reclaimed EMA20
EMA20 is flattening or rising
price is approaching SMA50 from below
```

Early improvement may support `Watch` but should not alone support `Strong Watch`.

### Condition 3: Higher-Timeframe Context

True when:

```text
1D is bullish or neutral
and
4H is bullish or improving
```

False when:

```text
1D bearish
and
4H bearish
```

### Condition 4: MACD Momentum

True when:

```text
MACD line > signal line
```

Or:

```text
MACD histogram improving for at least 2 closed candles
```

Stronger when:

```text
MACD histogram > 0
and
MACD histogram increasing
```

### Condition 5: RSI Health

True when:

```text
45 <= RSI <= 70
```

Best zone:

```text
50 <= RSI <= 65
```

False or caution when:

```text
RSI > 70 and price extended
RSI < 40 and falling
```

### Condition 6: Bollinger Context

True when:

```text
close >= Bollinger middle band
and
price is not excessively extended above upper band
```

Also true for breakout review when:

```text
close above upper band
and
volume confirms
and
breakout plan exists
```

False when:

```text
price is far above upper band
and
RSI > 70
and
volume is fading
```

### Condition 7: Volume Confirmation

True when:

```text
current volume >= VolumeMA20
```

For breakout setups, prefer:

```text
current volume >= 1.2 * VolumeMA20
```

Strong breakout volume:

```text
current volume >= 1.5 * VolumeMA20
```

### Condition 8: ATR Risk Acceptability

True when:

```text
planned stop distance is realistic
and
ATR is not too wide for the allowed risk
and
position size can be reduced enough to respect risk limits
```

False when:

```text
required stop distance is too wide
or
volatility is abnormal
or
risk cannot be defined
```

### Condition 9: Price Structure

True when at least one exists:

```text
higher low
breakout and close above resistance
successful retest of breakout level
pullback to EMA20 with support
reclaim of EMA20 after consolidation
range breakout with volume
```

False when:

```text
price is directly below strong resistance
price is in the middle of a choppy range
price has no clear support nearby
price has no invalidation level
```

---

## 14. Alert Scoring Rules

Use only closed candles.

Score the 9 long setup conditions:

```text
true  = 1 point
false = 0 points
```

Classification:

```text
Avoid        = any hard blocker is active
Strong Watch = no hard blockers and score >= 7/9
Watch        = no hard blockers and score >= 5/9
No Signal    = no hard blockers and score < 5/9
```

Additional Strong Watch requirements:

Strong Watch must include all core confirmations:

```text
trend condition true
momentum condition true
volume condition true
ATR/risk condition true
higher timeframe not strongly bearish
```

If score is 7/9 but one core confirmation is missing:

```text
classification = Watch
```

CoinPilot must include the score and reasons in every alert.

Example:

```text
classification: Strong Watch
score: 8/9
blockers: none
reasons:
- close above EMA20 and SMA50
- EMA20 above SMA50
- 4H improving and 1D neutral
- MACD bullish
- RSI 58
- price above Bollinger middle, not extended
- volume 1.3x VolumeMA20
- ATR risk acceptable
- breakout retest confirmed
```

---

## 15. Entry Review Rules

CoinPilot does not enter trades.

For future dry-run or human review, a long setup must have a defined entry trigger.

Valid long entry trigger types:

### 15.1 Breakout Close

A breakout close setup requires:

```text
price closes above defined resistance
volume >= 1.2 * VolumeMA20
MACD bullish or improving
RSI not extremely extended
ATR risk acceptable
```

Avoid breakout entries when:

```text
price closes far above upper Bollinger Band
RSI > 75
volume fades
candle is unusually large
reward-to-risk is poor
```

### 15.2 Pullback Continuation

A pullback continuation setup requires:

```text
higher timeframe bullish or neutral
price above SMA50
pullback toward EMA20 or support
RSI holds above 45
MACD histogram stabilizes or improves
bearish volume decreases
```

Preferred trigger:

```text
closed candle reclaims EMA20
or
closed candle bounces from support
```

### 15.3 Breakout Retest

A breakout retest setup requires:

```text
prior resistance becomes support
price retests breakout level
volume does not show strong selling
RSI remains above 45
MACD remains constructive
```

This is usually safer than chasing the first breakout candle.

### 15.4 Bollinger Squeeze Breakout

A Bollinger squeeze breakout setup requires:

```text
bands contracted before breakout
price closes outside consolidation range
volume confirms
MACD improves
RSI supports direction
ATR not excessively wide before entry
```

Avoid if the breakout candle is too large and creates poor reward-to-risk.

---

## 16. No-Chase Rules

CoinPilot must avoid emotional late entries.

Do not classify `Strong Watch` if:

```text
price is far above EMA20
and
price is above upper Bollinger Band
and
RSI > 70
and
no retest has occurred
```

Do not classify `Strong Watch` if:

```text
the candle is unusually large
and
entry would require a very wide stop
```

Do not chase:

* First pump candle.
* News spike.
* Liquidation wick.
* Listing spike.
* Social-media hype candle.
* Breakout without volume.
* Breakout directly into major resistance.
* Move where stop placement is unclear.

When in doubt:

```text
Wait for retest.
```

---

## 17. Exit Planning Rules

CoinPilot does not exit live trades because live trading is not enabled.

For future dry-run or human review, every trade idea must have an exit plan before entry.

A trade idea is incomplete without:

* Entry trigger.
* Invalidation level.
* Stop-loss plan.
* Take-profit plan.
* Reward-to-risk estimate.
* Time stop or review rule.
* Position sizing rule.
* Emergency exit rule.

If any are missing:

```text
classification cannot exceed Watch
```

---

## 18. Stop-Loss / Invalidation Rules

For future dry-run or human review, the stop must be based on where the trade idea is wrong.

Valid invalidation references:

* Below recent swing low.
* Below support.
* Below breakout retest level.
* Below EMA20 reclaim level.
* Below range low.
* ATR-buffered invalidation level.

Invalid stop methods:

* Random percentage.
* Emotional comfort level.
* Stop so tight that normal volatility will likely hit it.
* Stop so wide that risk becomes unacceptable.
* Moving stop lower to avoid taking a loss.
* Removing stop after entry.
* Averaging down instead of respecting invalidation.

For long setups:

```text
candidate_stop = invalidation_level - ATR_buffer
```

Recommended ATR buffer:

```text
ATR_buffer = 0.2 * ATR14
```

If the stop distance is too wide:

```text
reduce position size
or
reject the trade idea
```

Never increase risk because the setup "looks certain."

---

## 19. Reward-to-Risk Rules

For future dry-run or human review:

Minimum acceptable planned reward-to-risk:

```text
2R
```

Preferred:

```text
2.5R or higher
```

Definitions:

```text
R = amount risked if stop is hit
1R = entry price - stop price for long trades
2R target = entry price + 2 * R
```

Reject or downgrade setup when:

```text
nearest resistance is too close
target is unclear
stop is too wide
price is already extended
planned reward-to-risk < 2R
```

A high win rate is not enough. The trade must have logical upside compared with defined downside.

---

## 20. Profit Management Rules

For future dry-run or human review:

Possible profit management framework:

### 20.1 At +1R

Review trade quality.

Allowed actions:

```text
hold if trend remains strong
consider reducing risk
consider moving stop only if structure supports it
```

Do not move stop blindly if normal volatility would stop out a valid trend.

### 20.2 At +1.5R to +2R

Allowed actions:

```text
take partial profit
or
trail stop using structure, EMA20, or ATR
```

### 20.3 Trend Continuation

If trend remains strong:

```text
price above EMA20
MACD constructive
RSI not showing severe bearish divergence
volume supports continuation
```

Then allow trailing plan rather than fixed full exit.

### 20.4 Momentum Weakening

Consider exit review when:

```text
MACD histogram weakens for multiple closed candles
price closes below EMA20
RSI loses 50
volume increases on selling candles
price fails breakout retest
higher timeframe turns bearish
```

### 20.5 Never Let a Good Trade Become an Uncontrolled Trade

If a trade reaches meaningful profit, a protection plan must exist.

No open-ended hope-based holding.

---

## 21. Bad Trade Exit Rules

A bad trade must be exited or invalidated properly.

For future dry-run or human review, exit or mark failed when:

* Price closes below the invalidation level.
* Stop-loss level is reached.
* Breakout fails and closes back below resistance.
* Retest fails.
* MACD turns bearish with price below EMA20.
* RSI loses 45 or 50 depending on setup.
* Selling volume expands.
* Higher timeframe becomes bearish.
* Trade has no progress after the defined time stop.
* Original reason for the trade is no longer valid.

Do not:

* Average down.
* Move stop lower.
* Add to losing position.
* Ignore invalidation.
* Convert a short-term trade into a long-term hold.
* Blame the market after violating the plan.

---

## 22. Time Stop Rule

Some trades are wrong because they fail to move.

For future dry-run or human review:

If a setup does not make progress within a reasonable number of candles, review or close the idea.

Suggested review windows:

```text
15M setup = review after 8 to 12 candles
1H setup  = review after 6 to 10 candles
4H setup  = review after 4 to 8 candles
1D setup  = review after 3 to 5 candles
```

If price is flat, volume fades, and momentum weakens:

```text
exit review = required
```

---

## 23. Risk Rules

Live trading is currently forbidden.

Before any future live Spot trade exists, define and approve these limits in writing:

* Maximum risk per trade.
* Maximum position size per coin.
* Maximum total account exposure.
* Maximum daily loss.
* Maximum weekly loss.
* Maximum drawdown pause level.
* Maximum number of open positions.
* Maximum exposure to correlated coins.
* Stop-loss or invalidation method.
* Take-profit or exit review method.
* Emergency stop procedure.
* Rule for stopping during unusual market behavior.

Recommended conservative defaults for future dry-run testing:

```text
max_risk_per_trade = 0.5% of account equity
max_total_open_risk = 2% of account equity
max_daily_loss = 2% of account equity
max_weekly_loss = 5% of account equity
max_open_positions = 3
max_position_notional_per_coin = 5% of account equity
max_total_spot_exposure = 15% of account equity
```

These are dry-run defaults only. They do not authorize live trading.

---

## 24. Position Sizing Formula

For future dry-run or reviewed Spot logic:

```text
risk_amount = account_equity * risk_per_trade_percent
unit_risk = entry_price - stop_price
position_quantity = risk_amount / unit_risk
position_notional = position_quantity * entry_price
```

Rules:

* Position size must be based on stop distance.
* Position size must shrink when volatility increases.
* Position size must respect exchange minimum order size.
* Position size must respect maximum notional limits.
* Position size must respect total exposure limits.
* If correct position size is too small or invalid, skip the trade.
* Never size based on confidence alone.
* Never increase size after losses to recover.

---

## 25. Correlation Rules

Do not treat correlated coins as separate risk.

Examples of correlated exposure:

* Multiple large-cap crypto longs during the same Bitcoin breakdown risk.
* Several AI coins moving together.
* Several meme coins moving together.
* Several Layer 1 coins moving together.
* Coin and ecosystem token moving together.

Future dry-run/live logic must reduce exposure when trades are highly correlated.

Rule:

```text
If multiple coins depend on the same market move, total risk must be capped as one combined idea.
```

---

## 26. Bitcoin and Market Context Rule

For crypto Spot longs, Bitcoin and total market context matter.

Before classifying smaller coins as Strong Watch, check:

* BTC trend on 1D and 4H.
* BTC position relative to EMA20 and SMA50.
* BTC MACD direction.
* BTC volatility condition.
* Whether BTC is breaking support or resistance.
* Whether the broader market is risk-on or risk-off.

If BTC is strongly bearish:

```text
downgrade altcoin long alerts
```

If BTC is crashing or highly abnormal:

```text
avoid new altcoin long alerts
```

Exception:

A coin may remain Watch if it has unusual relative strength, strong volume, and clear structure, but it must be labeled as higher risk.

---

## 27. News and Event Risk Rule

Technical indicators can fail during major events.

Avoid or downgrade new long alerts during:

* Exchange outages.
* Binance-specific incidents.
* Token unlocks.
* Delisting announcements.
* Major regulatory news.
* Security exploits.
* Stablecoin depeg events.
* Major macroeconomic news.
* Sudden Bitcoin liquidation cascades.
* Abnormal funding/liquidation events.
* Extreme social-media pump behavior.

During abnormal events:

```text
classification = Avoid or No Signal
```

Do not force technical interpretation during event-driven volatility.

---

## 28. Alert Message Requirements

Every CoinPilot alert must include:

* Symbol.
* Timeframe.
* Classification.
* Score.
* Trigger type.
* Current price.
* Indicator snapshot.
* Hard blockers.
* Reasons for classification.
* Higher timeframe context.
* Volume status.
* ATR/risk status.
* Warning notes.
* Timestamp.
* Statement that alert is not a buy/sell instruction.

Example Telegram format:

```text
CoinPilot Alert: STRONG WATCH

Symbol: BTCUSDT
Timeframe: 4H
Score: 8/9
Trigger: Breakout retest

Trend:
- Close above EMA20 and SMA50
- EMA20 above SMA50

Momentum:
- MACD bullish
- Histogram improving
- RSI 58

Volatility/Volume:
- Price above Bollinger middle band, not extended
- Volume 1.3x VolumeMA20
- ATR risk acceptable

Higher Timeframe:
- 1D neutral-to-bullish

Blockers:
- None

Reminder:
This is a review prompt only. Not a buy/sell instruction.
```

---

## 29. Dashboard Display Requirements

Dashboard should clearly separate:

```text
Market data
Indicators
Alert classification
Risk context
Human notes
```

Dashboard must not show alerts as guaranteed signals.

Use labels:

```text
No Signal
Watch
Strong Watch
Avoid
```

Do not use labels like:

```text
Buy Now
Guaranteed Entry
Sure Win
Pump Signal
Auto Buy
```

For each symbol, show:

* Price.
* 24h change.
* Volume.
* EMA20.
* SMA50.
* RSI14.
* MACD line.
* MACD signal.
* MACD histogram.
* Bollinger position.
* ATR14.
* ATR Percent.
* Alert classification.
* Reason summary.
* Last candle close time.
* Data health.

---

## 30. Dry-Run Rules

Dry-run trading may be implemented only after the read-only monitor is stable.

Dry-run must:

* Use simulated capital.
* Use no live order endpoints.
* Include realistic fees.
* Include realistic slippage.
* Use closed candles only.
* Avoid look-ahead bias.
* Record every simulated decision.
* Record entry reason.
* Record exit reason.
* Record stop level.
* Record target level.
* Record position size.
* Record R multiple.
* Record maximum adverse excursion.
* Record maximum favorable excursion.
* Record time in trade.
* Support exportable reports.
* Be clearly labeled as dry-run.

Dry-run must not:

* Use live balances.
* Use live orders.
* Use live API trade permission.
* Share code paths with live trading unless separately reviewed.
* Hide failures.
* Delete losing trades.
* Modify historical results.

---

## 31. Backtesting Rules

Before any future live Spot review, strategy logic must be backtested.

Backtesting must include:

* Fees.
* Slippage.
* Closed-candle execution assumptions.
* No look-ahead bias.
* No future data leakage.
* Sufficient number of trades.
* Multiple market regimes.
* Bull markets.
* Bear markets.
* Ranging markets.
* High-volatility periods.
* Low-liquidity periods.
* Separate in-sample and out-of-sample testing.
* Walk-forward or forward-test review if possible.

Metrics to track:

* Win rate.
* Average win.
* Average loss.
* Expectancy.
* Profit factor.
* Maximum drawdown.
* Maximum losing streak.
* Average R.
* Median R.
* Best trade.
* Worst trade.
* Time in trade.
* Number of trades.
* Slippage sensitivity.
* Fee sensitivity.
* Performance by timeframe.
* Performance by market regime.

Minimum review questions:

```text
Does the system survive bad markets?
Does it rely on one lucky trade?
Does it overtrade?
Does it fail during chop?
Does it chase pumps?
Does it cut losses?
Does it protect profits?
Does it improve after fees and slippage?
```

Backtest success does not authorize live trading.

---

## 32. Overfitting Protection

Do not keep changing rules only to improve past results.

Avoid:

* Too many indicators.
* Too many special exceptions.
* Coin-specific curve fitting.
* Timeframe-specific curve fitting without reason.
* Optimizing only for win rate.
* Ignoring drawdown.
* Ignoring fees.
* Ignoring slippage.
* Ignoring losing streaks.
* Optimizing on one market regime only.

Prefer:

```text
simple rules
clear risk control
few indicators
robust performance
lower drawdown
consistent expectancy
```

---

## 33. Human Review Checklist

Before acting on any future manually reviewed long setup, answer:

```text
1. What is the trend?
2. What is the setup?
3. What is the trigger?
4. Where is the invalidation?
5. Where is the stop?
6. What is the target?
7. What is the reward-to-risk?
8. Is volume confirming?
9. Is ATR risk acceptable?
10. Is BTC/market context supportive?
11. Is this a chase?
12. Is there news risk?
13. What will make me exit?
14. What will make me take profit?
15. What will make me admit I am wrong?
```

If these cannot be answered:

```text
Do not enter.
```

---

## 34. Emotional Discipline Rules

CoinPilot must reject setups based on:

* Hope.
* FOMO.
* Revenge trading.
* Averaging down.
* “It already dropped a lot.”
* “It already pumped, so it will continue.”
* “I need to recover losses.”
* “This coin is popular.”
* “Telegram says it will pump.”
* “Influencers are talking about it.”
* “The chart looks exciting but has no plan.”

Required mindset:

```text
No plan = no trade.
No stop = no trade.
No reward-to-risk = no trade.
No confirmation = wait.
```

---

## 35. Code Change Safety Rules

Any future code change involving trading must be isolated and reviewed.

Trading code must not be hidden inside:

* Dashboard UI changes.
* Logging changes.
* Indicator changes.
* Telegram alert changes.
* Database migrations.
* Refactors.
* Test helpers.
* Documentation-only updates.
* Dependency updates.

Any code that can place, cancel, or manage orders must be in clearly named files and modules.

Suggested naming if ever approved:

```text
trading/
execution/
orders/
risk/
position_manager/
```

Until live trading is approved, these modules must not exist in production order-capable form.

---

## 36. Required Repo Safety Checks

Before merging CoinPilot changes, check:

```text
No API secrets committed.
No order endpoints added.
No withdrawal endpoints added.
No futures endpoints added.
No margin endpoints added.
No leverage logic added.
No hidden trade execution added.
No live API key required.
No dashboard button can place orders.
No Telegram command can place orders.
No cron job can place orders.
No background worker can place orders.
```

If any check fails:

```text
merge blocked
```

---

## 37. Emergency Stop Rules

Future dry-run or live review must include an emergency stop.

Emergency stop must disable:

* Signal generation if data is bad.
* Telegram trade-like alerts if calculations fail.
* Dry-run execution if state is inconsistent.
* Any future live execution if ever approved.

Emergency stop triggers:

* API data corruption.
* Missing candles.
* Incorrect timestamps.
* Unexpected symbol metadata.
* Abnormal number of alerts.
* Rapid repeated losses in dry-run.
* Strategy drawdown exceeds limit.
* Binance API error spike.
* Server clock drift.
* Database inconsistency.
* Duplicate bot instance detected.
* Manual emergency stop file/flag enabled.

Suggested emergency flag:

```text
COINPILOT_KILL_SWITCH=true
```

When kill switch is active:

```text
No alerts above No Signal
No dry-run entries
No live trading
Dashboard shows kill-switch warning
```

---

## 38. Multiple Bot and Manual Trade Rules

Do not run multiple bots against the same live account.

Do not mix manual trades with bot-managed positions unless state design explicitly supports it.

Future live system must know:

* Current open positions.
* Entry price.
* Stop level.
* Target level.
* Position size.
* Reason for entry.
* Whether position is manual or bot-managed.
* Whether bot is allowed to touch it.

If state ownership is unclear:

```text
bot must not act
```

---

## 39. Final Live Spot Requirements

Before live Spot trading can be considered:

* Public monitor stable.
* Telegram alerts stable.
* Candle collection stable.
* Indicator calculations verified.
* Alert scoring verified.
* Dashboard stable.
* Dry-run implemented.
* Dry-run tested across multiple regimes.
* Backtest completed.
* Risk rules approved.
* API key security approved.
* Spot-only trade permission reviewed.
* IP whitelist enabled.
* Withdrawal permission absent.
* Futures/margin/leverage absent.
* Kill switch implemented.
* Logs implemented.
* Error handling implemented.
* Duplicate-instance protection implemented.
* Manual final approval given.

Live Spot trading must remain forbidden until every item above is complete and approved.

---

## 40. Final Rule

CoinPilot exists to help Michael avoid emotional trading, not to create new risk.

The system must prefer missed opportunities over uncontrolled losses.

Final priority order:

```text
1. Protect capital
2. Protect account security
3. Avoid bad trades
4. Wait for clean setups
5. Enter only with a plan
6. Exit properly when wrong
7. Protect profit when right
8. Never hide trading capability
9. Never enable withdrawals
10. Never trade without explicit approval
```

Default action:

```text
WAIT
```
