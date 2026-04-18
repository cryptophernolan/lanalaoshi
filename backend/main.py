"""
Main orchestrator — v2 với full integration.

Luồng mới:
1. OIScanner → find divergences
2. SentimentScraper → get raw posts → NLPSentimentAnalyzer → confirmed sentiment
3. PriceStreamer → real-time prices (no HTTP polling)
4. SignalAggregator → combine
5. RiskManager → approve/reject
6. Executor → place orders
7. PositionTracker → monitor
8. TelegramBot → alerts + remote control
9. FastAPI + WebSocket → dashboard
"""
import asyncio
import logging
import sys
import math
import json
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from modules.binance_client import BinanceFuturesClient
from modules.oi_scanner import OIScanner
from modules.sentiment_scraper import SentimentScraper
from modules.nlp_sentiment import NLPSentimentAnalyzer
from modules.signal_aggregator import SignalAggregator
from modules.cattrade_scraper import CattradeScraper, CattradeSignal
from modules.new_listing_scanner import NewListingScanner
from modules.btc_bias_analyzer import BTCBiasAnalyzer
from modules.schemas import NewListingSetup
from modules.risk_manager import RiskManager
from modules.price_streamer import PriceStreamer
from modules.telegram_bot import TelegramBot
from modules.schemas import OIDivergence, SentimentScore, TradeSignal
from execution.executor import Executor
from execution.position_tracker import PositionTracker
from config.settings import config


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def _sanitize(obj):
    """Replace inf/nan float values with None so JSON serialization never crashes."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def safe_json(content) -> JSONResponse:
    return JSONResponse(content=_sanitize(content))


class Bot:
    def __init__(self):
        self.client = BinanceFuturesClient()
        self.oi_scanner = OIScanner(self.client)
        self.sentiment = SentimentScraper()
        self.nlp = NLPSentimentAnalyzer()
        self.aggregator = SignalAggregator()  # placeholder, replaced after btc_bias init below
        self.risk = RiskManager(self.client)
        self.executor = Executor(self.client)
        self.tracker = PositionTracker(self.client, self.executor)
        self.price_streamer = PriceStreamer()
        self.telegram = TelegramBot()
        
        self.cattrade = CattradeScraper()
        self.new_listing_scanner = NewListingScanner(self.client)
        self.btc_bias = BTCBiasAnalyzer()
        # Overwrite placeholder aggregator với BTCBias injected
        self.aggregator = SignalAggregator(btc_bias_analyzer=self.btc_bias)
        self.latest_divergences: list[OIDivergence] = []
        self.latest_sentiments: dict[str, SentimentScore] = {}
        self.latest_cattrades: dict[str, CattradeSignal] = {}
        self.latest_signals: list[TradeSignal] = []
        self.latest_new_listing_setups: dict[str, NewListingSetup] = {}
        self.pending_approvals: dict[str, TradeSignal] = {}
        self.ws_clients: set[WebSocket] = set()
        
        self._setup_telegram_handlers()
    
    def _setup_telegram_handlers(self):
        async def on_kill(reason: str):
            self.risk.trigger_kill_switch(reason)
            closed = await self.executor.close_all_positions(reason)
            await self.telegram.alert_kill_switch(f"{reason} ({closed} closed)")
        
        async def on_reset():
            self.risk.reset_kill_switch()
        
        async def on_close_all():
            await self.executor.close_all_positions("Telegram command")
        
        async def on_stats():
            await self.telegram.send_stats(self.tracker.get_stats())
        
        async def on_positions():
            await self.telegram.send_positions_list(self.tracker.positions)
        
        async def on_approve(signal_id: str):
            signal = self.pending_approvals.pop(signal_id, None)
            if signal:
                await self._execute(signal)
        
        async def on_reject(signal_id: str):
            self.pending_approvals.pop(signal_id, None)
        
        self.telegram.on_kill_switch = on_kill
        self.telegram.on_reset = on_reset
        self.telegram.on_close_all = on_close_all
        self.telegram.on_stats_request = on_stats
        self.telegram.on_positions_request = on_positions
        self.telegram.on_approve_signal = on_approve
        self.telegram.on_reject_signal = on_reject
    
    async def initialize(self):
        await self.executor.initialize()
        await self.oi_scanner.initialize()
        
        # Subscribe to top-volume symbols for price stream
        top_symbols = self.oi_scanner._symbols[:50]
        self.price_streamer.subscribe(top_symbols)
        
        logger.info("Bot initialized")
    
    async def _on_new_listing_signals(self, signals: list[TradeSignal]):
        """Called when NewListingScanner fires breakout signals."""
        # Sync latest setups cache
        self.latest_new_listing_setups = dict(self.new_listing_scanner._setups)
        for signal in signals:
            await self._process_signal(signal)
            await self._broadcast({
                "type": "new_listing_signal",
                "data": signal.model_dump(mode="json"),
            })

    async def _on_divergences(self, divergences: list[OIDivergence]):
        self.latest_divergences = divergences
        syms = [d.symbol for d in divergences]
        self.price_streamer.subscribe(syms)
        await self._try_generate_signals()
    
    async def _on_sentiments(self, sentiments: dict[str, SentimentScore]):
        self.latest_sentiments = sentiments
        await self._try_generate_signals()
    
    async def _try_generate_signals(self):
        if not self.latest_divergences:
            return
        
        current_prices = {}
        for div in self.latest_divergences:
            price = self.price_streamer.get_price(div.symbol)
            if price:
                current_prices[div.symbol] = price
            else:
                try:
                    klines = await self.client.get_klines(div.symbol, "1m", 1)
                    if klines:
                        current_prices[div.symbol] = float(klines[0][4])
                except Exception:
                    continue
        
        try:
            account = await self.client.get_account()
            balance = float(account.get("totalWalletBalance", 10000))
        except Exception:
            balance = 10000
        
        # Fetch CatTrade data (cached 2m, non-blocking)
        try:
            self.latest_cattrades = await self.cattrade.fetch()
        except Exception as e:
            logger.debug(f"CatTrade fetch error: {e}")

        signals = self.aggregator.aggregate(
            divergences=self.latest_divergences,
            sentiments=self.latest_sentiments,
            cattrades=self.latest_cattrades,
            account_balance=balance,
            current_prices=current_prices,
        )
        
        self.latest_signals = signals
        await self._broadcast({"type": "signals", "data": [s.model_dump(mode="json") for s in signals]})
        
        for signal in signals:
            await self._process_signal(signal)
    
    async def _process_signal(self, signal: TradeSignal):
        if signal.suggested_size_usdt >= config.executor.require_manual_confirm_above_usdt:
            self.pending_approvals[signal.signal_id] = signal
            await self.telegram.alert_signal(signal, require_approval=True)
            logger.info(f"Signal {signal.signal_id} awaiting Telegram approval")
            return
        
        await self._execute(signal)
    
    async def _execute(self, signal: TradeSignal):
        approved, reason = await self.risk.evaluate(signal)
        if not approved:
            logger.info(f"Signal {signal.signal_id} rejected: {reason}")
            await self._broadcast({
                "type": "signal_rejected",
                "signal_id": signal.signal_id,
                "reason": reason,
            })
            return
        
        await self.telegram.alert_signal(signal, require_approval=False)
        
        position = await self.executor.execute_signal(signal)
        if position:
            self.tracker.register(position)
            self.price_streamer.subscribe([position.symbol])
            await self.telegram.alert_position_opened(position)
            await self._broadcast({
                "type": "position_opened",
                "data": position.model_dump(mode="json"),
            })
    
    async def _broadcast(self, message: dict):
        dead = set()
        safe_msg = _sanitize(message)
        for ws in self.ws_clients:
            try:
                await ws.send_text(json.dumps(safe_msg))
            except Exception:
                dead.add(ws)
        self.ws_clients -= dead
    
    async def _monitor_new_listing_setups(self):
        """Sync new_listing_setups cache and broadcast updates every 30s."""
        while True:
            try:
                self.latest_new_listing_setups = dict(
                    self.new_listing_scanner._setups
                )
                if self.latest_new_listing_setups:
                    await self._broadcast({
                        "type": "new_listings",
                        "data": {
                            k: v.model_dump(mode="json")
                            for k, v in self.latest_new_listing_setups.items()
                        },
                    })
                # Check danger signals → close matching positions
                danger_syms = self.new_listing_scanner.get_danger_symbols()
                for sym in danger_syms:
                    pos = self.tracker._positions.get(sym)
                    if pos:
                        logger.warning(
                            f"NewListing danger signal on {sym} — closing position"
                        )
                        await self.executor.close_position(pos, "NL_DANGER_SIGNAL")
                        setup = self.latest_new_listing_setups.get(sym)
                        dsigs = setup.danger_signals if setup else []
                        await self.telegram.send(
                            f"⚠️ NEW LISTING DANGER — {sym}\n"
                            + "\n".join(f"  • {d}" for d in dsigs)
                        )
            except Exception as e:
                logger.warning(f"New listing monitor error: {e}")
            await asyncio.sleep(30)

    async def _monitor_closed_trades(self):
        known_ids = set()
        while True:
            try:
                for trade in self.tracker.closed_trades:
                    tid = f"{trade.symbol}-{trade.closed_at.isoformat()}"
                    if tid not in known_ids:
                        known_ids.add(tid)
                        await self.telegram.alert_position_closed(trade)
                        await self._broadcast({
                            "type": "position_closed",
                            "data": trade.model_dump(mode="json"),
                        })
            except Exception as e:
                logger.warning(f"Closed trade monitor error: {e}")
            await asyncio.sleep(5)
    
    async def run(self):
        await asyncio.gather(
            self.oi_scanner.run_forever(self._on_divergences),
            self.sentiment.run_forever(self._on_sentiments),
            self.tracker.run_forever(),
            self.price_streamer.run_forever(),
            self.telegram.run_polling(),
            self._monitor_closed_trades(),
            self.new_listing_scanner.run_forever(
                self._on_new_listing_signals,
                price_streamer=self.price_streamer,
            ),
            self._monitor_new_listing_setups(),
            self.btc_bias.run_forever(),
        )
    
    async def shutdown(self):
        await self.client.close()
        await self.sentiment.close()
        await self.telegram.close()


# ==================== FASTAPI ====================

bot = Bot()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.initialize()
    task = asyncio.create_task(bot.run())
    try:
        yield
    finally:
        task.cancel()
        await bot.shutdown()


app = FastAPI(title="OI Divergence Bot v2", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/status")
async def status():
    return safe_json({
        "running": True,
        "testnet": config.binance.testnet,
        "dry_run": config.executor.dry_run,
        "kill_switch": bot.risk.is_killed,
        "telegram_enabled": bot.telegram.enabled,
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.get("/api/divergences")
async def divergences():
    return safe_json([d.model_dump(mode="json") for d in bot.latest_divergences])


@app.get("/api/sentiments")
async def sentiments():
    return safe_json({k: v.model_dump(mode="json") for k, v in bot.latest_sentiments.items()})


@app.get("/api/signals")
async def signals():
    return safe_json([s.model_dump(mode="json") for s in bot.latest_signals])


@app.get("/api/positions")
async def positions():
    return safe_json([p.model_dump(mode="json") for p in bot.tracker.positions])


@app.get("/api/closed-trades")
async def closed_trades():
    return safe_json([t.model_dump(mode="json") for t in bot.tracker.closed_trades])


@app.get("/api/stats")
async def stats():
    return safe_json(bot.tracker.get_stats())


@app.get("/api/prices")
async def prices():
    return safe_json(bot.price_streamer.get_all_prices())


@app.get("/api/new-listings")
async def new_listings():
    """New listing pump setups — status of all tracked new coins."""
    return safe_json({
        k: v.model_dump(mode="json")
        for k, v in bot.latest_new_listing_setups.items()
    })


@app.get("/api/datasources")
async def datasources():
    """Status of all external data sources."""
    sentiment_status = bot.sentiment.get_status()
    cattrade_status = bot.cattrade.get_status()

    # OI Scanner status
    oi_symbols = len(bot.oi_scanner._symbols) if hasattr(bot.oi_scanner, "_symbols") else 0
    oi_divergences = len(bot.latest_divergences)

    # Price streamer status
    price_count = len(bot.price_streamer.get_all_prices())

    return safe_json({
        "oi_scanner": {
            "enabled": True,
            "status": "OK" if oi_symbols > 0 else "INITIALIZING",
            "symbols_tracked": oi_symbols,
            "active_divergences": oi_divergences,
        },
        "price_streamer": {
            "enabled": True,
            "status": "OK" if price_count > 0 else "NO_DATA",
            "symbols_streaming": price_count,
        },
        "sentiment": sentiment_status,
        "cattrade": cattrade_status,
        "binance_api": {
            "enabled": True,
            "status": "OK" if not bot.risk.is_killed else "KILLED",
            "testnet": config.binance.testnet,
            "dry_run": config.executor.dry_run,
        },
        "btc_bias": bot.btc_bias.get_status(),
    })


@app.get("/api/btc-bias")
async def btc_bias():
    """
    Smart Money BTC bias từ Paul Wei's account (BitMEX Hall of Legends, 52x return).
    Data source: github.com/bwjoke/BTC-Trading-Since-2020 (cập nhật daily)
    """
    b = bot.btc_bias.get_bias()
    return safe_json(b.to_dict())


@app.post("/api/btc-bias/refresh")
async def btc_bias_refresh():
    """Force refresh BTCBias từ GitHub."""
    b = await bot.btc_bias.refresh()
    return safe_json(b.to_dict())


@app.get("/api/cattrade")
async def cattrade():
    """CatTrade multi-timeframe OI + structure signals."""
    data = {
        base: {
            "symbol": s.symbol,
            "timeframe_rankings": s.timeframe_rankings,
            "oi_vol_direction": s.oi_vol_direction,
            "oi_vol_anomaly_score": s.oi_vol_anomaly_score,
            "oi_val_direction": s.oi_val_direction,
            "oi_val_anomaly_score": s.oi_val_anomaly_score,
            "zscore_vol_1h": s.zscore_vol_1h,
            "zscore_val_1h": s.zscore_val_1h,
            "market_share_score": s.market_share_score,
            "taker_ratio": s.taker_ratio,
            "structure_pattern": s.structure_pattern,
            "structure_strength": s.structure_strength,
            "direction_bias": s.direction_bias,
            "composite_score": s.composite_score,
            "multi_tf_confirmed": s.multi_timeframe_confirmed,
        }
        for base, s in bot.latest_cattrades.items()
    }
    return safe_json(data)


@app.post("/api/kill-switch/trigger")
async def trigger_kill(reason: str = "Manual"):
    bot.risk.trigger_kill_switch(reason)
    closed = await bot.executor.close_all_positions(reason)
    await bot.telegram.alert_kill_switch(f"{reason} ({closed} positions)")
    return safe_json({"killed": True, "positions_closed": closed})


@app.post("/api/kill-switch/reset")
async def reset_kill():
    bot.risk.reset_kill_switch()
    return safe_json({"killed": False})


@app.post("/api/position/{symbol}/close")
async def close_pos(symbol: str):
    pos = bot.tracker._positions.get(symbol)
    if not pos:
        raise HTTPException(404, "Position not found")
    success = await bot.executor.close_position(pos, "MANUAL")
    return safe_json({"success": success})


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    bot.ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        bot.ws_clients.discard(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=config.api_host,
        port=config.api_port,
        reload=False,
    )
