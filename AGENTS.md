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
