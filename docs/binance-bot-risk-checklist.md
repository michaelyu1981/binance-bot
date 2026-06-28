# Binance Bot Risk Checklist

Use this checklist before adding any Binance account API key, dry-run trading
framework, Telegram alerting, VPS deployment, or live Spot feature.

## Before API Key Creation

- [ ] Confirm the current need cannot be met with public market data.
- [ ] Confirm Michael explicitly approved creating a Binance account API key.
- [ ] Confirm the first account key will be read-only.
- [ ] Confirm the Binance account has 2FA enabled.
- [ ] Confirm no one will paste keys or secrets into Codex prompts.
- [ ] Confirm no screenshots will expose keys, secrets, balances, or account
      identifiers.
- [ ] Confirm `.env` is listed in `.gitignore`.
- [ ] Confirm no real secrets exist in Git history.

## API Key Permissions

- [ ] Enable Reading only for the first account API key.
- [ ] Do not enable Spot trading permission.
- [ ] Do not enable Margin.
- [ ] Do not enable Futures.
- [ ] Do not enable withdrawals.
- [ ] Do not enable leverage.
- [ ] Do not enable auto-borrow.
- [ ] Do not enable cross-margin.
- [ ] Do not enable isolated-margin.
- [ ] If a future live Spot key is approved, create a separate key from the
      read-only key.
- [ ] If a future live Spot key is approved, require IP whitelist before use.
- [ ] Use minimum permissions only.

## Secret Storage

- [ ] Store real secrets only in `.env` or a secure secret manager.
- [ ] Do not store secrets in README, docs, source code, tests, logs, examples,
      screenshots, or prompts.
- [ ] Do not store secrets in committed Freqtrade config files.
- [ ] Do not print secrets during startup.
- [ ] Do not log environment variables.
- [ ] Do not include secrets in exception messages.
- [ ] If a secret is exposed, revoke the key immediately and create a new one.

## Local Development Safety

- [ ] Default local mode is public-data only or dry-run.
- [ ] Do not run scripts that place real orders.
- [ ] Do not use a live trading key on local development unless explicitly
      approved for a narrow test.
- [ ] Use fake or empty credentials for dry-run.
- [ ] Keep logs local and ignored by Git.
- [ ] Keep Philippine time, UTC+8, explicit in human-readable logs.
- [ ] Use `Decimal` for prices, balances, quantities, and percentage changes.

## Git Safety

- [ ] Run `git status` before staging.
- [ ] Review `git diff` before staging.
- [ ] Review staged diff before commit.
- [ ] Never commit `.env`.
- [ ] Never commit runtime logs.
- [ ] Never commit API keys, API secrets, Telegram tokens, passwords, or VPS
      credentials.
- [ ] If a secret is committed, revoke it immediately before any cleanup.
- [ ] Do not force push.
- [ ] Do not rewrite Git history unless Michael explicitly requests and the
      secret has already been revoked.

## VPS Deployment Safety

- [ ] Use a dedicated non-root deployment user.
- [ ] Restrict SSH access.
- [ ] Keep OS packages updated.
- [ ] Store secrets outside Git.
- [ ] Restrict file permissions on `.env`.
- [ ] Configure process restart behavior intentionally.
- [ ] Configure log rotation.
- [ ] Monitor disk usage.
- [ ] If using a future live key, whitelist only the VPS public IP.
- [ ] If the VPS IP changes, disable live trading until Binance API whitelist is
      reviewed.

## Dry-Run Requirements

- [ ] Keep `dry_run` enabled.
- [ ] Use fake or empty exchange credentials unless read-only access is
      explicitly needed.
- [ ] Use a separate dry-run database.
- [ ] Label logs as dry-run.
- [ ] Document dry-run wallet amount.
- [ ] Test restart behavior.
- [ ] Test stop-loss assumptions.
- [ ] Test rate-limit behavior.
- [ ] Confirm dry-run orders are not posted to Binance.
- [ ] Do not treat dry-run profit as proof of live profitability.

## Before Enabling Spot Trading

- [ ] Michael must explicitly say: `enable live spot trading.`
- [ ] Use Spot only.
- [ ] Create a separate live Spot key.
- [ ] Enable IP whitelist.
- [ ] Confirm withdrawal permission is disabled.
- [ ] Confirm futures permission is disabled.
- [ ] Confirm margin permission is disabled.
- [ ] Confirm leverage and borrow features are disabled.
- [ ] Confirm position size is tiny.
- [ ] Define max open trades.
- [ ] Define max daily loss.
- [ ] Define stop-loss behavior.
- [ ] Define emergency stop command.
- [ ] Define order retry policy.
- [ ] Define duplicate-order prevention.
- [ ] Test on Spot Testnet where possible.
- [ ] Use a fresh live database, separate from dry-run.
- [ ] Review all code paths that call order endpoints.

## Emergency Stop Procedure

- [ ] Stop the local process or VPS service.
- [ ] Confirm no process restarted automatically.
- [ ] If live mode exists, disable the Binance API key.
- [ ] If live mode exists, inspect open orders manually in Binance.
- [ ] If live mode exists, cancel open orders manually if needed.
- [ ] Save logs for incident review.
- [ ] Do not restart until the root cause is documented.

## Key Revocation Procedure

- [ ] Log in to Binance directly.
- [ ] Open API Management.
- [ ] Disable or delete the suspected key.
- [ ] Confirm the bot can no longer authenticate with the old key.
- [ ] Remove the old key from `.env` or secret manager.
- [ ] Create a replacement key only if still needed.
- [ ] Update `.env` or secret manager with the replacement.
- [ ] Restart only after reviewing permissions and IP whitelist.
- [ ] Document what was exposed and when.

## Monitoring Requirements

- [ ] Log every run with timestamp and symbol data.
- [ ] Log API errors and rate-limit responses.
- [ ] Alert on repeated public API failures.
- [ ] Alert on log file write failures.
- [ ] In future account mode, alert on authentication failures.
- [ ] In future live mode, alert on every order create, cancel, fill, reject,
      and unknown status.
- [ ] In future live mode, alert on balance changes outside bot actions.
- [ ] In future live mode, alert on manual trades or unexpected open orders.
- [ ] Track process uptime and restart count.
- [ ] Track disk usage for logs and databases.
