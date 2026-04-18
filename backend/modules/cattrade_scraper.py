"""
CatTrade Dashboard Scraper
==========================
Fetch Google Sheet CSV từ CatTrade community (cập nhật ~2 phút/lần).
Sheet: https://docs.google.com/spreadsheets/d/1k16nGFCE7oBXrEqvTpHSA2Z5530GM_kou-wiWklTsfY

Các bảng được parse:
  - 5m/15m/1h/4h/1d/1w 异动榜  → volume/OI Z-score anomalies
  - 多窗口持仓量榜 / 多窗口持仓额榜 → multi-window OI direction consistency
  - 市场份额相对榜              → market share anomaly score
  - 波动区间榜                  → volatility range + Z-score
  - 结构分歧榜                  → large-account structure + taker ratio

Output: dict[symbol → CattradeSignal]
"""
import csv
import io
import logging
import asyncio
from dataclasses import dataclass, field
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

SHEET_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1k16nGFCE7oBXrEqvTpHSA2Z5530GM_kou-wiWklTsfY"
    "/export?format=csv&gid=1915220137"
)

# Mapping 方向一致性 → bias (+1 bullish, -1 bearish, 0 neutral)
_DIRECTION_BIAS = {
    "同向上": 1,
    "上拐":   1,   # turning bullish
    "同向下": -1,
    "下拐":   -1,  # turning bearish
}

# Mapping 结构形态 → bias
_STRUCTURE_BIAS = {
    "大户领先做多": 1,
    "多头共振":     1,
    "主动买领先多": 1,
    "大户领先做空": -1,
    "空头共振":     -1,
    "主动买领先空": -1,
}


@dataclass
class CattradeSignal:
    symbol: str                            # e.g. "BTCUSDT" (with USDT suffix)
    base: str                              # e.g. "BTC"

    # Multi-window OI
    oi_vol_direction: Optional[str] = None   # 同向上 / 同向下 / 上拐 / 下拐
    oi_vol_anomaly_score: float = 0.0
    oi_val_direction: Optional[str] = None
    oi_val_anomaly_score: float = 0.0

    # Timeframe rankings (set of timeframes where symbol appears in 异动榜 top-7)
    timeframe_rankings: list[str] = field(default_factory=list)  # ["1h","4h","1d"]

    # Per-timeframe Z-scores (best available)
    zscore_vol_1h: float = 0.0
    zscore_val_1h: float = 0.0
    zscore_vol_4h: float = 0.0
    zscore_val_4h: float = 0.0

    # Market share anomaly
    market_share_score: float = 0.0

    # Structure divergence
    taker_ratio: Optional[float] = None
    structure_pattern: Optional[str] = None    # 大户领先做多 / 大户领先做空 / 多头共振 / ...
    structure_strength: float = 0.0

    # Derived
    @property
    def direction_bias(self) -> int:
        """
        Combined directional bias: +1 bullish, -1 bearish, 0 neutral.
        Based on multi-window OI direction + structure pattern.
        """
        score = 0
        score += _DIRECTION_BIAS.get(self.oi_vol_direction or "", 0)
        score += _DIRECTION_BIAS.get(self.oi_val_direction or "", 0)
        score += _STRUCTURE_BIAS.get(self.structure_pattern or "", 0)
        if score > 0:
            return 1
        if score < 0:
            return -1
        return 0

    @property
    def multi_timeframe_confirmed(self) -> bool:
        """True nếu symbol xuất hiện trong ít nhất 2 timeframe rankings."""
        return len(self.timeframe_rankings) >= 2

    @property
    def composite_score(self) -> float:
        """0-100 score tổng hợp từ các indicators."""
        s = 0.0
        # Z-score 1h (capped at 10, normalized)
        z = max(abs(self.zscore_vol_1h), abs(self.zscore_val_1h))
        s += min(z / 10.0, 1.0) * 30

        # Multi-window anomaly
        best_anomaly = max(self.oi_vol_anomaly_score, self.oi_val_anomaly_score)
        s += min(best_anomaly / 20.0, 1.0) * 30

        # Multi-timeframe rankings count
        s += min(len(self.timeframe_rankings) / 3.0, 1.0) * 20

        # Market share
        s += min(self.market_share_score / 100.0, 1.0) * 10

        # Structure strength
        s += min(self.structure_strength / 15.0, 1.0) * 10

        return round(min(s, 100.0), 2)


def _pct(val: str) -> float:
    """'3.40%' → 3.40, '-2.15' → -2.15"""
    try:
        return float(val.strip().replace("%", "").replace("+", ""))
    except (ValueError, AttributeError):
        return 0.0


def _f(val: str) -> float:
    try:
        return float(val.strip().replace("+", ""))
    except (ValueError, AttributeError):
        return 0.0


