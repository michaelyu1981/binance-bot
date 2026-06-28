# Binance Bot

This is Michael's Binance bot project.

## Project purpose

Phase 1 is read-only monitoring only.

No buy.
No sell.
No futures.
No margin.
No leverage.
No withdrawals.

## Planned stack

- Mac / VS Code
- Codex for coding assistance
- Git for version control
- Docker for runtime
- DigitalOcean Singapore VPS for 24/7 operation
- Binance API
- Telegram alerts
- Freqtrade dry-run later

## Project phases

1. Public Binance market reader
2. Read-only account monitor
3. Telegram alerts
4. Freqtrade dry-run
5. Tiny live Spot trading only after explicit approval

## Phase 1 public market monitor

The first monitor reads public Binance spot prices only:

- BTC/USDT
- ETH/USDT
- BNB/USDT

It uses Binance's unauthenticated public ticker endpoint. It does not use an API
key, does not access account data, and does not contain buy/sell order code.

Run it with:

```bash
python3 -m app.main
```
