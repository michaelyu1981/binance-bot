# Binance API Security Research

Research date: 2026-06-28

Scope: documentation and risk analysis only. No Binance account API key is added
by this research. No Telegram, Freqtrade, account access, order endpoints, or
live trading features are enabled.

## Sources

Official Binance sources:

- Binance Spot API general information:
  https://developers.binance.com/docs/binance-spot-api-docs/rest-api/general-api-information
- Binance Spot API request security:
  https://developers.binance.com/docs/binance-spot-api-docs/rest-api/request-security
- Binance Spot API limits:
  https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits
- Binance API key types:
  https://developers.binance.com/docs/binance-spot-api-docs/faqs/api_key_types
- Binance market-data-only URLs:
  https://developers.binance.com/docs/binance-spot-api-docs/faqs/market_data_only
- Binance API key creation FAQ:
  https://www.binance.com/en/support/faq/detail/360002502072
- Binance API FAQ:
  https://www.binance.com/en/support/faq/detail/360004492232
- Binance Spot Testnet and Futures Demo Trading FAQ:
  https://www.binance.com/en/support/faq/detail/ab78f9a1b8824cf0a106b4229c76496d
- Binance WebSocket Streams:
  https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams

Freqtrade sources:

- Freqtrade configuration:
  https://www.freqtrade.io/en/stable/configuration/
- Freqtrade exchange-specific notes:
  https://www.freqtrade.io/en/stable/exchanges/
- Freqtrade stoploss:
  https://www.freqtrade.io/en/stable/stoploss/

General security and operational sources:

- GitHub secret scanning:
  https://docs.github.com/en/code-security/concepts/secret-security/secret-scanning
- OWASP Secrets Management Cheat Sheet:
  https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html

## Official Binance Information

### Public market data

Binance documents a market-data-only REST and WebSocket domain. These endpoints
do not require authentication and serve public market data only. The listed
public REST endpoints include `GET /api/v3/ticker/price`, which is what this
project currently uses.

Project implication:

- Keep Phase 1 on public market-data endpoints only.
- Prefer `data-api.binance.vision` for market-data-only REST calls in a future
  cleanup, because Binance explicitly documents it for public market data.
- Do not add an API key for current public price monitoring.

### API key creation and permissions

Binance API keys can allow external programs to view wallet or transaction
data, make trades, and deposit or withdraw funds depending on permissions. API
key creation requires account security steps such as 2FA, identity verification,
and account activation.

Binance describes these permission/security categories in Spot API docs:

- `NONE`: public market data.
- `TRADE`: trading on the exchange, including placing and canceling orders.
- `USER_DATA`: private account information such as order status and trading
  history.
- `USER_STREAM`: managing user data stream subscriptions.

Binance states that secure endpoints require a valid API key, and most secure
endpoints are signed. Binance also says API keys can be configured for separate
permissions, such as one key for trading and another key for account monitoring.
By default, an API key cannot trade unless trading is enabled in API Management.

Project implication:

- First account key, if ever approved, must be read-only.
- A read-only account monitor must not include `TRADE` permission.
- Any key with `TRADE` permission is a separate future decision and must be
  blocked unless Michael explicitly approves live spot trading.

### Reading, Spot trading, Futures, Margin, and Withdrawal permissions

Binance support states that unrestricted-IP HMAC keys are limited to reading.
It strongly recommends against enabling permissions beyond reading unless
appropriate IP access restrictions are set. It also states that IP restrictions
are mandatory to enable withdrawal permission.

Binance support also notes that Futures permission has separate eligibility
conditions, and that system-generated unrestricted-IP keys can only use reading
permission under current security controls.

Project implication:

- Reading permission is the only acceptable first account API permission.
- Withdrawal permission is forbidden for this project.
- Futures, margin, leverage, auto-borrow, cross-margin, and isolated-margin are
  forbidden by default.
- Any future live key must be spot-only, IP-whitelisted, and minimum-permission.

### IP access restrictions and whitelist

Binance documentation and support both emphasize IP access restrictions for
non-reading permissions. Binance requires IP restrictions for withdrawal
permission and recommends restrictions before enabling permissions beyond
reading.

Project implication:

- Any future live key must be IP-whitelisted to the deployment server only.
- Local development should not use a live trading key.
- If the VPS IP changes, live trading must stay disabled until the API key
  whitelist is reviewed.

### HMAC, RSA, and Ed25519 API keys

Binance supports HMAC, RSA, and Ed25519 keys. Binance recommends Ed25519 as the
best balance of performance and security. Binance describes HMAC as symmetric:
Binance generates a secret key and the same shared secret signs requests.
Binance describes HMAC keys as deprecated and recommends migration to
asymmetric keys such as Ed25519 or RSA.

Project implication:

- If an account API key is ever approved, prefer Ed25519 if the project can
  support it cleanly.
- If using HMAC temporarily, treat the secret as highly sensitive and store it
  only in `.env` or a secure secret manager.
- Never paste any key, secret, private key, or token into docs, README, code,
  tests, logs, screenshots, or Codex prompts.

