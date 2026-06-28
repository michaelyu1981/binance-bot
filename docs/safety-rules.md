# Safety Rules

Default mode: read-only or dry-run.

Forbidden by default:
- live trading
- withdrawals
- futures
- margin
- leverage
- hardcoded secrets

Real Binance API secrets must only be stored in `.env`.

`.env` must never be committed to Git.
