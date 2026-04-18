# CLAUDE.md

This file gives Claude Code context about this project. Read this first before making changes.

---

## Project: OI Divergence Auto-Trading Bot

Full-stack automated trading bot for Binance USDT-M Futures. Detects Open Interest divergence (OI pumps while price stays flat = smart money accumulating) and executes trades with strict risk management.

**Current status:** Skeleton production-ready. NOT yet validated with live capital. Requires backtesting + testnet forward-test before going live.

---

## Architecture

```
OIScanner ──┐
            ├─→ SignalAggregator ──→ RiskManager ──→ Executor ──→ Binance API
Sentiment ──┘          ↓                                ↓
                   TradeSignal                     PositionTracker
                       ↓                                ↓
                  TelegramBot ←──────── NOTIFICATIONS ──┤
                       ↓                                ↓
                  FastAPI + WebSocket ──→ React Dashboard
```

**Tech stack:**
- Backend: Python 3.11+, FastAPI, asyncio, httpx, pydantic
- Dashboard: React 18, Vite (single-file App.jsx)
- Deployment: Docker Compose
- Data: in-memory (TODO: add SQLite/Postgres persistence)

---

## Directory map

```
oi_bot/
├── backend/
│   ├── config/
│   │   ├── settings.py        # ⭐ ALL tunable params — edit here, never hardcode elsewhere
│   │   └── .env.example
│   ├── modules/
│   │   ├── schemas.py         # Pydantic models — TradeSignal, Position, etc.
│   │   ├── binance_client.py  # Binance API wrapper (testnet/mainnet switch)
│   │   ├── oi_scanner.py      # ⭐ Core strategy: OI divergence detection
│   │   ├── sentiment_scraper.py  # Binance Square + CoinGecko + gainers
│   │   ├── nlp_sentiment.py   # Rule-based NLP (EN/CN/VN)
│   │   ├── price_streamer.py  # WebSocket realtime prices
│   │   ├── signal_aggregator.py  # Combines divergence + sentiment → TradeSignal
│   │   ├── risk_manager.py    # ⭐ 11 checks + kill switches
│   │   └── telegram_bot.py    # Mobile alerts + /commands
│   ├── execution/
│   │   ├── executor.py        # Places bracket orders (entry + SL + TP)
│   │   └── position_tracker.py  # Monitors + trailing stop
│   ├── backtest/
│   │   └── backtester.py      # Historical simulation + metrics
│   ├── main.py                # Orchestrator + FastAPI server
│   ├── download_data.py       # CLI: download historical data
│   └── run_backtest.py        # CLI: run backtest
└── dashboard/
    └── src/App.jsx            # Single-file React dashboard
```

---

## Core strategy logic

**Entry rule (OI Divergence):**
1. OI change ≥ 15% in N-minute window (default N=60)
2. Price change ≤ 5% in same window
3. Divergence ratio (|ΔOI| / |ΔPrice|) ≥ 3
4. Direction determined by: funding rate + taker buy/sell ratio + price micro-bias
5. Sentiment confirmation (optional boost, not required)

**Exit rule:**
- Fixed SL at -3%, TP at +6% (R:R = 1:2)
- Trailing stop activates after +3% profit, trails at 1.5% behind mark price

**Not yet implemented (TODO):**
- Regime filter (halt during choppy markets)
- Position sizing via Kelly criterion
- NLP sentiment veto (currently only numeric scoring)
- Correlation-based portfolio risk

---

## Key files to read first

When debugging or modifying, read these in order:

1. `backend/config/settings.py` — understand what's tunable
2. `backend/modules/schemas.py` — understand data flow
3. `backend/modules/oi_scanner.py` — core strategy
4. `backend/main.py` — see how everything connects

---

## Tunable parameters that matter most

Located in `backend/config/settings.py`:

| Parameter | Default | Effect |
|-----------|---------|--------|
| `OIScannerConfig.min_oi_change_pct` | 15.0 | Higher → fewer but stronger signals |
| `OIScannerConfig.min_divergence_ratio` | 3.0 | Higher → only extreme smart money moves |
| `OIScannerConfig.funding_rate_threshold` | 0.0005 | Direction resolver sensitivity |
| `RiskConfig.max_leverage` | 5 | Never raise above 10, liquidation risk |
| `RiskConfig.default_stop_loss_pct` | 3.0 | Below 2% = noise stop-outs |
| `RiskConfig.default_take_profit_pct` | 6.0 | Must maintain R:R ≥ 1.5 |
| `RiskConfig.max_daily_loss_pct` | 5.0 | Auto kill switch trigger |
| `ExecutorConfig.dry_run` | true | KEEP TRUE until fully validated |

---

## Development workflow

### Running locally (Docker)

