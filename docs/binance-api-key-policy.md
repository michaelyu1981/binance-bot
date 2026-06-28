# Binance API Key Policy

This policy governs Binance API keys, secrets, permissions, and live trading
gates for this project.

## Default Mode

Default mode is public-data only.

The current monitor may use Binance public market data endpoints that do not
require authentication. It must not use account endpoints, signed endpoints, or
order endpoints.

## API Key Approval Gate

Adding any Binance account API key is forbidden until Michael explicitly
approves it.

Approval to create or store an account key is not approval to trade.

## Live Trading Gate

Any change that enables trading must require Michael's exact phrase:

```text
enable live spot trading.
```

Without that exact phrase, live trading must remain disabled.

## Forbidden Forever

Withdrawal permission is forbidden forever.

The project must never enable withdrawals from code, config, docs, examples,
tests, scripts, or deployment instructions.

## Forbidden By Default

The following are forbidden by default:

- Futures
- Margin
- Leverage
- Auto-borrow
- Cross-margin
- Isolated-margin

These must not be enabled unless a future policy explicitly changes. Current
project policy is to avoid them.

## First API Key

The first Binance account API key must be read-only.

Requirements:

- Reading permission only.
- No Spot trading permission.
- No withdrawal permission.
- No Futures permission.
- No Margin permission.
- No leverage or borrow capability.
- No order endpoints in project code.

## Future Live Spot Key

Any future live key must be separate from the read-only key.

Requirements:

- Spot-only.
- IP-whitelisted.
- Minimum-permission.
- Tiny-size only.
- No withdrawal permission.
- No futures permission.
- No margin permission.
- No leverage.
- No auto-borrow.
- No cross-margin.
- No isolated-margin.
- Reviewed before use.
- Enabled only after Michael says: `enable live spot trading.`

## Secret Storage

Real secrets must only be stored in:

- `.env`, or
- a secure secret manager.

`.env` must never be committed to Git.

Secrets include:

- Binance API keys
- Binance API secrets
- Binance private keys
- Telegram bot tokens
- Telegram chat IDs if sensitive
- Passwords
- VPS credentials
- Database credentials
- Any other bearer token or private credential

## Places Secrets Must Never Appear

Secrets must never appear in:

- README
- docs
- source code
- tests
- examples
- sample config committed to Git
- logs
- screenshots
- terminal transcripts
- Codex prompts
- Git commit messages
- pull request descriptions
- issue comments

## Logging Policy

Logs may include:

- timestamps
- public market symbols
- public prices
- public API errors
- non-secret operational status

Logs must not include:

- API keys
- API secrets
- private keys
- signed request payloads containing sensitive values
- account balances unless the read-only account phase is explicitly approved
- order details unless live Spot mode is explicitly approved
- Telegram tokens
- VPS credentials

## Exposure Response

If a secret is accidentally exposed:

1. Stop using the key immediately.
2. Revoke or delete the API key in Binance API Management.
3. Create a new key only if still needed.
4. Update `.env` or the secure secret manager.
5. Review logs, docs, prompts, screenshots, and Git history for additional
   exposure.
6. Do not rely on deleting the text from Git as the primary fix. Revocation is
   the priority.
7. Document the incident and prevention change.

## Review Requirements

Before any account API key is added:

- Review this policy.
- Review `docs/binance-bot-risk-checklist.md`.
- Confirm `.env` is ignored by Git.
- Confirm no real secrets are in the repository.
- Confirm the key is read-only.

Before any live Spot work:

- Michael must say `enable live spot trading.`
- Review order-related code paths.
- Review max position size, max daily loss, stop-loss, and emergency stop.
- Confirm Spot-only and IP whitelist.
- Confirm withdrawal, futures, margin, leverage, and borrow features are
  disabled.

## Current Project Status

Current status: public-data only.

No Binance account API key is present.

No live trading is enabled.

No account access is enabled.

No buy/sell or order endpoints are enabled.
