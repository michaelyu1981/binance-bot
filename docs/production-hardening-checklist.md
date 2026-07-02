# Production Hardening Checklist

This checklist applies to the Hermes deployment of CoinPilot. The production
dashboard is public over HTTPS, so authentication and minimum exposure matter.

## Current Public Surface

- Public URL: `https://coinpilot.mindforgecloud.com/login`.
- Public ports: `80` and `443` through Nginx.
- SSH: port `22`.
- Private dashboard app: `127.0.0.1:8765`.

Port `8765` must not be exposed publicly.

## Required Safety Checks

- [ ] `DASHBOARD_PASSWORD` is set in `/opt/coinpilot/.env`.
- [ ] `/opt/coinpilot/.env` is not committed to Git.
- [ ] `.env` permissions are restrictive.
- [ ] Dashboard binds to `127.0.0.1`, not `0.0.0.0`.
- [ ] Docker Compose maps dashboard as `127.0.0.1:8765:8765`.
- [ ] Nginx proxies to `http://127.0.0.1:8765`.
- [ ] HTTP redirects to HTTPS.
- [ ] Certbot renewal timer is active.
- [ ] No Binance API key is present.
- [ ] No Binance account access exists.
- [ ] No buy/sell or order endpoints are enabled.
- [ ] No withdrawals, futures, margin, leverage, auto-borrow, cross-margin, or isolated-margin.

## Server Checks

Run on Hermes:

```bash
cd /opt/coinpilot
docker compose ps
nginx -t
systemctl is-active nginx
systemctl list-timers certbot.timer --no-pager
```

Check that the dashboard is local-only:

```bash
ss -ltnp | grep 8765
```

Expected bind:

```text
127.0.0.1:8765
```

## Firewall Recommendations

DigitalOcean Cloud Firewall should allow:

- SSH `22` from Michael's IP if practical.
- HTTP `80` from all IPv4/IPv6 for Let's Encrypt and redirect.
- HTTPS `443` from all IPv4/IPv6.

DigitalOcean Cloud Firewall should not allow:

- `8765`.
- Database ports.
- Docker internal ports.

If UFW is enabled later, keep it consistent with the DigitalOcean firewall.

## Secret Handling

Allowed in `/opt/coinpilot/.env`:

```bash
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DASHBOARD_USERNAME=
DASHBOARD_PASSWORD=
```

Forbidden in Git, README, docs, screenshots, logs, tests, and Codex prompts:

- Telegram bot token.
- Dashboard password.
- Binance API key.
- Binance API secret.
- Server private keys.

If any secret is exposed:

1. Revoke or rotate the secret immediately.
2. Replace it in `/opt/coinpilot/.env`.
3. Restart only the affected service.
4. Review logs and shell history for exposure.

## Operational Rule

Any future feature that touches Binance account APIs must remain read-only until
Michael explicitly approves the next phase. Live spot trading remains forbidden
unless Michael says exactly:

```text
enable live spot trading.
```
