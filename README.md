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

Show command help without fetching Binance data:

```bash
python3 -m app.main --help
```

Run continuously until Ctrl+C:

```bash
python3 -m app.main --watch
```

Run continuously with a 5-second interval:

```bash
python3 -m app.main --watch --interval 5
```

Run continuously with a 5-second interval and 1% local price-change alerts:

```bash
python3 -m app.main --watch --interval 5 --alert-threshold 1
```

Every run prints timestamped public prices in Philippine time, UTC+8, and
appends the same lines to:

```text
logs/market_prices.log
```

The log contains only timestamps, symbols, and public market prices. It must not
contain secrets, API keys, account data, order data, Telegram tokens, or trading
credentials.

In watch mode, the monitor compares each symbol's current public price to its
previous cycle price. If the change is at least the configured threshold, it
prints an `ALERT` line and appends the same line to `logs/market_prices.log`.
The default alert threshold is `1.0%`.

## Optional Telegram alerts

Telegram alerts are optional. They send only `ALERT` lines from watch mode when
the configured price-change threshold is reached. Telegram does not receive
normal price lines.

The monitor still uses Binance public market data only:

- No Binance API key
- No Binance account access
- No buy/sell
- No order endpoints

Configure Telegram with environment variables:

```bash
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Leave either value empty to disable Telegram. The app will still print and log
alerts locally.

Do not commit `.env`. Do not paste Telegram tokens into README, docs, code,
tests, logs, screenshots, or Codex prompts.

If Telegram delivery fails, the app prints a clear send failure message without
printing the Telegram token.

## Docker local setup

The Docker setup runs the same read-only public market monitor. It does not add
Binance account access, buy/sell logic, order endpoints, Freqtrade, or
DigitalOcean deployment.

Build the local image:

```bash
docker compose build
```

Run once:

```bash
docker compose run --rm binance-bot python3 -m app.main
```

Run in watch mode:

```bash
docker compose run --rm binance-bot python3 -m app.main --watch --interval 60 --alert-threshold 0.5
```

Docker Compose reads `.env` for variable substitution if present, and passes
only the Telegram variables into the container. Telegram settings must come from
environment variables only:

```bash
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

The `.env` file is ignored by Git and is excluded from the Docker image build
context. Logs are mounted from `./logs` to `/app/logs` so
`logs/market_prices.log` persists outside the container.

Avoid running `docker compose config` with real secrets loaded, because Docker
Compose may print expanded environment values to the terminal.
