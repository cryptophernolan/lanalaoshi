"""
New Listing Pump Scanner
========================
Captures pump setups on newly-listed Binance USDT-M perpetual futures.

Strategy validated on $GENIUS (+40%) — see strategy.md §8:
  1. Find coins listed 12-96h ago (past first-day price discovery)
  2. Detect consolidation: price ranging >12h with <22% range
  3. Check 5 entry conditions:
     ① Consolidation >12h, range <22%
     ② Funding rate < -0.05%  (shorts crowded = coiled spring)
     ③ Current 1h vol < 40% of peak 1h vol  (volume contraction)
     ④ OI stable or slight up (4h change > -5%)
     ⑤ Global L/S ratio < 1.5  (longs not yet euphoric)
  4. Trigger: price breaks above consolidation high + 5m volume 2× spike
  5. Danger signals (for position monitoring):
     - Funding turns positive > +0.05%
     - OI drops >15%
     - L/S > 2.0

Output: NewListingSetup per tracked symbol + TradeSignal on breakout.
"""
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional

from modules.binance_client import BinanceFuturesClient
from modules.schemas import NewListingSetup, TradeSignal, Side, SignalStrength
from config.settings import config

logger = logging.getLogger(__name__)


class NewListingScanner:
    def __init__(self, client: BinanceFuturesClient):
        self.client = client
        self.cfg = config.new_listing
        # symbol → setup (rebuilt each scan)
        self._setups: dict[str, NewListingSetup] = {}
        # symbols that have already fired a breakout signal (avoid duplicates)
        self._triggered: set[str] = set()
        self._last_scan_ts: float = 0.0

    # ──────────────────────────────────────────────
    # PHASE 1: Discover new listings
    # ──────────────────────────────────────────────

    async def _get_new_listings(self) -> list[dict]:
        """
        Query exchangeInfo, return coins listed in [min_age, max_age] hour window.
        """
        try:
            info = await self.client.get_exchange_info()
            now_ms = int(datetime.utcnow().timestamp() * 1000)
            results = []
            for sym_info in info.get("symbols", []):
                symbol = sym_info.get("symbol", "")
                if not symbol.endswith("USDT"):
                    continue
                if sym_info.get("status") != "TRADING":
                    continue
                onboard_ms = sym_info.get("onboardDate", 0)
                if not onboard_ms:
                    continue
                age_h = (now_ms - onboard_ms) / 3_600_000
                if age_h < self.cfg.min_listing_age_hours:
                    continue
                if age_h > self.cfg.max_listing_age_hours:
                    continue
                results.append({
                    "symbol": symbol,
                    "onboard_ms": onboard_ms,
                    "age_hours": age_h,
                })
            return results
        except Exception as e:
            logger.warning(f"NewListing: exchangeInfo failed: {e}")
            return []

    # ──────────────────────────────────────────────
    # PHASE 2: Consolidation detection
    # ──────────────────────────────────────────────

    def _detect_consolidation(
        self, klines_1h: list
    ) -> Optional[tuple[float, float, float, float]]:
        """
        Find the longest contiguous window ending at NOW where price range < threshold.

        Returns (high, low, range_pct, hours) or None.

        Algorithm: extend window backwards from current candle until range exceeds
        threshold. The longest tight window = consolidation period.
        """
        cfg = self.cfg
        min_candles = int(cfg.min_consolidation_hours)
        max_range = cfg.max_consolidation_range_pct

        best_start: Optional[int] = None

        for look_back in range(min_candles, len(klines_1h)):
            start_idx = len(klines_1h) - look_back
            window = klines_1h[start_idx:]
            highs = [float(k[2]) for k in window]
            lows  = [float(k[3]) for k in window]
            w_high = max(highs)
            w_low  = min(lows)
            if w_low == 0:
                break
            range_pct = (w_high - w_low) / w_low * 100
            if range_pct < max_range:
                best_start = start_idx
            else:
                break  # extending further only widens range

        if best_start is None:
            return None

        window = klines_1h[best_start:]
        highs = [float(k[2]) for k in window]
        lows  = [float(k[3]) for k in window]
        w_high = max(highs)
        w_low  = min(lows)
        range_pct = (w_high - w_low) / w_low * 100
        hours = float(len(window))
        return (w_high, w_low, range_pct, hours)

    # ──────────────────────────────────────────────
    # PHASE 3: Analyze one symbol
    # ──────────────────────────────────────────────

    async def _analyze_symbol(
        self, symbol: str, listing_time: datetime, age_hours: float
    ) -> Optional[NewListingSetup]:
        """Full 5-condition analysis for one new listing."""
        try:
            # Parallel fetch
            r_klines1h, r_funding, r_oi, r_ls = await asyncio.gather(
                self.client.get_klines(symbol, "1h", 96),
                self.client.get_funding_rate(symbol),
                self.client.get_open_interest_hist(symbol, "1h", 6),
                self.client.get_global_long_short_ratio(symbol, "1h", 3),
                return_exceptions=True,
            )

            if isinstance(r_klines1h, Exception) or not r_klines1h or len(r_klines1h) < 12:
                return None

            setup = NewListingSetup(
                symbol=symbol,
                listing_time=listing_time,
                listing_age_hours=round(age_hours, 1),
                timestamp=datetime.utcnow(),
            )
            setup.current_price = float(r_klines1h[-1][4])

            # ── ① Consolidation ──────────────────────────────
            cons = self._detect_consolidation(r_klines1h)
            if cons:
                ch, cl, cr, chours = cons
                setup.consolidation_high  = ch
                setup.consolidation_low   = cl
                setup.consolidation_range_pct = round(cr, 2)
                setup.consolidation_hours = chours
                setup.cond_consolidation  = (
                    chours >= self.cfg.min_consolidation_hours
                    and cr < self.cfg.max_consolidation_range_pct
                )

            # ── ② Funding rate ───────────────────────────────
            if not isinstance(r_funding, Exception):
                fr = float(r_funding.get("lastFundingRate", 0))
                setup.funding_rate  = fr
                setup.cond_funding  = fr < self.cfg.funding_rate_max

            # ── ③ Volume contraction ─────────────────────────
            vols = [float(k[5]) for k in r_klines1h]   # base asset volume (1h)
            # Peak = max of first 24h bars (or all if fewer)
            first_24 = vols[:24] if len(vols) >= 24 else vols
            peak_vol = max(first_24) if first_24 else 0.0
            cur_vol  = vols[-1] if vols else 0.0
            setup.peak_volume_1h    = peak_vol
            setup.current_volume_1h = cur_vol
            if peak_vol > 0:
                ratio = cur_vol / peak_vol
                setup.volume_ratio  = round(ratio, 3)
                setup.cond_volume   = ratio < self.cfg.volume_contraction_ratio

            # ── ④ OI stability ───────────────────────────────
            if not isinstance(r_oi, Exception) and r_oi and len(r_oi) >= 3:
                oi_old = float(r_oi[-4]["sumOpenInterestValue"]) if len(r_oi) >= 4 else float(r_oi[0]["sumOpenInterestValue"])
                oi_now = float(r_oi[-1]["sumOpenInterestValue"])
                if oi_old > 0:
                    oi_chg = (oi_now - oi_old) / oi_old * 100
                    setup.oi_change_4h_pct = round(oi_chg, 2)
                    # Stable = not sharply dropping (can be flat or slightly up)
                    setup.cond_oi_stable = oi_chg > -self.cfg.oi_stability_max_drop_pct
                else:
                    setup.cond_oi_stable = True  # no baseline → assume OK
            else:
                setup.cond_oi_stable = True  # unavailable → don't block

            # ── ⑤ Global L/S ratio ──────────────────────────
            if not isinstance(r_ls, Exception) and r_ls:
                latest = r_ls[-1]
                ls = float(latest.get("longShortRatio", 0))
                if ls > 0:
                    setup.ls_ratio     = round(ls, 3)
                    setup.cond_ls_ratio = ls < self.cfg.max_ls_ratio
                else:
                    setup.ls_ratio     = None
                    setup.cond_ls_ratio = True   # no data → don't block
            else:
                setup.ls_ratio     = None
                setup.cond_ls_ratio = True  # endpoint unavailable for new coin

            # ── Computed summary ─────────────────────────────
            met = sum([
                setup.cond_consolidation,
                setup.cond_funding,
                setup.cond_volume,
                setup.cond_oi_stable,
                setup.cond_ls_ratio,
            ])
            setup.conditions_met    = met
            setup.all_conditions_met = (met == 5)

            # ── Status ───────────────────────────────────────
            if symbol in self._triggered:
                # Already fired — check danger signals
                setup.status = "TRIGGERED"
                dangers = self._check_danger_signals(setup)
                setup.danger_signals = dangers
                if dangers:
                    setup.status = "DANGER"
            elif setup.all_conditions_met:
                setup.status = "READY"
            else:
                setup.status = "WATCHING"

            return setup

        except Exception as e:
            logger.warning(f"NewListing analyze {symbol}: {e}")
            return None

    # ──────────────────────────────────────────────
    # Danger signal checker
    # ──────────────────────────────────────────────

    def _check_danger_signals(self, setup: NewListingSetup) -> list[str]:
        dangers: list[str] = []
        if setup.funding_rate > self.cfg.danger_funding_positive:
            dangers.append(f"FUNDING+ {setup.funding_rate*100:.3f}%")
        if setup.oi_change_4h_pct < -self.cfg.danger_oi_drop_pct:
            dangers.append(f"OI_DUMP {setup.oi_change_4h_pct:.1f}%")
        if setup.ls_ratio is not None and setup.ls_ratio > self.cfg.danger_ls_max:
            dangers.append(f"LS_HIGH {setup.ls_ratio:.2f}")
        return dangers

    # ──────────────────────────────────────────────
    # PHASE 4: Breakout detection
    # ──────────────────────────────────────────────

    async def _check_breakout_volume(
        self, symbol: str, cons_high: float
    ) -> tuple[bool, float]:
        """
        Returns (breakout_confirmed, current_price).
        Breakout = price > cons_high AND latest 5m vol > 2× average of prior 11 candles.
        """
        try:
            klines = await self.client.get_klines(symbol, "5m", 12)
            if not klines or len(klines) < 3:
                return False, 0.0
            cur_price = float(klines[-1][4])
            if cur_price <= cons_high:
                return False, cur_price
            vols = [float(k[5]) for k in klines]
            cur_vol = vols[-1]
            avg_vol = sum(vols[:-1]) / len(vols[:-1]) if len(vols) > 1 else 0
            confirmed = avg_vol > 0 and cur_vol > avg_vol * self.cfg.breakout_volume_multiplier
            return confirmed, cur_price
        except Exception as e:
            logger.debug(f"Breakout check {symbol}: {e}")
            return False, 0.0

    # ──────────────────────────────────────────────
    # Signal builder
    # ──────────────────────────────────────────────

    def _build_signal(
        self, setup: NewListingSetup, account_balance: float
    ) -> TradeSignal:
        entry    = setup.current_price
        mid      = (setup.consolidation_high + setup.consolidation_low) / 2
        sl       = mid * (1 - self.cfg.stop_loss_below_mid_pct / 100)
        tp       = entry * (1 + self.cfg.tp2_pct / 100)
        stop_dist = abs(entry - sl)
        rr       = abs(tp - entry) / stop_dist if stop_dist > 0 else 0

        # Position size: risk 1% of balance with stop distance
        risk_usdt = account_balance * (config.risk.position_risk_pct / 100)
        stop_pct  = stop_dist / entry if entry > 0 else 0.03
        size_usdt = min(
            risk_usdt / stop_pct if stop_pct > 0 else 0,
            config.risk.max_position_size_usdt,
        )

        cond_str = (
            f"{'✓' if setup.cond_consolidation else '✗'}Cons {setup.consolidation_hours:.0f}h "
            f"{setup.consolidation_range_pct:.1f}% | "
            f"{'✓' if setup.cond_funding else '✗'}Fund {setup.funding_rate*100:+.3f}% | "
            f"{'✓' if setup.cond_volume else '✗'}Vol {setup.volume_ratio:.0%} | "
            f"{'✓' if setup.cond_oi_stable else '✗'}OI {setup.oi_change_4h_pct:+.1f}% | "
            f"{'✓' if setup.cond_ls_ratio else '✗'}L/S "
            f"{'N/A' if setup.ls_ratio is None else f'{setup.ls_ratio:.2f}'}"
        )

        return TradeSignal(
            symbol=setup.symbol,
            side=Side.LONG,
            strength=SignalStrength.STRONG,
            signal_type="NEW_LISTING_PUMP",
            entry_price=entry,
            suggested_size_usdt=round(size_usdt, 2),
            leverage=min(config.risk.max_leverage, 3),
            stop_loss=round(sl, 6),
            take_profit=round(tp, 6),
            risk_reward_ratio=round(rr, 2),
            reasoning=f"NEW_LISTING_PUMP | Listed {setup.listing_age_hours:.0f}h ago | {cond_str}",
            confidence=min(0.65 + setup.conditions_met * 0.05, 0.95),
            timestamp=datetime.utcnow(),
            signal_id="NL-" + str(uuid.uuid4())[:6],
        )

    # ──────────────────────────────────────────────
    # Main scan loop
    # ──────────────────────────────────────────────

    async def scan(
        self,
        account_balance: float = 10_000.0,
        price_cache: Optional[dict] = None,
    ) -> tuple[dict[str, NewListingSetup], list[TradeSignal]]:
        """
        Discover new listings → analyze conditions → detect breakouts.
        Returns (setups_by_symbol, new_signals).
        """
        import time
        self._last_scan_ts = time.time()
        price_cache = price_cache or {}

        new_listings = await self._get_new_listings()
        if not new_listings:
            logger.debug("NewListing: no listings in age window")
            return self._setups, []

        # Analyze all in parallel (bounded by asyncio.gather)
        tasks = [
            self._analyze_symbol(
                l["symbol"],
                datetime.utcfromtimestamp(l["onboard_ms"] / 1000),
                l["age_hours"],
            )
            for l in new_listings
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        new_setups: dict[str, NewListingSetup] = {}
        signals: list[TradeSignal] = []

        # Cleanup triggered set — remove symbols no longer in age window
        live_symbols = {l["symbol"] for l in new_listings}
        self._triggered = {s for s in self._triggered if s in live_symbols}

        for listing, result in zip(new_listings, results):
            if isinstance(result, Exception) or result is None:
                continue
            symbol = listing["symbol"]
            setup = result

            # Override price from real-time cache if available
            if symbol in price_cache and price_cache[symbol]:
                setup.current_price = price_cache[symbol]

            # Breakout check — only for READY setups not yet triggered
            if setup.all_conditions_met and symbol not in self._triggered:
                triggered, bp = await self._check_breakout_volume(
                    symbol, setup.consolidation_high
                )
                if bp > 0:
                    setup.current_price = bp
                if triggered:
                    setup.status       = "TRIGGERED"
                    setup.triggered_at = datetime.utcnow()
                    self._triggered.add(symbol)
                    logger.info(
                        f"[NEW_LISTING_PUMP] BREAKOUT: {symbol} @ {bp:.6f} "
                        f"(cons_high={setup.consolidation_high:.6f}, "
                        f"conditions={setup.conditions_met}/5)"
                    )
                    signals.append(self._build_signal(setup, account_balance))

            new_setups[symbol] = setup

        self._setups = new_setups

        n_ready     = sum(1 for s in new_setups.values() if s.status == "READY")
        n_triggered = sum(1 for s in new_setups.values() if s.status in ("TRIGGERED", "DANGER"))
        logger.info(
            f"NewListingScanner: {len(new_setups)} coins tracked | "
            f"{n_ready} READY | {n_triggered} TRIGGERED"
        )
        return new_setups, signals

    def get_danger_symbols(self) -> list[str]:
        """Symbols with open triggered positions that hit danger signals."""
        return [
            sym for sym, s in self._setups.items()
            if sym in self._triggered and s.danger_signals
        ]

    async def run_forever(self, callback, price_streamer=None):
        while True:
            try:
                balance = 10_000.0
                try:
                    account = await self.client.get_account()
                    balance = float(account.get("totalWalletBalance", 10_000))
                except Exception:
                    pass

                price_cache = {}
                if price_streamer is not None:
                    price_cache = price_streamer.get_all_prices()

                setups, signals = await self.scan(
                    account_balance=balance,
                    price_cache=price_cache,
                )
                if signals:
                    await callback(signals)

            except Exception as e:
                logger.error(f"NewListing loop error: {e}", exc_info=True)
            await asyncio.sleep(self.cfg.scan_interval_seconds)
