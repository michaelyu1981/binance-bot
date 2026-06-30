# DigitalOcean Deployment Plan

This plan deploys the current CoinPilot public market monitor to a separate
DigitalOcean Droplet. It is documentation/setup only until Michael explicitly
chooses to deploy.

## Current Scope

Allowed:

- Public Binance market data only.
- Docker Compose watch service.
- Local log persistence on the Droplet.
- Optional Telegram alert delivery from environment variables.

Forbidden:

- Binance account API key.
- Binance account access.
- Buy/sell logic.
- Order endpoints.
- Withdrawals.
- Futures, margin, leverage, auto-borrow, cross-margin, isolated-margin.
- Freqtrade runtime.

## Official References

- DigitalOcean SSH keys:
  https://docs.digitalocean.com/products/droplets/how-to/add-ssh-keys/
- DigitalOcean Cloud Firewalls:
  https://docs.digitalocean.com/products/networking/firewalls/how-to/configure-rules/
- Docker Engine on Ubuntu:
  https://docs.docker.com/engine/install/ubuntu/

Key official guidance:

- DigitalOcean recommends SSH key pairs over password logins.
- DigitalOcean Cloud Firewalls block inbound traffic unless explicitly allowed.
- Docker's official Ubuntu docs describe installing Docker Engine and the
  Compose plugin from Docker's apt repository.

## Recommended Droplet

Start small because this monitor is lightweight:

- Region: Singapore, if available.
- Image: Ubuntu LTS.
- Size: smallest practical basic Droplet.
- Authentication: SSH key, not password.
- Project name/tag: `coinpilot-public-monitor`.

This Droplet is for public monitoring only. Do not reuse it for unrelated apps.

## Network and Firewall

Inbound:

- Allow SSH on port `22` only from Michael's current public IP if possible.
- No public HTTP port is needed yet.
- No dashboard port is needed yet.

Outbound:

- Allow HTTPS outbound so the monitor can reach Binance public market endpoints
  and Telegram.

Notes:

- DigitalOcean Cloud Firewall is separate from Droplet firewall software such as
  UFW. Keep rules consistent if both are used.
- When a future dashboard exists, expose it only after authentication and
  firewall rules are designed.

## Secrets

Allowed on the Droplet `.env`:

```bash
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Forbidden on the Droplet for this phase:

```bash
BINANCE_API_KEY=
BINANCE_API_SECRET=
```

The current phase must not use a Binance account API key.

Rules:

- Do not commit `.env`.
- Do not print `.env`.
- Do not paste tokens into Codex prompts.
- Do not run `docker compose config` with real secrets loaded because Compose
  may print expanded environment values.

## Initial Server Setup Checklist

On DigitalOcean:

- [ ] Create a new dedicated Droplet.
- [ ] Use SSH key authentication.
- [ ] Add a Cloud Firewall.
- [ ] Restrict inbound SSH as tightly as possible.
- [ ] Do not open HTTP/HTTPS inbound yet.

On the Droplet:

- [ ] Update packages.
- [ ] Install Docker Engine and Docker Compose plugin.
- [ ] Create a dedicated app directory, for example `/opt/coinpilot`.
- [ ] Clone or copy this repository.
- [ ] Create `.env` manually with Telegram variables only.
- [ ] Confirm `.env` permissions are restrictive.
- [ ] Build Docker image.
- [ ] Start Docker Compose service.
- [ ] Confirm logs are writing.
- [ ] Run summary after enough data exists.

## Deployment Commands

These commands are a draft runbook. Review before executing on the Droplet.

```bash
sudo apt update
sudo apt upgrade -y
```

Install Docker using the official Docker Ubuntu instructions:

```bash
# Follow the official Docker Engine on Ubuntu guide:
# https://docs.docker.com/engine/install/ubuntu/
```

Create an app directory:

```bash
sudo mkdir -p /opt/coinpilot
sudo chown "$USER":"$USER" /opt/coinpilot
cd /opt/coinpilot
```

Clone the repository:

```bash
git clone <repo-url> .
```

Create `.env` manually:

```bash
cp .env.example .env
nano .env
chmod 600 .env
```

For this phase, `.env` should contain Telegram variables only. Leave Binance
account API fields empty.

Build and start:

```bash
docker compose build
docker compose up -d
```

Inspect:

```bash
docker compose ps
docker compose logs --tail=80 binance-bot
python3 -m app.main --summary --summary-hours 3
```

If Python is not installed on the Droplet outside Docker, run summary through
Docker:

```bash
docker compose run --rm binance-bot python3 -m app.main --summary --summary-hours 3
```

## Runtime Operations

Check service:

```bash
docker compose ps
```

Follow logs:

```bash
docker compose logs -f binance-bot
```

Run summary:

```bash
docker compose run --rm binance-bot python3 -m app.main --summary --summary-hours 24
```

Stop service:

```bash
docker compose down
```

Restart service:

```bash
docker compose up -d
```

Update code:

```bash
git pull
docker compose build
docker compose up -d
```

## Monitoring Checks

After deployment:

- [ ] Confirm one log cycle per minute.
- [ ] Confirm `logs/market_prices-YYYY-MM-DD.log` persists after container restart.
- [ ] Confirm summary works.
- [ ] Confirm Telegram alerts only fire on threshold alerts.
- [ ] Confirm no secrets appear in Docker logs.
- [ ] Confirm no Binance account API variables are populated.
- [ ] Confirm no order endpoints exist in runtime logs.

## Emergency Stop

If anything behaves unexpectedly:

```bash
docker compose down
```

Then inspect:

```bash
docker compose logs --tail=200 binance-bot
tail -n 200 logs/market_prices-$(date +%F).log
```

If any secret appears in logs or terminal output:

1. Revoke the exposed token/key.
2. Replace it in `.env`.
3. Review what printed it.
4. Do not restart until fixed.

## Before Moving Beyond Public Monitoring

Do not add Binance read-only account API on the VPS until:

- This public monitor runs cleanly on DigitalOcean.
- Logs and summaries are reviewed.
- Telegram behavior is acceptable.
- `docs/binance-api-key-policy.md` is reviewed again.
- Michael explicitly approves adding a read-only Binance account API key.

Live Spot remains blocked unless Michael says exactly:

```text
enable live spot trading.
```