```bash
cp backend/config/.env.example backend/config/.env
# Edit .env with testnet API keys
docker compose up
# Backend at localhost:8000, Dashboard at localhost:5173
```

### Running locally (manual)

```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python main.py

# Dashboard (separate terminal)
cd dashboard
npm install && npm run dev
```

### Backtesting

```bash
cd backend
python download_data.py BTCUSDT ETHUSDT SOLUSDT --months 6 --interval 15m
python run_backtest.py BTCUSDT ETHUSDT SOLUSDT --start 2024-06-01
```

---

## Code style and conventions

- **Async everywhere** — all I/O uses `async/await`. Never use blocking `requests` or `time.sleep` in loops.
- **Pydantic for all data structures** — never use raw dicts for domain objects (TradeSignal, Position, etc.)
- **No hardcoded magic numbers** — all thresholds in `config/settings.py`
- **Logging over prints** — use `logging.getLogger(__name__)`
- **Type hints required** on public functions
- **httpx, not requests** — async HTTP client
- **Import order**: stdlib → third-party → local (modules/, execution/, config/)

### Error handling patterns

- API calls: catch `httpx.HTTPError`, log warning, return None or empty
- WebSocket: exponential backoff reconnect (already in `price_streamer.py`)
- Background loops: catch broad Exception, log, sleep, continue — never let the loop die
- Critical failures (balance fetch, exchange info): raise and let supervisor restart

---

## Safety constraints (NEVER violate)

When writing or modifying code, these rules are non-negotiable:

1. **Never remove the `dry_run` flag or kill switch logic**
2. **Never raise `max_leverage` default above 5** — suggest to user but don't change default
3. **Never commit `.env` files** — only `.env.example`
4. **Never hardcode API keys** — always read from `config.settings`
5. **Never skip `risk.evaluate()` before executing a signal**
6. **Never suggest changes that would auto-execute signals >$1000 without manual confirmation**
7. **Never add code that withdraws funds** — bot should only trade, not move money

---

## Common tasks

### "Add a new strategy"

1. Create `backend/strategies/your_strategy.py`
2. Implement interface: `async def generate_signals(context) -> list[TradeSignal]`
3. Register in `main.py` inside `Bot.__init__`
4. Add config dataclass in `config/settings.py`

### "Tune for more signals"

Lower these in `config/settings.py`:
- `min_oi_change_pct`: 15 → 10
- `min_divergence_ratio`: 3 → 2.5

But ALWAYS backtest the new params before deploying. Offer to run backtest after changes.

### "Debug why no signals are generating"

Check in order:
1. `scan_all` in `oi_scanner.py` — log how many symbols pass filters
2. `_determine_strength` in `signal_aggregator.py` — WEAK signals are filtered out
3. `risk.evaluate` in `risk_manager.py` — 11 possible rejection reasons
4. `executor.dry_run` status — if False and size > threshold, needs approval

### "Add a new exchange"

Don't do it inline — suggest creating `modules/exchange/` abstraction first.
Current code is tightly coupled to Binance.

---

## Testing guidance

**No unit tests yet** (TODO). When adding them:
- Use `pytest` + `pytest-asyncio`
- Mock `BinanceFuturesClient` — never hit real API in tests
- Focus on `risk_manager.evaluate()` — highest-impact logic
- Use `backtester.py` output as integration test for strategy changes

---

## Known issues / gotchas

1. **Binance Square endpoint in `sentiment_scraper.py`** is unofficial and may break. Playwright fallback is TODO.
2. **OI history API rate limits** — current batch size 10 + 0.5s sleep. Don't reduce.
3. **`PositionTracker._handle_position_closed`** uses heuristic to guess exit reason (TP vs SL vs manual). Accurate exit reason requires order history API call — TODO.
4. **WebSocket in `price_streamer.py`** doesn't handle Binance 24h reset — TODO add scheduled reconnect.
5. **No database** — all state is in-memory. Bot restart = losing trade history in-memory (though Binance keeps it server-side).

---

## When in doubt

- **Favor safety over features.** If a change might enable bigger losses, add a config flag defaulted to OFF.
- **Ask before changing risk parameters.** Tuning `max_leverage` or `max_daily_loss_pct` requires user confirmation.
- **Never disable kill switches** even temporarily for "testing". Use `dry_run=true` instead.
- **Trust the backtest, not intuition.** If user says "I feel like this will work" — insist on backtest first.

---

## User context (Erik)

- Based in Vietnam, can communicate in English or Vietnamese
- Running on Windows + WSL, 32GB RAM
- Has experience with crypto trading bots (market maker on HyperLiquid, Polymarket)
- Has Docker, Python, Node.js set up
- Uses Binance (mainnet account active)
- Technical level: can read/modify Python, comfortable with async

When Erik asks in Vietnamese, respond in Vietnamese. When he asks in English, respond in English. Mix is also fine.
