"""
WebSocket Price Streamer — realtime price thay vì polling.

Binance Futures WebSocket streams:
- mark price: <symbol>@markPrice
- aggregate trades: <symbol>@aggTrade
- order book: <symbol>@depth
- open interest: không có WS, phải poll 5min

Module này maintain latest prices in-memory, update liên tục.
Các module khác (executor, tracker) query instant không cần HTTP call.
"""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable

import websockets

from config.settings import config

logger = logging.getLogger(__name__)


@dataclass
class PriceData:
    symbol: str
    mark_price: float
    last_price: float
    bid: float = 0
    ask: float = 0
    timestamp: datetime = field(default_factory=datetime.utcnow)


class PriceStreamer:
    """
    Maintain latest prices for subscribed symbols via WebSocket.
    
    Usage:
        streamer = PriceStreamer()
        streamer.subscribe(["BTCUSDT", "ETHUSDT"])
        asyncio.create_task(streamer.run_forever())
        
        price = streamer.get_price("BTCUSDT")
    """
    
    def __init__(self):
        self._prices: dict[str, PriceData] = {}
        self._subscriptions: set[str] = set()
        self._ws = None
        self._base_url = config.binance.ws_url
        self._callbacks: list[Callable] = []
        self._needs_resubscribe = asyncio.Event()
    
    def subscribe(self, symbols: list[str]):
        """Add symbols to subscription list."""
        new = {s.lower() for s in symbols} - self._subscriptions
        if new:
            self._subscriptions.update(new)
            self._needs_resubscribe.set()
            logger.info(f"Subscribed to {len(new)} new symbols, total {len(self._subscriptions)}")
    
    def unsubscribe(self, symbols: list[str]):
        removed = {s.lower() for s in symbols} & self._subscriptions
        if removed:
            self._subscriptions -= removed
            self._needs_resubscribe.set()
    
    def get_price(self, symbol: str) -> Optional[float]:
        """Get latest mark price. None nếu chưa có data."""
        data = self._prices.get(symbol.upper())
        return data.mark_price if data else None
    
    def get_price_data(self, symbol: str) -> Optional[PriceData]:
        return self._prices.get(symbol.upper())
    
    def get_all_prices(self) -> dict[str, float]:
        return {k: v.mark_price for k, v in self._prices.items()}
    
    def on_price_update(self, callback: Callable[[PriceData], None]):
        """Register callback khi price update."""
        self._callbacks.append(callback)
    
    async def _handle_message(self, msg: dict):
        # Binance futures combined stream format:
        # { "stream": "btcusdt@markPrice", "data": { ... } }
        stream = msg.get("stream", "")
        data = msg.get("data", {})
        
        if not data:
            return
        
        if "@markPrice" in stream:
            symbol = data.get("s", "").upper()
            mark_price = float(data.get("p", 0))
            
            if symbol and mark_price > 0:
                existing = self._prices.get(symbol)
                price_data = PriceData(
                    symbol=symbol,
                    mark_price=mark_price,
                    last_price=existing.last_price if existing else mark_price,
                    bid=existing.bid if existing else 0,
                    ask=existing.ask if existing else 0,
                )
                self._prices[symbol] = price_data
                
                for cb in self._callbacks:
                    try:
                        cb(price_data)
                    except Exception as e:
                        logger.warning(f"Price callback error: {e}")
        
        elif "@aggTrade" in stream:
            symbol = data.get("s", "").upper()
            last = float(data.get("p", 0))
            if symbol in self._prices:
                self._prices[symbol].last_price = last
    
    async def _build_subscribe_message(self) -> dict:
        streams = []
        for sym in self._subscriptions:
            streams.append(f"{sym}@markPrice")
            streams.append(f"{sym}@aggTrade")
        return {
            "method": "SUBSCRIBE",
            "params": streams,
            "id": int(datetime.utcnow().timestamp()),
        }
    
    async def _connect_and_stream(self):
        """1 connection attempt. Reconnect logic ở run_forever."""
        # Empty subscribe → đợi
        if not self._subscriptions:
            await asyncio.sleep(5)
            return
        
        url = f"{self._base_url}/stream"
        async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
            self._ws = ws
            
            # Initial subscribe
            sub_msg = await self._build_subscribe_message()
            await ws.send(json.dumps(sub_msg))
            logger.info(f"WS connected, subscribed to {len(self._subscriptions)} symbols")
            
            # Background task để resubscribe khi có thêm symbols
            async def resubscribe_watcher():
                while True:
                    await self._needs_resubscribe.wait()
                    self._needs_resubscribe.clear()
                    try:
                        await ws.send(json.dumps(await self._build_subscribe_message()))
                    except Exception:
                        break
            
            watcher_task = asyncio.create_task(resubscribe_watcher())
            
            try:
                async for raw_msg in ws:
                    try:
                        msg = json.loads(raw_msg)
                        await self._handle_message(msg)
                    except json.JSONDecodeError:
                        pass
                    except Exception as e:
                        logger.warning(f"WS msg handle error: {e}")
            finally:
                watcher_task.cancel()
    
    async def run_forever(self):
        """Reconnect loop with exponential backoff."""
        backoff = 1
        max_backoff = 60
        
        while True:
            try:
                await self._connect_and_stream()
                backoff = 1  # reset on successful connection
            except Exception as e:
                logger.warning(f"WS connection error: {e}, reconnect in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
