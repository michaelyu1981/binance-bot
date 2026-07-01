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

Default collection interval is 300 seconds, or 5 minutes. Price monitoring and
candle collection use the same shared default interval.

Run continuously with a 5-second interval for testing:

```bash
python3 -m app.main --watch --interval 5
```

Run continuously with a 5-second interval and 1% local price-change alerts:

```bash
python3 -m app.main --watch --interval 5 --alert-threshold 1
```

Build a local summary from the runtime log without fetching Binance data:

```bash
python3 -m app.main --summary
```

Use a specific summary lookback period:

```bash
python3 -m app.main --summary --summary-hours 24
python3 -m app.main --summary --summary-hours 1
```

Send the summary to Telegram when Telegram env vars are configured:

```bash
python3 -m app.main --summary --send-telegram
```

Run a local read-only dashboard:

```bash
python3 -m app.main --dashboard
```

Then open:

```text
http://127.0.0.1:8765
```

Every run prints timestamped public prices in Philippine time, UTC+8, and
appends the same lines to a daily log file:

```text
logs/market_prices-YYYY-MM-DD.log
```

The log contains only timestamps, symbols, and public market prices. It must not
contain secrets, API keys, account data, order data, Telegram tokens, or trading
credentials.

In watch mode, the monitor compares each symbol's current public price to its
previous cycle price. If the change is at least the configured threshold, it
prints an `ALERT` line and appends the same line to the current daily log file.
The default alert threshold is `1.0%`.

When watch mode starts, it prints the watchlist, interval, alert threshold,
Telegram enabled/disabled status, and a public-data-only safety message. It
does not print secrets.

## Summary reports

Summary mode reads only:

```text
logs/market_prices-YYYY-MM-DD.log
```

It does not call Binance unless a future change explicitly adds that behavior.
The summary includes:

- report timestamp
- latest price per symbol
- first and last logged price per symbol for the summary period
- percent change per symbol
- biggest mover
- total number of `ALERT` lines
- last alert line, if any

The default summary period is the last 24 hours. Missing, empty, or unparseable
logs are handled with a local message instead of an exception traceback.
Legacy `logs/market_prices.log` is still read as a fallback if it exists.

## Local dashboard

Dashboard mode reads local market price logs and renders a simple browser view.
It does not fetch Binance data by itself, does not use Binance account access,
and does not include trading controls.

```bash
python3 -m app.main --dashboard
```

Optional bind settings:

```bash
python3 -m app.main --dashboard --dashboard-host 127.0.0.1 --dashboard-port 8765
```

Keep the dashboard bound to `127.0.0.1` unless an authentication and firewall
plan is added later.

The Recent Log Lines section groups each watch cycle by timestamp and labels
the timestamp as Philippine time. The log view is scrollable for easier review.

The dashboard also shows read-only operational visibility:

- monitor health based on the latest log timestamp
- separate process health for the price monitor and candle collector
- last update age
- first and latest log time in the selected summary window
- log coverage duration from first price line to latest price line
- price cycle count based on grouped timestamps
- alert count for the selected summary window
- alert count for the current Philippine date
- recent alert history
- 1h, 4h, and 24h price movement from local logs

These values are calculated from local log files only. They are not buy/sell
signals and do not enable trading.

Log coverage is not Docker container uptime. It shows how long the dashboard can
see continuous market data in the selected log window.

Process health is reported through local heartbeat files:

```text
data/health/price_monitor.json
data/health/candle_collector.json
```

Each long-running process updates its own heartbeat after a successful cycle or
after an error. The dashboard shows `OK`, `STALE`, `ERROR`, or `DOWN` for each
process.

## Public candle collection

The candle collector stores public Binance kline/candlestick data in SQLite for
later read-only technical analysis. It uses only unauthenticated public market
data:

- no Binance API key
- no Binance account access
- no buy/sell
- no order endpoints

Default symbols:

- BTCUSDT
- ETHUSDT
- BNBUSDT
- ZECUSDT
- XTZUSDT

Default intervals:

- 1m
- 5m
- 15m
- 1h
- 4h
- 1d

Run one candle collection cycle:

```bash
python3 -m app.main --collect-candles
```

Run candle collection continuously using the shared default interval:

