# Progress Roadmap

This document tracks the Binance bot build from safe read-only monitoring toward
the planned full feature set.

## Current Safety Mode

Default mode is read-only or dry-run only.

Forbidden unless explicitly approved:

- live trading
- withdrawals
- futures
- margin
- leverage
- auto-borrow
- cross-margin
- isolated-margin

Secrets must never be hardcoded. API keys, API secrets, Telegram tokens,
passwords, and server credentials belong only in `.env`.

## Completed

### Phase 1: Public Market Monitor

Status: in progress, usable locally.

Completed features:

- Public Binance spot price reader.
- No API key.
- No account access.
- No buy/sell logic.
- No order endpoints.
- Default watchlist in one config location: `app/config.py`.
- Current watchlist:
  - `BTCUSDT`
  - `ETHUSDT`
  - `BNBUSDT`
  - `ZECUSDT`
  - `XTZUSDT`
- One-shot run:
  - `python3 -m app.main`
- CLI help without fetching Binance data:
  - `python3 -m app.main --help`
- Watch mode:
  - `python3 -m app.main --watch`
- Configurable watch interval:
  - `python3 -m app.main --watch --interval 5`
- Timestamped output in Philippine time, UTC+8.
- Daily runtime log files:
  - `logs/market_prices-YYYY-MM-DD.log`
- Local price-change alerts in watch mode.
- Configurable alert threshold:
  - `python3 -m app.main --watch --interval 5 --alert-threshold 1`
- Clean Ctrl+C shutdown in watch mode.

## Next Steps

### Phase 1 Improvements

- Add tests for CLI parsing, watchlist config, timestamp formatting, and alert
  threshold logic.
- Add safer network error handling for watch mode so temporary Binance public
  endpoint failures can be logged without stopping the monitor.
- Consider reading watchlist and alert settings from a safe non-secret config
  file later if the list grows.
- Consider retention cleanup so old daily logs do not grow forever.
- Prepare and review DigitalOcean deployment using
  `docs/digitalocean-deployment.md`.

### Phase 2: Read-Only Account Monitor

Not started.

Safety requirements before this phase:

- Requires Binance API key and secret in `.env` only.
- API key must be read-only.
- No withdrawal permission.
- No futures permission.
- No margin permission.
- No trading permission.
- No order endpoints.

Planned features:

- Read spot account balances only.
- Show portfolio snapshot.
- Log read-only account snapshot.
- Never place orders.

### Phase 3: Telegram Alerts

Not started.

Safety requirements before this phase:

- Telegram token must be stored only in `.env`.
- Do not commit `.env`.
- Telegram sends notifications only.
- Telegram must not trigger trades.

Planned features:

- Send public price-change alerts to Telegram.
- Send read-only account monitor alerts after Phase 2 exists.
- Add quiet hours or alert cooldowns if needed.

### Phase 4: Freqtrade Dry-Run

Not started.

Safety requirements before this phase:

- Dry-run must stay enabled.
- No live trading.
- No futures, margin, leverage, or withdrawals.

Planned features:

- Add dry-run strategy validation.
- Add Docker runtime for dry-run.
- Track dry-run logs and simulated performance.

### Phase 5: Tiny Live Spot

Blocked until explicit approval.

This phase must not begin unless Michael explicitly says:

```text
enable live spot trading.
```

Even then:

- Spot only.
- Tiny size only.
- No withdrawals.
- No futures.
- No margin.
- No leverage.
- No auto-borrow.
- No cross-margin.
- No isolated-margin.
