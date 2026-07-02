# DigitalOcean Deployment

This document describes the current CoinPilot deployment on the Hermes
DigitalOcean Droplet and the safe operating commands for the read-only public
market monitor.

## Current Production Snapshot

- Droplet name: Hermes.
- Public IPv4: `68.183.225.86`.
- Private IPv4: `10.15.0.6`.
- App directory: `/opt/coinpilot`.
- Public URL: `https://coinpilot.mindforgecloud.com/login`.
- Nginx public front door: ports `80` and `443`.
- Dashboard internal bind: `127.0.0.1:8765`.
- HTTPS: Let's Encrypt via Certbot.
- Certificate renewal: `certbot.timer`.

Docker services:

- `coinpilot-binance-bot-1`: public price monitor.
- `coinpilot-binance-candles-1`: public candle collector.
- `coinpilot-binance-dashboard-1`: read-only dashboard.

The dashboard must remain password protected on Hermes through `.env`:

```bash
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=replace-with-a-private-password
```

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
- Allow HTTP on port `80` for Let's Encrypt validation and redirect to HTTPS.
- Allow HTTPS on port `443` for the dashboard.
- Do not expose port `8765` publicly.

Outbound:

- Allow HTTPS outbound so the monitor can reach Binance public market endpoints
  and Telegram.

Notes:

- DigitalOcean Cloud Firewall is separate from Droplet firewall software such as
  UFW. Keep rules consistent if both are used.
- The dashboard app must stay bound to `127.0.0.1`. Nginx is the only public
  HTTP/HTTPS entry point.

## Secrets

Allowed on the Droplet `.env`:

```bash
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DASHBOARD_USERNAME=
DASHBOARD_PASSWORD=
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
- [ ] Open HTTP `80` for Let's Encrypt validation and HTTPS redirect.
- [ ] Open HTTPS `443` for the public dashboard.
- [ ] Do not open dashboard port `8765`.

On the Droplet:

- [ ] Update packages.
- [ ] Install Docker Engine and Docker Compose plugin.
- [ ] Create a dedicated app directory, for example `/opt/coinpilot`.
- [ ] Clone or copy this repository.
- [ ] Create `.env` manually with Telegram and dashboard variables only.
- [ ] Confirm `.env` permissions are restrictive.
- [ ] Confirm `DASHBOARD_PASSWORD` is set before exposing Nginx publicly.
- [ ] Build Docker image.
- [ ] Start Docker Compose service.
- [ ] Confirm logs are writing.
- [ ] Run summary after enough data exists.

## Deployment Commands

These commands are the deployment runbook. Review before executing on the
Droplet.

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

For this phase, `.env` should contain Telegram and dashboard variables only.
Leave Binance account API fields empty.

Build and start:

```bash
docker compose build
docker compose up -d
docker compose --profile dashboard up -d binance-dashboard
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
cd /opt/coinpilot
docker compose ps
```

Follow logs:

```bash
cd /opt/coinpilot
docker compose logs -f binance-bot
```

Run summary:

```bash
cd /opt/coinpilot
docker compose run --rm binance-bot python3 -m app.main --summary --summary-hours 24
```

Stop service:

```bash
cd /opt/coinpilot
docker compose down
```

Restart service:

```bash
cd /opt/coinpilot
docker compose up -d
docker compose --profile dashboard up -d binance-dashboard
```

Update code:

```bash
cd /opt/coinpilot
git pull
docker compose build
docker compose up -d
docker compose --profile dashboard up -d binance-dashboard
```

Restart only the dashboard after dashboard code or `.env` auth changes:

```bash
cd /opt/coinpilot
docker compose --profile dashboard up -d --build --no-deps binance-dashboard
```

Restart only the public price monitor:

```bash
cd /opt/coinpilot
docker compose up -d --build --no-deps binance-bot
```

Restart only the public candle collector:

```bash
cd /opt/coinpilot
docker compose up -d --build --no-deps binance-candles
```

## Nginx And HTTPS

Nginx proxies public HTTPS traffic to the private dashboard process:

```text
https://coinpilot.mindforgecloud.com -> http://127.0.0.1:8765
```

Important files on Hermes:

```text
/etc/nginx/sites-available/coinpilot
/etc/nginx/sites-enabled/coinpilot
/etc/letsencrypt/live/coinpilot.mindforgecloud.com/fullchain.pem
/etc/letsencrypt/live/coinpilot.mindforgecloud.com/privkey.pem
```

Check Nginx:

```bash
nginx -t
systemctl is-active nginx
systemctl reload nginx
```

Check HTTPS from any machine:

```bash
curl -I https://coinpilot.mindforgecloud.com/login
curl -I http://coinpilot.mindforgecloud.com
```

Expected:

- HTTPS login returns `200`.
- HTTP returns `301` redirecting to HTTPS.

Check Certbot renewal timer:

```bash
systemctl list-timers certbot.timer --no-pager
certbot renew --dry-run
```

## Monitoring Checks

After deployment:

- [ ] Confirm one public price cycle per configured interval.
- [ ] Confirm one public candle collection cycle per configured interval.
- [ ] Confirm `logs/market_prices-YYYY-MM-DD.log` persists after container restart.
- [ ] Confirm `data/market_data.sqlite3` persists after container restart.
- [ ] Confirm summary works.
- [ ] Confirm dashboard login is required.
- [ ] Confirm `https://coinpilot.mindforgecloud.com/login` works.
- [ ] Confirm `http://coinpilot.mindforgecloud.com` redirects to HTTPS.
- [ ] Confirm Telegram alerts only fire on threshold alerts.
- [ ] Confirm no secrets appear in Docker logs.
- [ ] Confirm no Binance account API variables are populated.
- [ ] Confirm no order endpoints exist in runtime logs.

## Emergency Stop

If anything behaves unexpectedly:

```bash
cd /opt/coinpilot
docker compose down
```

Then inspect:

```bash
cd /opt/coinpilot
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