```bash
python3 -m app.main --collect-candles --watch
```

Run candle collection continuously with a test interval:

```bash
python3 -m app.main --collect-candles --watch --interval 300
```

Fetch a smaller batch for testing:

```bash
python3 -m app.main --collect-candles --candle-limit 10
```

The SQLite database is stored at:

```text
data/market_data.sqlite3
```

Default candle retention is 90 days. After each collection cycle, rows older
than the retention window are deleted:

```bash
python3 -m app.main --collect-candles --retention-days 90
```

For the 5 default symbols and 6 default intervals, 90 days is roughly 835,000
candle rows. SQLite can handle this size. A practical size estimate is roughly
250 MB to 750 MB depending on indexes and stored precision.

SQLite normally reuses deleted space internally instead of immediately shrinking
the file on disk. Run manual maintenance when needed:

```bash
python3 -m app.main --db-maintenance
```

To delete old rows and compact the SQLite file:

```bash
python3 -m app.main --db-maintenance --vacuum
```

Do not run `VACUUM` every collection cycle. Use it occasionally, such as weekly
or monthly, because it can temporarily lock the database and require extra disk
space while compacting.

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

The Docker setup runs the same read-only public market monitor. The default
Compose services run a production-safe price watch loop and candle collection
loop:

```bash
python3 -m app.main --watch --interval 300 --alert-threshold 0.5
python3 -m app.main --collect-candles --watch --interval 300 --candle-limit 100 --retention-days 90
```

It does not add Binance account access, buy/sell logic, order endpoints,
Freqtrade, or DigitalOcean deployment.

Build the local image:

```bash
docker compose build
```

Run once:

```bash
docker compose run --rm binance-bot python3 -m app.main
```

Run a summary:

```bash
docker compose run --rm binance-bot python3 -m app.main --summary
```

Run one public candle collection cycle:

```bash
docker compose run --rm binance-bot python3 -m app.main --collect-candles
```

Run candle DB maintenance:

```bash
docker compose run --rm binance-bot python3 -m app.main --db-maintenance
```

Run a summary and send it to Telegram if configured:

```bash
docker compose run --rm binance-bot python3 -m app.main --summary --send-telegram
```

Start the production-style watch service:

```bash
docker compose up -d
```

This starts:

- `binance-bot` for public price monitoring
- `binance-candles` for public candle collection

Start the private dashboard service locally:

```bash
docker compose --profile dashboard up -d binance-dashboard
```

The Docker dashboard binds only to `127.0.0.1:8765` on the host. It reads the
mounted `logs/` directory as read-only and does not fetch Binance data by
itself.

Follow logs:

```bash
docker compose logs -f binance-bot
docker compose logs -f binance-candles
```

Stop the service:

```bash
docker compose down
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
daily market price logs persist outside the container.

Avoid running `docker compose config` with real secrets loaded, because Docker
Compose may print expanded environment values to the terminal.

## Private Hermes dashboard

On Hermes, keep the dashboard private. Start it on the server with:

```bash
cd /opt/coinpilot
docker compose --profile dashboard up -d binance-dashboard
```

From the Mac, open an SSH tunnel:

```bash
ssh -i ~/.ssh/coinpilot_codex_ed25519 -L 8765:127.0.0.1:8765 root@68.183.225.86
```

Then open this on the Mac:

```text
http://127.0.0.1:8765
```

The browser is local, but the dashboard data comes from Hermes logs. Do not
bind the dashboard to `0.0.0.0` on the host or expose it publicly until we add
authentication and a firewall plan.

## Recommended one-day local Docker test

Build and start the watch service:

```bash
docker compose build
docker compose up -d
```

Watch runtime output:

```bash
docker compose logs -f binance-bot
```

After it has collected data, run a summary:

```bash
docker compose run --rm binance-bot python3 -m app.main --summary
```

Stop the service:

```bash
docker compose down
```

## Safety notes

This project remains public-data-only in the current phase:

- No Binance API key
- No Binance account access
- No buy/sell
- No order endpoints
- No withdrawals
- No futures, margin, or leverage
- No Freqtrade runtime yet
- No DigitalOcean deployment yet
- Telegram token and chat ID are read only from environment variables
- `.env` is ignored by Git and must not be committed