class CattradeScraper:
    def __init__(self):
        self._client = httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        self._cache: dict[str, CattradeSignal] = {}
        self._cache_ts: float = 0.0
        self._last_symbols_count: int = 0
        self._last_multi_tf_count: int = 0
        self._last_error: Optional[str] = None

    async def fetch(self) -> dict[str, CattradeSignal]:
        """
        Fetch và parse sheet CSV. Cache 2 phút (sheet update interval).
        Returns dict keyed by base symbol (e.g. "BTC", không có "USDT").
        """
        import time
        if time.time() - self._cache_ts < 120 and self._cache:
            return self._cache

        try:
            r = await self._client.get(
                SHEET_CSV_URL,
                headers={"User-Agent": "oi-bot/1.0"},
            )
            r.raise_for_status()
            raw = r.text
        except Exception as e:
            logger.warning(f"CatTrade fetch failed: {e}")
            return self._cache

        signals: dict[str, CattradeSignal] = {}

        try:
            reader = csv.reader(io.StringIO(raw))
            rows = list(reader)

            current_section = ""
            for row in rows:
                if not row or len(row) < 3:
                    continue

                col0 = row[0].strip()

                # Detect section header lines (col0 non-empty, col1 = "序号")
                if col0 and len(row) > 1 and row[1].strip() in ("序号", ""):
                    if "异动" in col0 or "持仓" in col0 or "份额" in col0 or "波动" in col0 or "结构" in col0:
                        current_section = col0
                    continue

                # Data rows: col0 empty, col1 is rank number
                if col0 != "" or len(row) < 3:
                    continue

                rank_str = row[1].strip()
                if not rank_str.isdigit():
                    continue

                symbol = row[2].strip()
                if not symbol.endswith("USDT"):
                    continue

                base = symbol.replace("USDT", "")

                if base not in signals:
                    signals[base] = CattradeSignal(symbol=symbol, base=base)

                sig = signals[base]

                # ── 异动榜 (5m/15m/1h/4h/1d/1w) ──
                if "异动" in current_section:
                    tf = current_section.split()[0]  # "5m", "15m", "1h", "4h", "1d", "1w"
                    if tf not in sig.timeframe_rankings:
                        sig.timeframe_rankings.append(tf)
                    if len(row) > 10:
                        if tf == "1h":
                            sig.zscore_vol_1h = _f(row[9])
                            sig.zscore_val_1h = _f(row[10])
                        elif tf == "4h":
                            sig.zscore_vol_4h = _f(row[9])
                            sig.zscore_val_4h = _f(row[10])

                # ── 多窗口持仓量榜 ──
                elif "多窗口持仓量" in current_section:
                    if len(row) > 14:
                        sig.oi_vol_direction = row[12].strip() or None
                        sig.oi_vol_anomaly_score = _f(row[13])

                # ── 多窗口持仓额榜 ──
                elif "多窗口持仓额" in current_section:
                    if len(row) > 14:
                        sig.oi_val_direction = row[12].strip() or None
                        sig.oi_val_anomaly_score = _f(row[13])

                # ── 市场份额相对榜 ──
                elif "市场份额" in current_section:
                    if len(row) > 10:
                        sig.market_share_score = _f(row[9])

                # ── 波动区间榜 ──
                elif "波动区间" in current_section:
                    if len(row) > 7:
                        sig.zscore_vol_1h = max(sig.zscore_vol_1h, abs(_f(row[5])))
                        sig.zscore_val_1h = max(sig.zscore_val_1h, abs(_f(row[6])))

                # ── 结构分歧榜 ──
                elif "结构分歧" in current_section:
                    if len(row) > 17:
                        sig.taker_ratio = _f(row[3])
                        sig.structure_pattern = row[16].strip() or None
                        sig.structure_strength = _f(row[17])

            self._cache = signals
            self._cache_ts = time.time()
            total = len(signals)
            multi_tf = sum(1 for s in signals.values() if s.multi_timeframe_confirmed)
            self._last_symbols_count = total
            self._last_multi_tf_count = multi_tf
            self._last_error = None
            logger.info(
                f"CatTrade: {total} symbols parsed | "
                f"{multi_tf} multi-timeframe confirmed"
            )

        except Exception as e:
            self._last_error = str(e)
            logger.warning(f"CatTrade parse error: {e}", exc_info=True)

        return self._cache

    def get_status(self) -> dict:
        """Returns status dict for /api/datasources endpoint."""
        import time
        now = time.time()
        age = round(now - self._cache_ts) if self._cache_ts > 0 else None
        status = "OK" if self._last_symbols_count > 0 else ("ERROR" if self._last_error else "NO_DATA")
        return {
            "enabled": True,
            "status": status,
            "last_update_age_s": age,
            "symbols_parsed": self._last_symbols_count,
            "multi_tf_confirmed": self._last_multi_tf_count,
            "last_error": self._last_error,
            "sheet_url": SHEET_CSV_URL,
        }

    async def close(self):
        await self._client.aclose()
