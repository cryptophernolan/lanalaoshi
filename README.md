# OI Divergence Auto-Trading Bot

Hệ thống trading tự động dựa trên chiến thuật **OI divergence + sentiment confirmation** cho Binance USDT-M Futures.

## Kiến trúc

```
┌─────────────────────────────────────────────────────────────┐
│                        DASHBOARD (React)                     │
│   Positions │ Signals │ PnL │ Risk Panel │ Kill Switch      │
└───────────────────────────▲─────────────────────────────────┘
                            │ WebSocket + REST
┌───────────────────────────┴─────────────────────────────────┐
│                      BACKEND (FastAPI)                       │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ OI Scanner   │  │ Sentiment    │  │ Signal       │      │
│  │ (WS + REST)  │→ │ Scraper      │→ │ Aggregator   │      │
│  └──────────────┘  └──────────────┘  └──────┬───────┘      │
│                                             │               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────▼───────┐      │
│  │ Risk Manager │← │ Position     │← │ Executor     │      │
│  │ (Kill switch)│  │ Tracker      │  │ (Binance API)│      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│                                                              │
│              ┌──────────────────────┐                        │
│              │  Backtest Framework  │                        │
│              └──────────────────────┘                        │
└─────────────────────────────────────────────────────────────┘
```

## Các module chính

1. **OI Scanner** — detect Open Interest divergence
2. **Sentiment Scraper** — Binance Square + Twitter/X
3. **Signal Aggregator** — kết hợp 2 tầng + filters (funding rate, direction)
4. **Risk Manager** — position sizing, max drawdown, kill switch
5. **Executor** — đặt lệnh qua Binance Futures API
6. **Backtest** — test strategy trên historical data

## CẢNH BÁO QUAN TRỌNG

- **KHÔNG BAO GIỜ chạy live với tiền thật trước khi backtest ≥ 6 tháng dữ liệu**
- **BẮT BUỘC dùng Binance Testnet trước**
- **API key phải là Futures-only, IP-whitelisted, withdrawal DISABLED**
- Trading futures có thể mất hết tiền. Đây là template kỹ thuật, không phải lời khuyên tài chính.

## Setup

```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp config/.env.example config/.env  # điền API keys
python main.py

# Dashboard
cd dashboard
npm install
npm run dev
```
