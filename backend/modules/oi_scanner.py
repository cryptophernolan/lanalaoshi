"""
OI Divergence Scanner — Trái tim của hệ thống.

Logic:
1. Poll tất cả symbols USDT-M mỗi N giây
2. So sánh OI_t với OI_{t-window}, tương tự với price
3. Tìm: OI tăng mạnh nhưng price chưa di chuyển nhiều
4. XÁC ĐỊNH HƯỚNG bằng funding rate + taker ratio (cải tiến quan trọng so với bài gốc)
5. Output: OIDivergence với direction đã được xác định
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from modules.binance_client import BinanceFuturesClient
from modules.schemas import OIDivergence, OISnapshot, Side
from config.settings import config

logger = logging.getLogger(__name__)


class OIScanner:
    def __init__(self, client: BinanceFuturesClient):
        self.client = client
        self.cfg = config.oi_scanner
        self._symbols: list[str] = []
        self._last_scan: dict[str, datetime] = {}
        self._volume_cache: dict[str, float] = {}  # symbol → 24h volume USDT
    
    async def initialize(self):
        """Load danh sách symbols đủ thanh khoản và cache volume."""
        tickers = await self.client.get_all_symbols_ticker_24h()

        filtered = []
        for t in tickers:
            symbol = t["symbol"]
            if not symbol.endswith("USDT"):
                continue
            if symbol in self.cfg.excluded_symbols:
                continue
            quote_volume = float(t.get("quoteVolume", 0))
            if quote_volume < self.cfg.min_24h_volume_usdt:
                continue
            filtered.append(symbol)
            self._volume_cache[symbol] = quote_volume

        self._symbols = filtered
        logger.info(f"OIScanner initialized with {len(self._symbols)} symbols")
    
    async def scan_symbol(
        self, symbol: str, window_minutes: int
    ) -> Optional[OIDivergence]:
        """
        Phân tích 1 symbol trong 1 time window.
        Trả về OIDivergence nếu đạt threshold, None nếu không.
        """
        try:
            # Pick period phù hợp với window
            if window_minutes <= 15:
                period, limit = "5m", max(window_minutes // 5 + 1, 4)
            elif window_minutes <= 60:
                period, limit = "15m", window_minutes // 15 + 1
            else:
                period, limit = "1h", window_minutes // 60 + 1
            
            # Fetch parallel cho speed
            oi_hist_task = self.client.get_open_interest_hist(symbol, period, limit)
            klines_task = self.client.get_klines(symbol, period, limit)
            funding_task = self.client.get_funding_rate(symbol)
            taker_task = self.client.get_taker_long_short_ratio(symbol, period, 3)
            
            oi_hist, klines, funding, taker = await asyncio.gather(
                oi_hist_task, klines_task, funding_task, taker_task,
                return_exceptions=True
            )
            
            # Check errors
            for r in [oi_hist, klines, funding, taker]:
                if isinstance(r, Exception):
                    logger.debug(f"{symbol}: fetch error {r}")
                    return None
            
            if not oi_hist or len(oi_hist) < 2 or not klines or len(klines) < 2:
                return None
            
            # Tính OI change
            oi_start = float(oi_hist[0]["sumOpenInterestValue"])
            oi_end = float(oi_hist[-1]["sumOpenInterestValue"])
            if oi_start == 0:
                return None
            oi_change_pct = ((oi_end - oi_start) / oi_start) * 100
            
            # Tính price change
            price_start = float(klines[0][1])   # open
            price_end = float(klines[-1][4])    # close
            if price_start == 0:
                return None
            price_change_pct = ((price_end - price_start) / price_start) * 100
            
            # Core filter: OI phải tăng đáng kể
            if abs(oi_change_pct) < self.cfg.min_oi_change_pct:
                return None
            
            # Core filter: price chưa di chuyển nhiều
            if abs(price_change_pct) > self.cfg.max_price_change_pct:
                return None
            
            # Divergence ratio
            if abs(price_change_pct) < 0.1:
                ratio = abs(oi_change_pct) / 0.1  # avoid div by zero
            else:
                ratio = abs(oi_change_pct) / abs(price_change_pct)
            
            if ratio < self.cfg.min_divergence_ratio:
                return None
            
            # ============ XÁC ĐỊNH HƯỚNG (cải tiến) ============
            # Bài gốc không làm cái này — đây là lỗ hổng lớn
            funding_rate = float(funding["lastFundingRate"])
            
            taker_ratio = 1.0
            if taker and len(taker) > 0:
                latest = taker[-1]
                buy_sell = float(latest.get("buySellRatio", 1.0))
                taker_ratio = buy_sell
            
            # Logic xác định direction:
            # - OI tăng + giá chưa tăng + taker buy > sell + funding chưa cao
            #   → Smart money LONG, retail chưa theo
            # - OI tăng + giá chưa giảm + taker sell > buy + funding dương cao
            #   → Smart money SHORT, squeeze longs
            
            direction: Optional[Side] = None
            confidence = 0.5
            
            long_signals = 0
            short_signals = 0
            
            if taker_ratio > self.cfg.taker_ratio_threshold:
                long_signals += 1
            elif taker_ratio < (1 / self.cfg.taker_ratio_threshold):
                short_signals += 1
            
            # Funding rate: dương cao = long crowded → contrarian short
            # Funding âm = shorts crowded → contrarian long
            if funding_rate > self.cfg.funding_rate_threshold:
                short_signals += 1  # crowded longs → short bias
            elif funding_rate < -self.cfg.funding_rate_threshold:
                long_signals += 1   # crowded shorts → long bias
            
            # Price slight bias: giá nhích xanh nhẹ với OI pump = long building
            if price_change_pct > 0.5:
                long_signals += 1
            elif price_change_pct < -0.5:
                short_signals += 1
            
            if long_signals > short_signals:
                direction = Side.LONG
                confidence = 0.5 + 0.15 * (long_signals - short_signals)
            elif short_signals > long_signals:
                direction = Side.SHORT
                confidence = 0.5 + 0.15 * (short_signals - long_signals)
            else:
                # Không rõ hướng → bỏ qua, không trade
                return None
            
            confidence = min(confidence, 1.0)
            
            volume_24h = self._volume_cache.get(symbol, 0.0)

            return OIDivergence(
                symbol=symbol,
                window_minutes=window_minutes,
                oi_change_pct=oi_change_pct,
                price_change_pct=price_change_pct,
                divergence_ratio=ratio,
                direction=direction,
                confidence=confidence,
                timestamp=datetime.utcnow(),
                funding_rate=funding_rate,
                taker_ratio=taker_ratio,
                volume_24h_usdt=volume_24h,
            )
        
        except Exception as e:
            logger.warning(f"scan_symbol {symbol} failed: {e}")
            return None
    
    async def scan_all(self, window_minutes: int = None) -> list[OIDivergence]:
        """Scan toàn bộ symbols với concurrency limit."""
        window = window_minutes or self.cfg.medium_window

        # Refresh volume cache 1 lần per scan thay vì gọi trong mỗi scan_symbol
        try:
            tickers = await self.client.get_all_symbols_ticker_24h()
            for t in tickers:
                sym = t.get("symbol", "")
                if sym:
                    self._volume_cache[sym] = float(t.get("quoteVolume", 0))
        except Exception as e:
            logger.warning(f"Volume cache refresh failed: {e}")

        # Batch để không hit rate limit
        batch_size = 10
        results: list[OIDivergence] = []
        
        for i in range(0, len(self._symbols), batch_size):
            batch = self._symbols[i:i + batch_size]
            batch_results = await asyncio.gather(
                *[self.scan_symbol(s, window) for s in batch],
                return_exceptions=True
            )
            for r in batch_results:
                if isinstance(r, OIDivergence):
                    results.append(r)
            await asyncio.sleep(0.5)  # rate limit respect
        
        # Sort theo confidence desc
        results.sort(key=lambda x: x.confidence * x.divergence_ratio, reverse=True)
        logger.info(f"Scan done: {len(results)} divergences found from {len(self._symbols)} symbols")
        return results
    
    async def run_forever(self, callback):
        """Main loop — scan liên tục, call callback với kết quả."""
        await self.initialize()
        while True:
            try:
                divergences = await self.scan_all()
                if divergences:
                    await callback(divergences)
            except Exception as e:
                logger.error(f"Scan loop error: {e}", exc_info=True)
            await asyncio.sleep(self.cfg.scan_interval_seconds)
