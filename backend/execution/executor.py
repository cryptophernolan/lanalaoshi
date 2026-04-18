"""
Executor — đặt lệnh thực tế lên Binance Futures.

QUAN TRỌNG:
- DRY_RUN mode không thực sự đặt lệnh, chỉ log
- Mọi entry PHẢI kèm SL + TP orders (bracket)
- Trailing stop được quản lý tự động trong PositionTracker
"""
import logging
import asyncio
from datetime import datetime
from typing import Optional

from modules.schemas import TradeSignal, Position, Side, ClosedTrade
from modules.binance_client import BinanceFuturesClient
from config.settings import config

logger = logging.getLogger(__name__)


class Executor:
    def __init__(self, client: BinanceFuturesClient):
        self.client = client
        self.cfg = config.executor
        self._symbol_info: dict[str, dict] = {}
        self._order_times: list[datetime] = []
    
    async def initialize(self):
        """Cache symbol info (step size, min qty...)."""
        info = await self.client.get_exchange_info()
        for s in info.get("symbols", []):
            self._symbol_info[s["symbol"]] = s
        logger.info(f"Executor: loaded {len(self._symbol_info)} symbol infos")
    
    def _round_quantity(self, symbol: str, qty: float) -> float:
        """Round theo stepSize của symbol."""
        info = self._symbol_info.get(symbol)
        if not info:
            return round(qty, 3)
        
        for f in info.get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                step_size = float(f["stepSize"])
                precision = 0
                s = f"{step_size:f}".rstrip("0")
                if "." in s:
                    precision = len(s.split(".")[1])
                return round(qty - (qty % step_size), precision)
        return round(qty, 3)
    
    def _round_price(self, symbol: str, price: float) -> float:
        info = self._symbol_info.get(symbol)
        if not info:
            return round(price, 4)
        
        for f in info.get("filters", []):
            if f["filterType"] == "PRICE_FILTER":
                tick_size = float(f["tickSize"])
                precision = 0
                s = f"{tick_size:f}".rstrip("0")
                if "." in s:
                    precision = len(s.split(".")[1])
                return round(price - (price % tick_size), precision)
        return round(price, 4)
    
    def _check_rate_limit(self) -> bool:
        now = datetime.utcnow()
        self._order_times = [
            t for t in self._order_times
            if (now - t).total_seconds() < 60
        ]
        return len(self._order_times) < self.cfg.max_orders_per_minute
    
    async def execute_signal(self, signal: TradeSignal) -> Optional[Position]:
        """
        Đặt bracket order: entry + SL + TP.
        Trả về Position nếu thành công, None nếu fail.
        """
        if not self._check_rate_limit():
            logger.warning("Rate limit hit, skipping")
            return None
        
        # Size cần confirm manual?
        if signal.suggested_size_usdt > self.cfg.require_manual_confirm_above_usdt:
            logger.warning(
                f"Signal {signal.signal_id} size {signal.suggested_size_usdt} "
                f"requires manual confirm, skipping auto-execute"
            )
            return None
        
        # Calculate quantity
        qty_raw = (signal.suggested_size_usdt * signal.leverage) / signal.entry_price
        qty = self._round_quantity(signal.symbol, qty_raw)
        if qty <= 0:
            logger.error(f"Qty {qty} <= 0 after rounding, skip")
            return None
        
        sl_price = self._round_price(signal.symbol, signal.stop_loss)
        tp_price = self._round_price(signal.symbol, signal.take_profit)
        
        side_str = "BUY" if signal.side == Side.LONG else "SELL"
        opposite_side = "SELL" if signal.side == Side.LONG else "BUY"
        
        if self.cfg.dry_run:
            logger.info(
                f"[DRY RUN] Would execute: {signal.side.value} {qty} {signal.symbol} "
                f"@ ~{signal.entry_price} | SL {sl_price} | TP {tp_price} "
                f"| Leverage {signal.leverage}x | Size ${signal.suggested_size_usdt:.0f}"
            )
            self._order_times.append(datetime.utcnow())
            return Position(
                symbol=signal.symbol,
                side=signal.side,
                entry_price=signal.entry_price,
                current_price=signal.entry_price,
                size_usdt=signal.suggested_size_usdt,
                leverage=signal.leverage,
                stop_loss=sl_price,
                take_profit=tp_price,
                unrealized_pnl_usdt=0,
                unrealized_pnl_pct=0,
                opened_at=datetime.utcnow(),
                signal_id=signal.signal_id,
                entry_order_id="DRY_RUN",
                sl_order_id="DRY_RUN_SL",
                tp_order_id="DRY_RUN_TP",
            )
        
        # REAL EXECUTION
        try:
            # 1. Set leverage
            await self.client.set_leverage(signal.symbol, signal.leverage)
            
            # 2. Market entry
            entry_order = await self.client.place_order(
                symbol=signal.symbol,
                side=side_str,
                order_type="MARKET",
                quantity=qty,
            )
            entry_id = str(entry_order.get("orderId"))
            avg_price = float(entry_order.get("avgPrice", signal.entry_price))
            if avg_price == 0:
                avg_price = signal.entry_price
            logger.info(f"Entry filled: {entry_order}")
            
            # 3. Stop loss (STOP_MARKET, reduce_only, closePosition)
            sl_order = await self.client.place_order(
                symbol=signal.symbol,
                side=opposite_side,
                order_type="STOP_MARKET",
                quantity=qty,
                stop_price=sl_price,
                close_position=True,
            )
            sl_id = str(sl_order.get("orderId"))
            
            # 4. Take profit
            tp_order = await self.client.place_order(
                symbol=signal.symbol,
                side=opposite_side,
                order_type="TAKE_PROFIT_MARKET",
                quantity=qty,
                stop_price=tp_price,
                close_position=True,
            )
            tp_id = str(tp_order.get("orderId"))
            
            self._order_times.append(datetime.utcnow())
            
            return Position(
                symbol=signal.symbol,
                side=signal.side,
                entry_price=avg_price,
                current_price=avg_price,
                size_usdt=signal.suggested_size_usdt,
                leverage=signal.leverage,
                stop_loss=sl_price,
                take_profit=tp_price,
                unrealized_pnl_usdt=0,
                unrealized_pnl_pct=0,
                opened_at=datetime.utcnow(),
                signal_id=signal.signal_id,
                entry_order_id=entry_id,
                sl_order_id=sl_id,
                tp_order_id=tp_id,
            )
        
        except Exception as e:
            logger.error(f"Execute signal {signal.signal_id} failed: {e}", exc_info=True)
            # Best effort cleanup
            try:
                await self.client.cancel_all_orders(signal.symbol)
            except Exception:
                pass
            return None
    
    async def close_position(self, position: Position, reason: str) -> bool:
        """Close 1 position (market) và cancel SL/TP orders."""
        if self.cfg.dry_run:
            logger.info(f"[DRY RUN] Close {position.symbol} reason={reason}")
            return True
        
        try:
            # Cancel SL/TP trước
            await self.client.cancel_all_orders(position.symbol)
            
            # Market close
            side = "SELL" if position.side == Side.LONG else "BUY"
            qty = self._round_quantity(
                position.symbol,
                (position.size_usdt * position.leverage) / position.current_price
            )
            await self.client.place_order(
                symbol=position.symbol,
                side=side,
                order_type="MARKET",
                quantity=qty,
                reduce_only=True,
            )
            logger.info(f"Closed {position.symbol} reason={reason}")
            return True
        except Exception as e:
            logger.error(f"Close position failed: {e}")
            return False
    
    async def close_all_positions(self, reason: str = "EMERGENCY") -> int:
        """Close tất cả positions — dùng cho kill switch."""
        positions = await self.client.get_positions()
        count = 0
        for p in positions:
            amt = float(p.get("positionAmt", 0))
            if amt == 0:
                continue
            try:
                symbol = p["symbol"]
                side = "SELL" if amt > 0 else "BUY"
                await self.client.cancel_all_orders(symbol)
                if not self.cfg.dry_run:
                    await self.client.place_order(
                        symbol=symbol,
                        side=side,
                        order_type="MARKET",
                        quantity=abs(amt),
                        reduce_only=True,
                    )
                count += 1
            except Exception as e:
                logger.error(f"Failed to close {p.get('symbol')}: {e}")
        logger.critical(f"Emergency close: {count} positions closed. Reason: {reason}")
        return count