### API key expiry, masking, and deletion behavior

Binance says API Secret Keys are visible only when created; after that they are
masked. If the secret is lost, create a new API key. Binance also states that
disabled accounts delete active API keys, and older/inactive legacy keys may
have been purged historically.

Project implication:

- If a secret is lost, do not try to recover it. Revoke and recreate.
- If a secret is exposed, immediately revoke the key and create a new one.
- Keep a documented key revocation procedure before adding any account key.

### Request security and signed endpoints

Binance signed endpoints require a `signature`. Signed requests also require a
current `timestamp` and may use `recvWindow`. Binance recommends keeping
`recvWindow` small, with 5000 ms or less recommended, and the maximum is 60000
ms. Binance notes network timing matters and unstable networks can cause
requests to arrive late.

Project implication:

- Current monitor must avoid signed endpoints entirely.
- Any future signed account access must use synchronized system time.
- Do not implement signed order calls until the explicit live spot phrase is
  provided and reviewed.

### REST limits, request weight, 429, and bans

Binance documents request limits through `/api/v3/exchangeInfo`. Binance's API
FAQ lists hard limits including 6000 request weight per minute, 100 orders per
10 seconds, and 200000 orders per 24 hours. The FAQ also says these limits can
change.

Binance REST limits documentation says each route has a request weight, 429
means the client exceeded a rate limit, and clients must back off. Repeated
violations or failure to back off can trigger an automated IP ban with HTTP
418. Binance states IP bans can scale from 2 minutes to 3 days for repeat
offenders, and `Retry-After` tells how long to wait.

Project implication:

- Add backoff before increasing polling frequency.
- Treat 429 as a stop-and-wait condition, not a retry loop.
- Avoid rapid polling across many symbols.
- Never implement future order loops without strict order-rate controls.

### WebSocket limits and connection behavior

Binance WebSocket streams have connection and message limits. A single
connection is valid for 24 hours, then disconnects. The server sends pings and
expects pong responses. Binance limits incoming messages to 5 per second per
connection, allows up to 1024 streams on one connection, and limits connection
attempts by IP. Repeated disconnect violations may lead to bans. Binance also
documents `data-stream.binance.vision` for market-data-only streams and states
that user data streams are not available from that URL.

Project implication:

- WebSocket support should be market-data-only at first.
- Reconnect logic must expect 24-hour disconnects.
- Do not add user data streams until account API-key policy is satisfied.

### Spot Testnet and demo testing

Binance provides Spot Testnet and Futures Demo Trading. Spot Testnet uses test
assets that are not real and cannot transfer in or out. Binance notes Spot
Testnet resets periodically, normally once per month without prior notice.

Project implication:

- Before any real account integration, use Spot Testnet or public dry-run flows
  where possible.
- Futures Demo Trading is not a reason to enable futures in this project.
  Futures remain forbidden by project policy.

## Official Freqtrade Information

### Dry-run mode

Freqtrade recommends starting in dry-run mode. In dry-run, the bot does not
engage real money, and it runs a live simulation without creating exchange
trades. Freqtrade recommends setting `dry_run` to `true` and removing or using
fake exchange key/secret values in dry-run.

Freqtrade says dry-run uses a simulated wallet from `dry_run_wallet`, defaults
to 1000, and only read-only exchange operations are performed if API keys are
provided.

Project implication:

- Freqtrade must stay dry-run until explicitly approved otherwise.
- Dry-run config should use fake or empty API credentials.
- Dry-run is useful, but it does not prove live execution safety.

### Dry-run limitations

Freqtrade dry-run simulates orders and does not post them to the exchange. Its
simulation assumptions include:

- Market orders fill based on orderbook volume at the moment of placement, with
  a maximum slippage assumption.
- Limit orders fill once price reaches the configured level or time out.
- Certain crossing limit orders may be converted to market-order simulation.
- With `stoploss_on_exchange`, dry-run assumes the stop-loss price fills.
- Open orders may remain open across bot restarts under assumptions about
  offline fills.

Project implication:

- Dry-run results can diverge from live execution.
- Restart behavior and stop-loss assumptions need explicit tests before any
  production trading.
- Logs must clearly label dry-run versus live.

### Production/live trading warnings

Freqtrade warns that production mode engages real money, and a wrong strategy
can lose all money. It recommends a different or fresh database when switching
from dry-run to production to avoid dry-run trades affecting real-money state
and statistics.

Project implication:

- Do not share a database between dry-run and live mode.
- Do not switch to production mode as a configuration cleanup.
- Any future production switch must be a dedicated reviewed change.

### Secret storage

Freqtrade documents API keys and secrets as production-only fields that must be
kept secret. It supports environment variables and also recommends a second
private config file for secrets.

Project implication:

- This project policy is stricter: real secrets must go only in `.env` or a
  secure secret manager, not committed config files.
- Logs and debug output must not print environment-derived secret values.

### Exchange/account separation

Freqtrade exposes `available_capital` for assigning available capital when
running multiple bots on one exchange account. This implies multiple-bot account
state must be explicitly managed. Common practice is to separate bot accounts
or subaccounts where possible.

