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

- BTCUSDT
- ETHUSDT
- BNBUSDT
- ZECUSDT
- XTZUSDT

It uses Binance's unauthenticated public ticker endpoint. It does not use an API
key, does not access account data, and does not contain buy/sell order code.
It does not call order endpoints and has no trading permissions.

The default watchlist is defined in one safe config location:

```python
app/config.py
```

Run it with:

```bash
python3 -m app.main
```

Every run prints timestamped public prices in Philippine time, UTC+8, and
appends the same lines to:

```text
logs/market_prices.log
```

The log contains only timestamps, symbols, and public market prices. It must not
contain secrets, API keys, account data, order data, Telegram tokens, or trading
credentials. Telegram alerts are not included yet.
