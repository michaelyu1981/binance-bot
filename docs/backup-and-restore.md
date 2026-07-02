# Backup And Restore

CoinPilot currently stores runtime state on Hermes in plain files and SQLite.
Backups must never be committed to Git.

## Important Runtime Paths

On Hermes:

```text
/opt/coinpilot/.env
/opt/coinpilot/logs/
/opt/coinpilot/data/market_data.sqlite3
```

What they contain:

- `.env`: Telegram token, Telegram chat ID, dashboard username/password.
- `logs/`: daily public price logs and local alert lines.
- `data/market_data.sqlite3`: public candle data and local indicator source data.

## Backup Policy

- Back up `.env` separately and privately.
- Back up `logs/` and `data/` together.
- Do not store real secrets in Git.
- Do not paste backup contents into Codex.
- Keep at least one off-server backup before major deployment changes.

## Manual Backup On Hermes

Create a timestamped backup directory:

```bash
mkdir -p /root/coinpilot-backups
cd /opt/coinpilot
tar -czf /root/coinpilot-backups/coinpilot-runtime-$(date +%F-%H%M%S).tar.gz logs data .env
chmod 600 /root/coinpilot-backups/coinpilot-runtime-*.tar.gz
```

Copy a backup to your Mac:

```bash
scp -i ~/.ssh/coinpilot_codex_ed25519 root@68.183.225.86:/root/coinpilot-backups/coinpilot-runtime-YYYY-MM-DD-HHMMSS.tar.gz .
```

Replace the filename with the actual backup filename.

## Restore On Hermes

Stop services first:

```bash
cd /opt/coinpilot
docker compose down
```

Restore the backup:

```bash
cd /opt/coinpilot
tar -xzf /root/coinpilot-backups/coinpilot-runtime-YYYY-MM-DD-HHMMSS.tar.gz
chmod 600 .env
```

Start services:

```bash
docker compose up -d
docker compose --profile dashboard up -d binance-dashboard
docker compose ps
```

## SQLite Maintenance

The candle database uses retention cleanup. SQLite files do not always shrink
immediately after old rows are deleted. If the file becomes unnecessarily large,
run `VACUUM` during a maintenance window after stopping the candle collector:

```bash
cd /opt/coinpilot
docker compose stop binance-candles
sqlite3 data/market_data.sqlite3 'VACUUM;'
docker compose up -d binance-candles
```

Only run this if `sqlite3` is installed on the server and the database is not
being written at the same time.

## Restore Safety Checks

After restore:

- [ ] `docker compose ps` shows all expected services up.
- [ ] Dashboard login works.
- [ ] `logs/` contains recent daily log files.
- [ ] `data/market_data.sqlite3` exists.
- [ ] No Binance API key is present unless a future approved phase requires it.
- [ ] No trading service is running.
