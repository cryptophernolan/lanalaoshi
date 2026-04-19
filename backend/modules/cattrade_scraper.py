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

# Mapping 结构形态 → bias  (dùng substring match để chịu garbling)
_STRUCTURE_BIAS = {
    "大户领先做多": 1,
    "多头共振":     1,
    "主动买领先多": 1,
    "大户领先做空": -1,
    "空头共振":     -1,
    "主动买领先空": -1,
}


def _structure_bias_fuzzy(pattern: str) -> int:
    """
    Fuzzy match structure bias — chịu được encoding garbling.
    `做` (\u505a) có thể bị garble thành \udc81 hoặc ký tự khác.
    """
    if not pattern:
        return 0
    # Exact match first
    exact = _STRUCTURE_BIAS.get(pattern, None)
    if exact is not None:
        return exact
    # Substring-based fallback
    if "大户领先" in pattern and "多" in pattern:
        return 1
    if "多头共振" in pattern:
        return 1
    if "主动买领先多" in pattern:
        return 1
    if "大户领先" in pattern and "空" in pattern:
        return -1
    if "空头共振" in pattern:
        return -1
    if "主动买领先空" in pattern:
        return -1
    return 0


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
        score += _structure_bias_fuzzy(self.structure_pattern or "")
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
            # Track multi-window sections order: first = 量, second = 额
            _multi_window_seen = 0

            for row in rows:
                if not row or len(row) < 3:
                    continue

                col0 = row[0].strip()

                # ── Section header detection ──
                # Header rows: col0 non-empty, col1 is NOT a digit (rank number)
                # Note: col1 = "序号" but may be garbled → just check col0 non-empty
                if col0:
                    detected = False
                    if "异动" in col0:
                        current_section = col0
                        detected = True
                    elif "多窗" in col0:
                        # 多窗口持仓量榜 vs 多窗口持仓额榜
                        # Distinguish by garbled char: 量→\udc8f, 额→\udc9d
                        # OR by order (量 always comes first in sheet)
                        _multi_window_seen += 1
                        if _multi_window_seen == 1:
                            current_section = "多窗口持仓量榜"  # normalized
                        else:
                            current_section = "多窗口持仓额榜"  # normalized
                        detected = True
                    elif "市场份" in col0:
                        current_section = "市场份额相对榜"
                        detected = True
                    elif "波动区间" in col0:
                        current_section = "波动区间榜"
                        detected = True
                    elif "结构分" in col0:
                        current_section = "结构分歧榜"
                        detected = True
                    if detected:
                        continue
                    # Non-section non-empty col0 = ad/metadata → skip
                    continue

                # ── Data rows: col0 empty, col1 = rank digit ──
                rank_str = row[1].strip()
                if not rank_str.isdigit():
                    continue

                # Symbol is base symbol WITHOUT USDT (sheet format)
                base = row[2].strip()
                if not base or len(base) > 15:
                    continue
                # Filter out non-symbol garbage (Chinese chars, spaces, etc.)
                if any(ord(c) > 0x2E7F for c in base):
                    continue  # skip garbled/Chinese symbol names

                # Binance futures symbol = base + USDT
                symbol = base + "USDT"

                if base not in signals:
                    signals[base] = CattradeSignal(symbol=symbol, base=base)

                sig = signals[base]

                # ── 异动榜 (5m/15m/30m/1h/2h/4h/1d/1w) ──
                if "异动" in current_section:
                    # Extract timeframe from section name prefix (e.g. "1h 异动榜" → "1h")
                    parts = current_section.split()
                    tf = parts[0] if parts else ""
                    if tf and tf not in sig.timeframe_rankings:
                        sig.timeframe_rankings.append(tf)
                    # Z-scores: col9=量Z分数, col10=额Z分数
                    if len(row) > 10:
                        if tf == "1h":
                            sig.zscore_vol_1h = _f(row[9])
                            sig.zscore_val_1h = _f(row[10])
                        elif tf == "4h":
                            sig.zscore_vol_4h = _f(row[9])
                            sig.zscore_val_4h = _f(row[10])

                # ── 多窗口持仓量榜 ──
                # cols: rank, symbol, 5m, 15m, 30m, 1h, 2h, 4h, 1d, 1w, 冲击比, 方向, 异常分, 现持仓额
                elif current_section == "多窗口持仓量榜":
                    if len(row) > 13:
                        raw_dir = row[12].strip()
                        sig.oi_vol_direction = raw_dir or None
                        sig.oi_vol_anomaly_score = _f(row[13])

                # ── 多窗口持仓额榜 ──
                elif current_section == "多窗口持仓额榜":
                    if len(row) > 13:
                        raw_dir = row[12].strip()
                        sig.oi_val_direction = raw_dir or None
                        sig.oi_val_anomaly_score = _f(row[13])

                # ── 市场份额相对榜 ──
                # cols: rank, symbol, 市场份额, 5m份额变化, 1h份额变化, 5m量超额变动,
                #        5m额超额变动, 1h量超额变动, 1h额超额变动, 异常综合分, 现持仓额
                elif current_section == "市场份额相对榜":
                    if len(row) > 10:
                        sig.market_share_score = _f(row[10])  # col10 = 异常综合分

                # ── 波动区间榜 ──
                # cols: rank, symbol, 量连续根数, 额连续根数, 1h量波动Z分数, 1h额波动Z分数, ...
                elif current_section == "波动区间榜":
                    if len(row) > 6:
                        sig.zscore_vol_1h = max(sig.zscore_vol_1h, abs(_f(row[5])))
                        sig.zscore_val_1h = max(sig.zscore_val_1h, abs(_f(row[6])))

                # ── 结构分歧榜 ──
                # cols: rank, symbol, 主动买比(taker), 大户账户比5m变化, 大户仓比5m变化,
                #        全市场账户比5m变化, 主动买比5m变化, 5m结构变化强度, 1h结构变化强度,
                #        大户仓-市场账户, 主动买-市场账户, 大户集中度, 结构共识分, 结构冲突分,
                #        主动买滞后数, 结构形态(col16), 结构强度(col17), 现持仓额(col18)
                elif current_section == "结构分歧榜":
                    if len(row) > 17:
                        sig.taker_ratio = _f(row[3])
                        pattern = row[16].strip()
                        sig.structure_pattern = pattern or None
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
