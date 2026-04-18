"""
Wrapper quanh Binance Futures API.
Tách riêng để dễ mock khi test + dễ switch testnet/mainnet.
"""
import hmac
import hashlib
import time
from typing import Optional
import httpx
from urllib.parse import urlencode

from config.settings import config


class BinanceFuturesClient:
    def __init__(self):
        self.cfg = config.binance
        self._client = httpx.AsyncClient(
            base_url=self.cfg.base_url,
            timeout=10.0,
            headers={"X-MBX-APIKEY": self.cfg.api_key} if self.cfg.api_key else {}
        )
    
    # ==================== PUBLIC ENDPOINTS ====================
    
    async def get_exchange_info(self) -> dict:
        r = await self._client.get("/fapi/v1/exchangeInfo")
        r.raise_for_status()
        return r.json()
    
    async def get_all_symbols_ticker_24h(self) -> list[dict]:
        """24h stats cho tất cả symbols - dùng để filter thanh khoản."""
        r = await self._client.get("/fapi/v1/ticker/24hr")
        r.raise_for_status()
        return r.json()
    
    async def get_open_interest(self, symbol: str) -> dict:
        r = await self._client.get("/fapi/v1/openInterest", params={"symbol": symbol})
        r.raise_for_status()
        return r.json()
    
    async def get_open_interest_hist(
        self, symbol: str, period: str = "5m", limit: int = 30
    ) -> list[dict]:
        """
        Historical OI data — endpoint này QUAN TRỌNG cho divergence.
        period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
        """
        r = await self._client.get(
            "/futures/data/openInterestHist",
            params={"symbol": symbol, "period": period, "limit": limit}
        )
        r.raise_for_status()
        return r.json()
    
    async def get_klines(
        self, symbol: str, interval: str = "5m", limit: int = 100
    ) -> list[list]:
        r = await self._client.get(
            "/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit}
        )
        r.raise_for_status()
        return r.json()
    
    async def get_funding_rate(self, symbol: str) -> dict:
        r = await self._client.get("/fapi/v1/premiumIndex", params={"symbol": symbol})
        r.raise_for_status()
        return r.json()
    
    async def get_taker_long_short_ratio(
        self, symbol: str, period: str = "5m", limit: int = 10
    ) -> list[dict]:
        r = await self._client.get(
            "/futures/data/takerlongshortRatio",
            params={"symbol": symbol, "period": period, "limit": limit}
        )
        r.raise_for_status()
        return r.json()

    async def get_global_long_short_ratio(
        self, symbol: str, period: str = "1h", limit: int = 5
    ) -> list[dict]:
        """
        Global long/short ACCOUNT ratio (not taker volume ratio).
        Returns: [{"longShortRatio": "1.81", "longAccount": "0.644", ...}]
        Note: newer coins may return empty list.
        """
        r = await self._client.get(
            "/futures/data/globalLongShortAccountRatio",
            params={"symbol": symbol, "period": period, "limit": limit}
        )
        r.raise_for_status()
        return r.json()
    
    # ==================== SIGNED ENDPOINTS ====================
    
    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        signature = hmac.new(
            self.cfg.api_secret.encode(),
            query.encode(),
            hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params
    
    async def get_account(self) -> dict:
        params = self._sign({})
        r = await self._client.get("/fapi/v2/account", params=params)
        r.raise_for_status()
        return r.json()
    
    async def get_positions(self) -> list[dict]:
        params = self._sign({})
        r = await self._client.get("/fapi/v2/positionRisk", params=params)
        r.raise_for_status()
        return r.json()
    
    async def set_leverage(self, symbol: str, leverage: int) -> dict:
        params = self._sign({"symbol": symbol, "leverage": leverage})
        r = await self._client.post("/fapi/v1/leverage", params=params)
        r.raise_for_status()
        return r.json()
    
    async def place_order(
        self,
        symbol: str,
        side: str,               # BUY or SELL
        order_type: str,         # MARKET, LIMIT, STOP_MARKET, TAKE_PROFIT_MARKET
        quantity: float,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        reduce_only: bool = False,
        close_position: bool = False,
        position_side: str = "BOTH",
    ) -> dict:
        params = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
        }
        if price is not None:
            params["price"] = price
            params["timeInForce"] = "GTC"
        if stop_price is not None:
            params["stopPrice"] = stop_price
        if reduce_only:
            params["reduceOnly"] = "true"
        if close_position:
            params["closePosition"] = "true"
            params.pop("quantity", None)  # closePosition không cần quantity
        
        params = self._sign(params)
        r = await self._client.post("/fapi/v1/order", params=params)
        r.raise_for_status()
        return r.json()
    
    async def cancel_order(self, symbol: str, order_id: str) -> dict:
        params = self._sign({"symbol": symbol, "orderId": order_id})
        r = await self._client.delete("/fapi/v1/order", params=params)
        r.raise_for_status()
        return r.json()
    
    async def cancel_all_orders(self, symbol: str) -> dict:
        params = self._sign({"symbol": symbol})
        r = await self._client.delete("/fapi/v1/allOpenOrders", params=params)
        r.raise_for_status()
        return r.json()
    
    async def close(self):
        await self._client.aclose()
