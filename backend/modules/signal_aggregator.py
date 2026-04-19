"""
Signal Aggregator — kết hợp OI divergence + sentiment + CatTrade thành TradeSignal.

Logic:
- OI divergence là PRIMARY signal
- Sentiment là CONFIRMATION (boost confidence)
- CatTrade là MULTI-TIMEFRAME + STRUCTURE confirmation
- Không có OI → không signal, dù sentiment/cattrade mạnh
"""
import logging
import uuid
from datetime import datetime
from typing import Optional

from modules.schemas import (
    OIDivergence, SentimentScore, TradeSignal, Side, SignalStrength
)
from modules.cattrade_scraper import CattradeSignal
from config.settings import config

# BTCBiasAnalyzer import (optional – injected at runtime)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from modules.btc_bias_analyzer import BTCBiasAnalyzer


logger = logging.getLogger(__name__)


class SignalAggregator:
    def __init__(self, btc_bias_analyzer=None):
        self.oi_cfg = config.oi_scanner
        self.risk_cfg = config.risk
        self.bias_cfg = config.btc_bias
        self._recent_signals: dict[tuple[str, str], datetime] = {}  # (symbol, direction) → last signal time
        self._cooldown_minutes = 60  # per-direction cooldown (60 min)
        # BTCBiasAnalyzer instance (injected từ main.py)
        self._btc_bias = btc_bias_analyzer
    
    def _base_symbol(self, pair_symbol: str) -> str:
        return pair_symbol.replace("USDT", "")
    
    def _is_in_cooldown(self, symbol: str, direction: "Side | None" = None) -> bool:
        """
        Cooldown per (symbol, direction) — cho phép reverse direction signal sau 10 phút,
        nhưng cùng direction phải đợi đủ 60 phút.
        """
        if direction is None:
            # Legacy check — không biết direction → dùng worst-case (longest remaining)
            for key, last in self._recent_signals.items():
                if key[0] == symbol:
                    delta = (datetime.utcnow() - last).total_seconds() / 60
                    if delta < self._cooldown_minutes:
                        return True
            return False

        key = (symbol, direction.value)
        last = self._recent_signals.get(key)
        if not last:
            return False
        delta = (datetime.utcnow() - last).total_seconds() / 60
        return delta < self._cooldown_minutes
    
    def _determine_strength(
        self,
        divergence: OIDivergence,
        sentiment: Optional[SentimentScore],
        cattrade: Optional[CattradeSignal] = None,
    ) -> tuple[SignalStrength, int]:
        """Returns (strength, score) để dễ debug."""
        score = 0

        # ── OI ratio ──
        if divergence.divergence_ratio >= 8:
            score += 3
        elif divergence.divergence_ratio >= 5:
            score += 2
        else:
            score += 1

        # ── Direction confidence ──
        if divergence.confidence >= 0.8:
            score += 2
        elif divergence.confidence >= 0.65:
            score += 1

        # ── Sentiment ──
        if sentiment:
            if sentiment.composite_score >= 60:
                score += 2
            elif sentiment.composite_score >= 30:
                score += 1

        # ── Volume ──
        if divergence.volume_24h_usdt >= 500_000_000:
            score += 1

        # ── CatTrade multi-timeframe + structure ──
        if cattrade:
            signal_side = divergence.direction  # Side.LONG or Side.SHORT

            # Multi-timeframe OI confirmation (1h + 4h in rankings)
            tf_set = set(cattrade.timeframe_rankings)
            if "1h" in tf_set and "4h" in tf_set:
                score += 2
            elif len(tf_set) >= 2:
                score += 1

            # Multi-window direction consistency matches signal
            ct_bias = cattrade.direction_bias  # +1, -1, 0
            signal_bias = 1 if signal_side == Side.LONG else -1
            if ct_bias != 0 and ct_bias == signal_bias:
                score += 2  # consistent direction across multiple windows
            elif ct_bias != 0 and ct_bias != signal_bias:
                score -= 1  # direction conflict → weaken signal

            # Structure pattern (大户 whale leading) matches signal
            from modules.cattrade_scraper import _structure_bias_fuzzy
            struct_bias = _structure_bias_fuzzy(cattrade.structure_pattern or "")
            if struct_bias != 0 and struct_bias == signal_bias:
                score += 2  # whale accumulation matches direction
            elif struct_bias != 0 and struct_bias != signal_bias:
                score -= 1  # whale going opposite direction

            # High anomaly score in multi-window OI
            best_anomaly = max(cattrade.oi_vol_anomaly_score, cattrade.oi_val_anomaly_score)
            if best_anomaly >= 10:
                score += 1

            # Market share anomaly
            if cattrade.market_share_score >= 20:
                score += 1

        # ── BTCBias (Smart Money) ──
        # BTC: full delta. Alts: half delta (BTC correlation ~70% → signal masih relevant).
        if self._btc_bias and self.bias_cfg.enabled:
            bias = self._btc_bias.get_bias()
            if bias.is_fresh and bias.direction != "NEUTRAL":
                signal_dir_str = "LONG" if divergence.direction == Side.LONG else "SHORT"
                delta = self._btc_bias.get_score_delta(signal_dir_str)

                # Fix 6: alts nhận half delta (int truncate → nhỏ hơn 0 cũng bị giảm)
                is_btc_symbol = divergence.symbol in ("BTCUSDT", "XBTUSD")
                if not is_btc_symbol:
                    delta = int(delta / 2)  # 2→1, 1→0, -2→-1, -1→0

                # Fix 8: freshness decay — data > 30 phút thì giảm confidence
                # (Paul Wei có thể đã flip position trong vòng 1h refresh interval)
                age_decay = (
                    max(0.0, 1.0 - (bias.age_hours - 0.5) / 25.5)
                    if bias.age_hours > 0.5 else 1.0
                )
                effective_confidence = bias.confidence * age_decay

                same = (
                    (signal_dir_str == "LONG"  and bias.direction == "BULLISH") or
                    (signal_dir_str == "SHORT" and bias.direction == "BEARISH")
                )
                if same and effective_confidence >= self.bias_cfg.min_confidence_to_boost:
                    score += min(delta, self.bias_cfg.max_score_delta)
                elif not same and effective_confidence >= self.bias_cfg.min_confidence_to_suppress:
                    score += max(delta, -self.bias_cfg.max_score_delta)

        if score >= 7:
            return SignalStrength.STRONG, score
        elif score >= 4:
            return SignalStrength.MEDIUM, score
        return SignalStrength.WEAK, score
    
    def _cattrade_hard_veto(
        self,
        divergence: OIDivergence,
        cattrade: Optional[CattradeSignal],
    ) -> bool:
        """
        Hard veto khi CatTrade đồng thời conflict trên CẢ 3 dimension:
          - direction_bias (multi-window average)
          - structure_pattern (whale activity)
          - multi-timeframe confirmation (≥2 TFs)

        Một điểm conflict (-1đ) không đủ để veto — phải cả 3 cùng nói ngược.
        """
        if not cattrade:
            return False

        signal_bias = 1 if divergence.direction == Side.LONG else -1
        ct_bias = cattrade.direction_bias

        from modules.cattrade_scraper import _structure_bias_fuzzy
        struct_bias = _structure_bias_fuzzy(cattrade.structure_pattern or "")

        direction_conflict  = ct_bias != 0 and ct_bias != signal_bias
        structure_conflict  = struct_bias != 0 and struct_bias != signal_bias
        multi_tf_confirmed  = len(cattrade.timeframe_rankings) >= 2

        if direction_conflict and structure_conflict and multi_tf_confirmed:
            logger.info(
                f"CatTrade HARD VETO {divergence.symbol}: "
                f"signal={'+' if signal_bias > 0 else '-'} "
                f"ct_dir={ct_bias} struct={struct_bias} "
                f"TF={cattrade.timeframe_rankings}"
            )
            return True
        return False

    def _compute_position_size(
        self, strength: SignalStrength, account_balance: float
    ) -> tuple[float, int]:
        """Trả về (size_usdt, leverage) dựa trên strength."""
        base_size = min(
            self.risk_cfg.max_position_size_usdt,
            account_balance * (self.risk_cfg.position_risk_pct / 100) * 33
            # risk 1% với SL 3% → notional = balance * 33 * 1%
        )
        
        if strength == SignalStrength.STRONG:
            return base_size, min(self.risk_cfg.max_leverage, 5)
        elif strength == SignalStrength.MEDIUM:
            return base_size * 0.6, min(self.risk_cfg.max_leverage, 3)
        return base_size * 0.3, 2
    
    def _compute_stops(
        self, entry: float, side: Side
    ) -> tuple[float, float]:
        sl_pct = self.risk_cfg.default_stop_loss_pct / 100
        tp_pct = self.risk_cfg.default_take_profit_pct / 100
        
        if side == Side.LONG:
            sl = entry * (1 - sl_pct)
            tp = entry * (1 + tp_pct)
        else:
            sl = entry * (1 + sl_pct)
            tp = entry * (1 - tp_pct)
        return sl, tp
    
    def aggregate(
        self,
        divergences: list[OIDivergence],
        sentiments: dict[str, SentimentScore],
        account_balance: float = 10_000.0,
        current_prices: Optional[dict[str, float]] = None,
        cattrades: Optional[dict[str, CattradeSignal]] = None,
    ) -> list[TradeSignal]:
        """
        Input:
            divergences:  từ OIScanner
            sentiments:   từ SentimentScraper (key = base symbol, no USDT)
            cattrades:    từ CattradeScraper  (key = base symbol, no USDT)
            account_balance: từ Binance account
            current_prices: map {symbol: price} để tính entry
        Output: list of TradeSignals (đã filter + ranked)
        """
        signals: list[TradeSignal] = []
        current_prices = current_prices or {}
        cattrades = cattrades or {}

        for div in divergences:
            if self._is_in_cooldown(div.symbol, div.direction):
                logger.debug(f"{div.symbol} {div.direction.value} in cooldown, skip")
                continue

            base = self._base_symbol(div.symbol)
            sentiment = sentiments.get(base)
            cattrade = cattrades.get(base)

            # Fix 5: CatTrade hard veto — tất cả 3 dimension đều ngược → skip
            if self._cattrade_hard_veto(div, cattrade):
                continue

            strength, score = self._determine_strength(div, sentiment, cattrade)

            # In dry_run: show all signals including WEAK for monitoring
            if strength == SignalStrength.WEAK and not config.executor.dry_run:
                continue

            entry = current_prices.get(div.symbol, 0)
            if entry == 0:
                continue

            size_usdt, leverage = self._compute_position_size(strength, account_balance)
            sl, tp = self._compute_stops(entry, div.direction)

            rr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0

            reasoning_parts = [
                f"OI {div.oi_change_pct:+.1f}% vs Price {div.price_change_pct:+.1f}% "
                f"(ratio {div.divergence_ratio:.1f})",
                f"Funding {div.funding_rate*100:.3f}%, Taker ratio {div.taker_ratio:.2f}",
            ]
            if sentiment:
                reasoning_parts.append(
                    f"Sentiment {sentiment.composite_score:.0f} "
                    f"(sq={sentiment.square_mentions})"
                )
            if cattrade:
                ct_parts = []
                if cattrade.timeframe_rankings:
                    ct_parts.append(f"TF={'+'.join(cattrade.timeframe_rankings)}")
                if cattrade.oi_vol_direction:
                    ct_parts.append(f"OI={cattrade.oi_vol_direction}")
                if cattrade.structure_pattern:
                    ct_parts.append(f"Struct={cattrade.structure_pattern}")
                if ct_parts:
                    reasoning_parts.append(f"CatTrade[{' '.join(ct_parts)}] score={score}")

            signal = TradeSignal(
                symbol=div.symbol,
                side=div.direction,
                strength=strength,
                entry_price=entry,
                suggested_size_usdt=size_usdt,
                leverage=leverage,
                stop_loss=sl,
                take_profit=tp,
                risk_reward_ratio=rr,
                oi_divergence=div,
                sentiment=sentiment,
                reasoning=" | ".join(reasoning_parts),
                confidence=div.confidence,
                timestamp=datetime.utcnow(),
                signal_id=str(uuid.uuid4())[:8],
            )
            signals.append(signal)
            self._recent_signals[(div.symbol, div.direction.value)] = datetime.utcnow()
        
        # Sort by strength + confidence
        strength_order = {
            SignalStrength.STRONG: 3,
            SignalStrength.MEDIUM: 2,
            SignalStrength.WEAK: 1,
        }
        signals.sort(
            key=lambda s: (strength_order[s.strength], s.confidence),
            reverse=True,
        )
        
        return signals
