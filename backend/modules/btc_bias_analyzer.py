"""
BTCBiasAnalyzer — "Smart Money" signal từ tài khoản thực Paul Wei (@coolish).

Nguồn dữ liệu: https://github.com/bwjoke/BTC-Trading-Since-2020
- 6 năm giao dịch BTC (2020-2026), 52x return, BitMEX Hall of Legends
- Cập nhật mỗi ngày, exposed qua GitHub raw API
- Primary instrument: XBTUSD inverse perpetual (99%+ BTC-settled)

Module này:
1. Fetch vị thế hiện tại (position snapshot)
2. Fetch equity curve 30 ngày gần nhất để tính market regime
3. Tổng hợp thành BTCBias: {direction, confidence, context}
4. Tích hợp vào SignalAggregator như 1 lớp filter/boost

Tần suất refresh: mỗi 1 giờ (repo update 1 lần/ngày, không cần poll liên tục)

Dữ liệu quan trọng đã phân tích từ repo:
- 2020: +473%, 2021: +526%, 2022: +3.5%, 2023: +14.9%, 2024: +3.7%, 2025: +12.8%
- ALL-TIME PEAK: 96.46 XBT (2026-03-28), hiện tại: 96.39 XBT
- Vị thế hiện tại (Apr 2026): SHORT -1,298,000 XBTUSD @ $73,013 (100x leverage)
- Unrealized PnL: -3.4% (BTC mark ~$75,600)
- Credibility score: 52.4x từ 1.84 BTC ban đầu
"""
import asyncio
import csv
import io
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

GITHUB_RAW_BASE = "https://raw.githubusercontent.com/bwjoke/BTC-Trading-Since-2020/main"
POSITION_URL    = f"{GITHUB_RAW_BASE}/api-v1-position.snapshot.csv"
EQUITY_URL      = f"{GITHUB_RAW_BASE}/derived-equity-curve.csv"


# ──────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────
@dataclass
class BTCBias:
    """Output của BTCBiasAnalyzer."""
    # Hướng bias: "BEARISH" | "BULLISH" | "NEUTRAL"
    direction: str = "NEUTRAL"

    # 0.0 → 1.0  (kết hợp vị thế size, unrealized PnL, equity trend)
    confidence: float = 0.0

    # Từ position snapshot
    position_qty: float = 0.0           # âm = SHORT, dương = LONG
    avg_entry_price: float = 0.0        # key S/R level
    mark_price: float = 0.0
    unrealized_pnl_pct: float = 0.0    # % unrealized P&L
    leverage: float = 0.0

    # Từ equity curve
    account_multiple: float = 52.4      # 52x return → credibility weight
    equity_30d_pct: float = 0.0         # % equity change in last 30 days
    equity_7d_pct: float = 0.0          # % equity change in last 7 days
    regime: str = "UNKNOWN"             # "BULL" | "BEAR" | "SIDEWAYS"

    # Metadata
    last_update: float = 0.0            # unix timestamp
    data_date: str = ""                 # date of position snapshot
    error: Optional[str] = None

    @property
    def age_hours(self) -> float:
        if not self.last_update:
            return 999.0
        return (time.time() - self.last_update) / 3600

    @property
    def is_fresh(self) -> bool:
        return self.age_hours < 26  # dữ liệu < 26h = còn tốt (repo update daily)

    @property
    def bias_emoji(self) -> str:
        return {"BEARISH": "🔴", "BULLISH": "🟢", "NEUTRAL": "⚪"}.get(self.direction, "⚪")

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "confidence": round(self.confidence, 3),
            "position_qty": self.position_qty,
            "avg_entry_price": self.avg_entry_price,
            "mark_price": self.mark_price,
            "unrealized_pnl_pct": round(self.unrealized_pnl_pct * 100, 2),
            "leverage": self.leverage,
            "account_multiple": self.account_multiple,
            "equity_30d_pct": round(self.equity_30d_pct, 2),
            "equity_7d_pct": round(self.equity_7d_pct, 2),
            "regime": self.regime,
            "data_date": self.data_date,
            "last_update_age_h": round(self.age_hours, 1),
            "is_fresh": self.is_fresh,
            "error": self.error,
            # Key level cho chart
            "key_level": round(self.avg_entry_price, 1) if self.avg_entry_price else None,
        }


