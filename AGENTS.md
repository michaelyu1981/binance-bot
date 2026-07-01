# Binance Bot Codex Rules

This project touches real money. Default mode is read-only or dry-run only.

## Absolute safety rules

Codex must never enable live trading unless Michael explicitly says:

"enable live spot trading."

Codex must never enable:
- withdrawals
- futures
- margin
- leverage
- auto-borrow
- cross-margin
- isolated-margin

Codex must keep dry-run enabled unless Michael explicitly approves a change.

Codex must never hardcode:
- Binance API keys
- Binance API secrets
- Telegram bot tokens
- passwords
- server credentials

Secrets must be stored only in `.env`.

`.env` must never be committed to Git.

Codex must not place real orders from:
- scripts
- tests
- examples
- sample code
- debugging commands

Default project phase is:

1. Read-only Binance monitor
2. Telegram alerts
3. Freqtrade dry-run
4. Tiny live Spot only after explicit approval

## Git safety

Codex must not force push.

Codex must not delete branches.

Codex must not rewrite Git history.

Codex must show changes before commit.

Codex must not touch the Bayanifi project from this repository.

## AI Trading Agents Registry

These agent profiles are advisory documentation only. They must not enable
trading, bypass safety gates, place orders, or contain secrets.

### Active Risk & Analyst Agents

- **Michael Burry Agent** (`docs/bots/bot_michael_burry.md`): Contrarian macro/short analyst.
- **Ed Seykota Agent** (`docs/bots/bot_ed_seykota.md`): Long-term mathematical trend follower.
- **William O'Neil Agent** (`docs/bots/bot_william_oneil.md`): Parabolic breakout and volume specialist.
- **Mark Minervini Agent** (`docs/bots/bot_mark_minervini.md`): High-velocity swing trade and risk manager.
- **Stanley Druckenmiller Agent** (`docs/bots/bot_stanley_druckenmiller.md`): Macro liquidity, regime, and asymmetric risk analyst.
- **Linda Raschke Agent** (`docs/bots/bot_linda_raschke.md`): Short-term swing, pattern, and market-structure tactician.
- **Jim Simons Agent** (`docs/bots/bot_jim_simons.md`): Quantitative signal, statistics, and model-risk analyst.