Project implication:

- Do not run multiple live bots against the same account by default.
- Future live work should use a dedicated Spot-only account or subaccount if
  available.

### Stop-loss and strategy assumptions

Freqtrade documents stop-loss modes and warns that too-tight stop-loss settings
on exchange can miss fills, while very wide settings may fail due to exchange
limitations. It also notes changing stop-loss on open trades has limitations
when trailing stop-loss has already adjusted.

Project implication:

- No live trading without explicit stop-loss, max position size, and emergency
  exit policy.
- Stop-loss behavior must be tested under dry-run and, if possible, testnet
  before live Spot.

## Third-Party and Common Operational Risk Patterns

This section is not official Binance guidance. It summarizes common engineering
and operations risks for automated trading systems, supported by general
security references where applicable.

### API key leakage

GitHub documents that committed credentials become targets for unauthorized
access. GitHub secret scanning scans Git history for hardcoded credentials and
recommends rotating affected credentials immediately when exposed.

Project risk:

- A leaked Binance key with trading permission could place real orders.
- A leaked key with withdrawal permission could enable direct asset theft.
- A leaked Telegram token could expose alert channels or control surfaces later.

Mitigation:

- Never commit `.env`.
- Use secret scanning and manual review before every commit involving config.
- Revoke exposed keys immediately.

### Over-permissioned keys and no IP whitelist

Least-privilege is a core secret-management principle. OWASP recommends secrets
be minimum-privilege, revocable, rotated, and never logged.

Project risk:

- A key that can trade, withdraw, use margin, or use futures can turn a software
  bug or credential leak into account loss.
- A non-whitelisted live key can be abused from anywhere if leaked.

Mitigation:

- First account key must be read-only.
- Withdrawal is forbidden forever.
- Future live Spot key must be IP-whitelisted and minimum-permission.

### Bad strategy or bad assumptions

Automated strategies can lose money quickly. They can also fail because the live
market differs from backtest or dry-run assumptions: spread, slippage, partial
fills, order rejection, candle timing, exchange downtime, and liquidity.

Mitigation:

- Require dry-run first.
- Require small position sizing and max daily loss before live Spot.
- Avoid low-liquidity pairs until liquidity and spread checks exist.
- Keep manual review gates before live trading.

### Low-liquidity pairs and manipulation risk

Thin markets are easier to move, have wider spreads, and can produce misleading
signals. Price-change alerts on low-liquidity pairs may trigger repeatedly from
small orderbook movement.

Mitigation:

- Add volume/spread filters before strategy logic.
- Treat alerts as informational, not trading signals.
- Do not trade new symbols just because they are in the monitor watchlist.

### Rate-limit bans and partial API failure

Binance rate limits apply by IP for request weight. 429 responses require
backoff, and repeated violations can cause IP bans. Network failures can cause
incomplete data, stale prices, or unknown order state in future signed flows.

Mitigation:

- Implement backoff before increasing polling.
- Log API errors with timestamps.
- In future live mode, query order status before retrying unknown orders.

### Restart behavior and duplicate actions

Bots can restart after an order, alert, or state transition and repeat the same
action if state is only kept in memory. This is currently low risk because the
project only logs public prices, but it becomes high risk if orders are added.

Mitigation:

- Persist state before future account or order workflows.
- Use idempotency keys or client order IDs before any live orders.
- Include duplicate-alert cooldowns before Telegram.

### Timezone and logging confusion

Incorrect timestamps can make incident review difficult. This project currently
logs Philippine time, UTC+8.

Mitigation:

- Keep timezone explicit in every log line.
- For future signed endpoints, sync system clock and log both local UTC+8 and
  exchange timestamps where useful.

### Decimal precision

Using binary floating-point for prices and quantities can cause rounding errors.
This project already parses prices as `Decimal`.

Mitigation:

- Continue using `Decimal`.
- Before any orders, enforce Binance symbol filters for tick size, step size,
  min notional, and precision.

### Multiple bots or manual trades on one account

Running multiple bots or making manual trades can cause the bot's assumed state
to diverge from real account state.

Mitigation:

- Use separate accounts/subaccounts where possible.
- Do not run multiple live bots on the same account.
- Disable manual trading on the bot account during live tests.

### Dependency and package supply-chain risk

Trading bots often depend on exchange wrappers, HTTP libraries, Docker images,
and system packages. A compromised dependency could leak keys or place
unwanted requests.

Mitigation:

- Keep dependencies minimal.
- Pin versions for runtime components.
- Review new dependencies before adding them.
- Use dependency scanning and avoid untrusted packages.

## Project Conclusions

- Current Phase 1 should remain public-data only.
- No Binance account API key should be added yet.
- The first account key, when explicitly approved, must be read-only.
- Withdrawal permission is forbidden forever.
- Futures, margin, leverage, auto-borrow, cross-margin, and isolated-margin are
  forbidden by default.
- Any future live key must be Spot-only, IP-whitelisted, minimum-permission, and
  gated by Michael's exact phrase: `enable live spot trading.`