# ──────────────────────────────────────────────
# Analyzer
# ──────────────────────────────────────────────
class BTCBiasAnalyzer:
    """
    Fetches và phân tích dữ liệu từ github.com/bwjoke/BTC-Trading-Since-2020.

    Paul Wei stats (được xác minh public):
    - BitMEX Hall of Legends, 70x return trong 3 năm
    - 52x adjusted return từ 2020→2026
    - 43k+ orders, chủ yếu XBTUSD inverse perpetual
    - Phong cách: discretionary, K-line driven, long-term compounding
    """

    CREDIBILITY_WEIGHT = 0.85  # 52x return → high weight vs. random signal

    def __init__(self):
        self._bias: BTCBias = BTCBias()
        self._lock = asyncio.Lock()
        self._refresh_interval = 3600  # 1 giờ
        self._last_fetch_ts: float = 0.0

    # ── Public API ──────────────────────────────

    def get_bias(self) -> BTCBias:
        """Trả về bias hiện tại (cached)."""
        return self._bias

    def get_status(self) -> dict:
        b = self._bias
        return {
            "status": "OK" if b.is_fresh and not b.error else ("ERROR" if b.error else "STALE"),
            "direction": b.direction,
            "confidence": round(b.confidence, 3),
            "regime": b.regime,
            "last_update_age_h": round(b.age_hours, 1),
            "data_date": b.data_date,
            "error": b.error,
        }

    async def refresh(self) -> BTCBias:
        """Force refresh từ GitHub."""
        async with self._lock:
            bias = await self._fetch_and_compute()
            self._bias = bias
            self._last_fetch_ts = time.time()
            return bias

    async def run_forever(self):
        """Background loop: refresh mỗi 1 tiếng."""
        logger.info("BTCBiasAnalyzer: starting background refresh loop")
        while True:
            try:
                await self.refresh()
                logger.info(
                    f"BTCBias updated: {self._bias.direction} "
                    f"conf={self._bias.confidence:.2f} "
                    f"entry={self._bias.avg_entry_price:.0f} "
                    f"regime={self._bias.regime}"
                )
            except Exception as e:
                logger.error(f"BTCBiasAnalyzer refresh error: {e}")
                self._bias.error = str(e)

            await asyncio.sleep(self._refresh_interval)

    # ── Signal integration helpers ───────────────

    def get_signal_adjustment(self, signal_direction: str) -> float:
        """
        Trả về hệ số điều chỉnh score cho SignalAggregator.

        - signal_direction: "LONG" hoặc "SHORT"
        - Returns: float, nhân vào score
          +0.3  = tăng 30% (cùng hướng với Paul Wei)
          -0.3  = giảm 30% (ngược hướng với Paul Wei)
           0.0  = không ảnh hưởng (neutral hoặc dữ liệu cũ)
        """
        b = self._bias
        if not b.is_fresh or b.confidence < 0.3 or b.direction == "NEUTRAL":
            return 0.0

        same_direction = (
            (signal_direction == "LONG" and b.direction == "BULLISH") or
            (signal_direction == "SHORT" and b.direction == "BEARISH")
        )

        # Scale adjustment với confidence
        adj = b.confidence * self.CREDIBILITY_WEIGHT * 0.4
        return adj if same_direction else -adj

    def get_score_delta(self, signal_direction: str) -> int:
        """
        Trả về delta (+/-) để cộng vào score trong SignalAggregator.
        """
        adj = self.get_signal_adjustment(signal_direction)
        if adj >= 0.25:
            return 2
        elif adj >= 0.12:
            return 1
        elif adj <= -0.25:
            return -2
        elif adj <= -0.12:
            return -1
        return 0

    # ── Internal fetch & compute ─────────────────

    async def _fetch_and_compute(self) -> BTCBias:
        bias = BTCBias()

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                pos_task = client.get(POSITION_URL)
                eq_task  = client.get(EQUITY_URL)
                pos_resp, eq_resp = await asyncio.gather(pos_task, eq_task)
                pos_resp.raise_for_status()
                eq_resp.raise_for_status()

            self._parse_position(pos_resp.text, bias)
            self._parse_equity_curve(eq_resp.text, bias)
            self._compute_confidence(bias)

        except Exception as e:
            logger.warning(f"BTCBiasAnalyzer fetch failed: {e}")
            bias.error = str(e)
            # Giữ lại giá trị cũ nếu có
            if self._bias.last_update:
                bias.direction        = self._bias.direction
                bias.confidence       = self._bias.confidence * 0.7  # decay khi stale
                bias.avg_entry_price  = self._bias.avg_entry_price
                bias.position_qty     = self._bias.position_qty
                bias.account_multiple = self._bias.account_multiple
                bias.regime           = self._bias.regime
                bias.data_date        = self._bias.data_date

        bias.last_update = time.time()
        return bias

    def _parse_position(self, csv_text: str, bias: BTCBias):
        """Parse api-v1-position.snapshot.csv → XBTUSD row.

        Dùng csv.DictReader để xử lý đúng quoted fields (positionReport chứa JSON).
        """
        def to_float(v: str) -> float:
            try:
                return float(v) if v and v.strip() not in ("", "null", "None") else 0.0
            except (ValueError, TypeError):
                return 0.0

        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            symbol = row.get("symbol", "")
            if "XBTUSD" not in symbol:
                continue

            bias.position_qty       = to_float(row.get("currentQty", ""))
            bias.avg_entry_price    = to_float(row.get("avgEntryPrice", ""))
            bias.mark_price         = to_float(row.get("markPrice", ""))
            bias.unrealized_pnl_pct = to_float(row.get("unrealisedPnlPcnt", ""))
            bias.leverage           = to_float(row.get("leverage", ""))

            ts = row.get("timestamp", "")
            bias.data_date = ts[:10] if ts else ""

            # Direction từ position quantity
            if bias.position_qty < -100_000:        # meaningful short
                bias.direction = "BEARISH"
            elif bias.position_qty > 100_000:        # meaningful long
                bias.direction = "BULLISH"
            else:
                bias.direction = "NEUTRAL"

            break  # chỉ cần XBTUSD row

    def _parse_equity_curve(self, csv_text: str, bias: BTCBias):
        """Parse derived-equity-curve.csv → lấy 30 ngày gần nhất."""
        # Group by date, lấy giá trị cuối cùng mỗi ngày
        by_date: dict[str, float] = {}
        mult_by_date: dict[str, float] = {}

        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            ts = row.get("timestamp", "")
            date_key = ts[:10]
            if not date_key:
                continue

            w_str = row.get("adjustedWealthXBT", "")
            m_str = row.get("adjustedWealthMultipleVsBaseline", "")
            try:
                w = float(w_str) if w_str and w_str.strip() else None
                m = float(m_str) if m_str and m_str.strip() else None
                if w is not None:
                    by_date[date_key] = w
                if m is not None:
                    mult_by_date[date_key] = m
            except ValueError:
                continue

        if not by_date:
            return

        sorted_dates = sorted(by_date.keys())

        # Account multiple (latest)
        latest_date = sorted_dates[-1]
        if latest_date in mult_by_date:
            bias.account_multiple = mult_by_date[latest_date]

        # 7-day change
        if len(sorted_dates) >= 7:
            d7   = by_date[sorted_dates[-7]]
            d_now = by_date[sorted_dates[-1]]
            bias.equity_7d_pct = ((d_now - d7) / d7 * 100) if d7 else 0.0

        # 30-day change
        if len(sorted_dates) >= 30:
            d30   = by_date[sorted_dates[-30]]
            d_now = by_date[sorted_dates[-1]]
            bias.equity_30d_pct = ((d_now - d30) / d30 * 100) if d30 else 0.0

        # Market regime từ equity trend
        eq_7d  = bias.equity_7d_pct
        eq_30d = bias.equity_30d_pct

        if eq_7d > 0.5 and eq_30d > 1.0:
            bias.regime = "BULL"       # tài khoản đang tăng → thị trường thuận
        elif eq_7d < -0.5 and eq_30d < -1.0:
            bias.regime = "BEAR"       # tài khoản đang giảm → thị trường nghịch
        elif abs(eq_30d) < 0.5:
            bias.regime = "SIDEWAYS"
        else:
            bias.regime = "TRANSITION"

    def _compute_confidence(self, bias: BTCBias):
        """
        Tính confidence score [0.0, 1.0] dựa trên:
        - Position size: lớn hơn = chắc hơn
        - Unrealized PnL: đang lời = tăng confidence, đang lỗ nặng = giảm
        - Equity trend 7d/30d: cùng hướng = tăng confidence
        - Direction: NEUTRAL = 0
        """
        if bias.direction == "NEUTRAL":
            bias.confidence = 0.0
            return

        score = 0.0

        # ① Position size (so với max thông thường ~1.3M contracts)
        abs_qty = abs(bias.position_qty)
        if abs_qty > 1_000_000:
            score += 0.35
        elif abs_qty > 500_000:
            score += 0.25
        elif abs_qty > 100_000:
            score += 0.15
        else:
            score += 0.05

        # ② Unrealized PnL signal
        upnl = bias.unrealized_pnl_pct  # -0.034 = -3.4%
        if upnl > 0.02:         # lời >2% → position đang thắng
            score += 0.20
        elif upnl > -0.05:      # lỗ nhỏ < 5% → còn valid
            score += 0.12
        elif upnl > -0.15:      # lỗ 5-15% → giảm confidence
            score += 0.05
        else:                    # lỗ nặng >15% → low confidence
            score += 0.0

        # ③ Equity trend cùng hướng với position
        is_bearish = bias.direction == "BEARISH"
        if is_bearish and bias.equity_7d_pct > 0:
            # Account tăng = short positions đang lời → bullish signal nhưng account BEARISH
            # nghịch lý → giảm confidence
            score -= 0.05
        elif not is_bearish and bias.equity_7d_pct > 0:
            score += 0.10

        # ④ Regime alignment
        regime_boosts = {
            ("BEARISH", "BEAR"):       0.15,
            ("BULLISH", "BULL"):       0.15,
            ("BEARISH", "SIDEWAYS"):   0.05,
            ("BULLISH", "SIDEWAYS"):   0.05,
            ("BEARISH", "TRANSITION"): 0.08,
            ("BULLISH", "TRANSITION"): 0.08,
        }
        score += regime_boosts.get((bias.direction, bias.regime), 0.0)

        # ⑤ Credibility multiplier (account_multiple)
        # 52x → rất credible → giữ nguyên
        # <10x → ít credible → giảm
        mult = bias.account_multiple
        cred = min(1.0, mult / 30.0)  # 30x = full credibility
        score *= cred

        bias.confidence = round(min(1.0, max(0.0, score)), 3)
