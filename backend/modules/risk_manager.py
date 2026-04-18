"""
Risk Manager — layer cuối cùng quyết định có execute signal hay không.

Kiểm tra:
1. Account balance sufficient?
2. Số positions hiện tại < max?
3. Total exposure < max?
4. Daily loss < threshold?
5. Emergency stop active?
6. BTC crash detection?
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from modules.schemas import TradeSignal, Position, Side
from modules.binance_client import BinanceFuturesClient
from config.settings import config

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, client: BinanceFuturesClient):
        self.client = client
        self.cfg = config.risk
        
        # State
        self._day_start_balance: Optional[float] = None
        self._day_start_time: Optional[datetime] = None
        self._peak_balance: float = 0.0
        self._kill_switch_triggered: bool = False
        self._kill_reason: Optional[str] = None
    
    async def _get_account_balance(self) -> float:
        try:
            account = await self.client.get_account()
            balance = float(account.get("totalWalletBalance", 0))
            if balance > 0:
                return balance
        except Exception as e:
            logger.debug(f"Can't get account balance (no API key?): {e}")
        # Dry-run fallback: simulate 10,000 USDT so signals pass risk checks
        if config.executor.dry_run:
            return 10_000.0
        return 0
    
    async def _get_open_positions(self) -> list[dict]:
        try:
            positions = await self.client.get_positions()
            return [p for p in positions if float(p.get("positionAmt", 0)) != 0]
        except Exception as e:
            logger.debug(f"Can't get positions (no API key?): {e}")
            return []
    
    async def _reset_daily_if_needed(self):
        now = datetime.utcnow()
        if self._day_start_time is None or (now - self._day_start_time).days >= 1:
            self._day_start_balance = await self._get_account_balance()
            self._day_start_time = now
            logger.info(f"Daily reset: start balance = {self._day_start_balance}")
    
    async def _check_btc_crash(self) -> bool:
        """BTC giảm >5% trong 1h = crash → không trade."""
        try:
            klines = await self.client.get_klines("BTCUSDT", "5m", 12)
            if len(klines) < 2:
                return False
            start_price = float(klines[0][1])
            current_price = float(klines[-1][4])
            change_pct = ((current_price - start_price) / start_price) * 100
            if change_pct < -5:
                logger.warning(f"BTC crash detected: {change_pct:.2f}% in 1h")
                return True
            return False
        except Exception as e:
            logger.error(f"BTC crash check failed: {e}")
            return False
    
    async def evaluate(self, signal: TradeSignal) -> tuple[bool, str]:
        """
        Trả về (approved: bool, reason: str).
        approved=False nghĩa là reject signal.
        """
        # 1. Manual emergency stop
        if self.cfg.emergency_stop or self._kill_switch_triggered:
            return False, f"Emergency stop: {self._kill_reason or 'manual'}"
        
        # 2. BTC crash
        if self.cfg.auto_close_on_crash:
            if await self._check_btc_crash():
                return False, "BTC crash detected, trading halted"
        
        await self._reset_daily_if_needed()
        
        # 3. Account balance
        balance = await self._get_account_balance()
        if balance <= 0:
            return False, "Zero balance"
        
        # Update peak
        if balance > self._peak_balance:
            self._peak_balance = balance
        
        # 4. Max drawdown
        if self._peak_balance > 0:
            drawdown_pct = ((self._peak_balance - balance) / self._peak_balance) * 100
            if drawdown_pct > self.cfg.max_drawdown_pct:
                self._kill_switch_triggered = True
                self._kill_reason = f"Max drawdown hit: {drawdown_pct:.2f}%"
                return False, self._kill_reason
        
        # 5. Daily loss limit
        if self._day_start_balance and self._day_start_balance > 0:
            daily_change_pct = ((balance - self._day_start_balance) / self._day_start_balance) * 100
            if daily_change_pct < -self.cfg.max_daily_loss_pct:
                self._kill_switch_triggered = True
                self._kill_reason = f"Daily loss limit: {daily_change_pct:.2f}%"
                return False, self._kill_reason
        
        # 6. Max concurrent positions
        positions = await self._get_open_positions()
        if len(positions) >= self.cfg.max_concurrent_positions:
            return False, f"Max positions reached ({len(positions)})"
        
        # 7. Check if already in this symbol
        for p in positions:
            if p["symbol"] == signal.symbol:
                return False, f"Already have position in {signal.symbol}"
        
        # 8. Total exposure
        total_exposure = sum(
            abs(float(p.get("notional", 0))) for p in positions
        )
        new_exposure = signal.suggested_size_usdt * signal.leverage
        total_after = total_exposure + new_exposure
        max_exposure = balance * (self.cfg.max_total_exposure_pct / 100)
        if total_after > max_exposure:
            return False, f"Exposure limit: {total_after:.0f} > {max_exposure:.0f}"
        
        # 9. Position size sanity
        if signal.suggested_size_usdt > self.cfg.max_position_size_usdt:
            return False, f"Size too big: {signal.suggested_size_usdt}"
        
        # 10. Leverage sanity
        if signal.leverage > self.cfg.max_leverage:
            return False, f"Leverage too high: {signal.leverage}"
        
        # 11. Risk/reward minimum
        if signal.risk_reward_ratio < 1.5:
            return False, f"R:R too low: {signal.risk_reward_ratio:.2f}"
        
        return True, "Approved"
    
    def trigger_kill_switch(self, reason: str):
        self._kill_switch_triggered = True
        self._kill_reason = reason
        logger.critical(f"KILL SWITCH TRIGGERED: {reason}")
    
    def reset_kill_switch(self):
        self._kill_switch_triggered = False
        self._kill_reason = None
        logger.info("Kill switch reset")
    
    @property
    def is_killed(self) -> bool:
        return self._kill_switch_triggered
