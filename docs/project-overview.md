# Project Overview

## Development flow

Mac / VS Code
→ Codex helps write and review code
→ Git repository tracks all changes
→ Docker runs the bot cleanly
→ DigitalOcean Singapore VPS runs it 24/7
→ Binance API provides data
→ Telegram sends alerts

## Runtime flow

Binance market data
→ Bot reads prices/candles
→ Strategy checks conditions
→ Dry-run simulates trades first
→ Bot logs result
→ Telegram/Web UI shows status
