"""
Position Tracker — theo dõi open positions và quản lý trailing stop.

Chạy loop liên tục:
1. Sync positions từ Binance
2. Update current price cho mỗi position
3. Nếu đạt trailing activation → move SL
4. Detect close events (SL/TP hit) → record ClosedTrade
"""
import logging
import asyncio
from datetime import datetime
from typing import Callable, Optional

from modules.schemas import Position, ClosedTrade, Side
from modules.binance_client import BinanceFuturesClient
from execution.executor import Executor
from config.settings import config

logger = logging.getLogger(__name__)


class PositionTracker:
    def __init__(self, client: BinanceFuturesClient, executor: Executor):
        self.client = client
        self.executor = executor
        self.cfg = config.risk
        self._positions: dict[str, Position] = {}   # symbol → Position
        self._closed_trades: list[ClosedTrade] = []
        self._trailing_activated: dict[str, bool] = {}
    
    def register(self, position: Position):
        self._positions[position.symbol] = position
        logger.info(f"Tracking {position.symbol} {position.side.value} @ {position.entry_price}")
    
    @property
    def positions(self) -> list[Position]:
        return list(self._positions.values())
    
    @property
    def closed_trades(self) -> list[ClosedTrade]:
        return list(self._closed_trades)
    
    async def sync_from_binance(self):
        """Reconcile internal state với Binance."""
        try:
            binance_positions = await self.client.get_positions()
            binance_symbols = set()
            
            for p in binance_positions:
                amt = float(p.get("positionAmt", 0))
                if amt == 0:
                    continue
                symbol = p["symbol"]
                binance_symbols.add(symbol)
                
                current_price = float(p.get("markPrice", 0))
                entry_price = float(p.get("entryPrice", 0))
                
                if symbol in self._positions:
                    pos = self._positions[symbol]
                    pos.current_price = current_price
                    
                    # Tính unrealized PnL
                    if pos.side == Side.LONG:
                        pnl_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100
                    else:
                        pnl_pct = ((pos.entry_price - current_price) / pos.entry_price) * 100
                    
                    pos.unrealized_pnl_pct = pnl_pct * pos.leverage
                    pos.unrealized_pnl_usdt = pos.size_usdt * (pnl_pct / 100) * pos.leverage
            
            # Detect closed positions (có trong _positions nhưng không còn trên Binance)
            for symbol in list(self._positions.keys()):
                if symbol not in binance_symbols:
                    closed = self._handle_position_closed(symbol)
                    if closed:
                        self._closed_trades.append(closed)
        except Exception as e:
            logger.debug(f"Sync failed (no API key?): {e}")
    
    def _handle_position_closed(self, symbol: str) -> Optional[ClosedTrade]:
        pos = self._positions.pop(symbol, None)
        if not pos:
            return None
        
        # Exit reason heuristic — không có orderbook data nên đoán
        if pos.side == Side.LONG:
            if pos.current_price >= pos.take_profit * 0.99:
                reason = "TP"
            elif pos.current_price <= pos.stop_loss * 1.01:
                reason = "SL"
            else:
                reason = "MANUAL"
        else:
            if pos.current_price <= pos.take_profit * 1.01:
                reason = "TP"
            elif pos.current_price >= pos.stop_loss * 0.99:
                reason = "SL"
            else:
                reason = "MANUAL"
        
        closed = ClosedTrade(
            symbol=pos.symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=pos.current_price,
            size_usdt=pos.size_usdt,
            leverage=pos.leverage,
            realized_pnl_usdt=pos.unrealized_pnl_usdt,
            realized_pnl_pct=pos.unrealized_pnl_pct,
            fees_usdt=pos.size_usdt * pos.leverage * 0.0008,  # ~0.08% round trip
            opened_at=pos.opened_at,
            closed_at=datetime.utcnow(),
            exit_reason=reason,
            signal_id=pos.signal_id,
        )
        logger.info(
            f"Closed {symbol} reason={reason} PnL=${closed.realized_pnl_usdt:.2f} "
            f"({closed.realized_pnl_pct:.2f}%)"
        )
        return closed
    
    async def manage_trailing_stops(self):
        """Update trailing stop cho các positions đã activate."""
        if not self.cfg.use_trailing_stop:
            return
        
        activation_pct = self.cfg.trailing_stop_activation_pct
        distance_pct = self.cfg.trailing_stop_distance_pct
        
        for symbol, pos in self._positions.items():
            if pos.unrealized_pnl_pct < activation_pct:
                continue
            
            # Đã đạt activation threshold
            if pos.side == Side.LONG:
                new_sl = pos.current_price * (1 - distance_pct / 100)
                if new_sl > pos.stop_loss:
                    await self._update_stop_loss(pos, new_sl)
            else:
                new_sl = pos.current_price * (1 + distance_pct / 100)
                if new_sl < pos.stop_loss:
                    await self._update_stop_loss(pos, new_sl)
    
    async def _update_stop_loss(self, pos: Position, new_sl: float):
        if config.executor.dry_run:
            logger.info(f"[DRY] Trailing SL {pos.symbol}: {pos.stop_loss:.4f} → {new_sl:.4f}")
            pos.stop_loss = new_sl
            return
        
        try:
            # Cancel old SL
            if pos.sl_order_id:
                try:
                    await self.client.cancel_order(pos.symbol, pos.sl_order_id)
                except Exception:
                    pass  # có thể đã bị fill
            
            # Place new SL
            opposite = "SELL" if pos.side == Side.LONG else "BUY"
            new_sl_rounded = self.executor._round_price(pos.symbol, new_sl)
            new_order = await self.client.place_order(
                symbol=pos.symbol,
                side=opposite,
                order_type="STOP_MARKET",
                quantity=0,
                stop_price=new_sl_rounded,
                close_position=True,
            )
            pos.sl_order_id = str(new_order.get("orderId"))
            pos.stop_loss = new_sl_rounded
            logger.info(f"Trailing SL updated {pos.symbol} → {new_sl_rounded}")
        except Exception as e:
            logger.error(f"Trailing SL update failed: {e}")
    
    async def run_forever(self):
        while True:
            try:
                await self.sync_from_binance()
                await self.manage_trailing_stops()
            except Exception as e:
                logger.error(f"PositionTracker loop error: {e}")
            await asyncio.sleep(10)  # sync mỗi 10s
    
    # Stats
    def get_stats(self) -> dict:
        total_pnl = sum(t.realized_pnl_usdt for t in self._closed_trades)
        wins = [t for t in self._closed_trades if t.realized_pnl_usdt > 0]
        losses = [t for t in self._closed_trades if t.realized_pnl_usdt <= 0]
        
        win_rate = len(wins) / len(self._closed_trades) if self._closed_trades else 0
        avg_win = sum(t.realized_pnl_usdt for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.realized_pnl_usdt for t in losses) / len(losses) if losses else 0
        
        total_win = sum(t.realized_pnl_usdt for t in wins)
        total_loss = abs(sum(t.realized_pnl_usdt for t in losses))
        profit_factor = total_win / total_loss if total_loss > 0 else 0.0
        
        return {
            "total_trades": len(self._closed_trades),
            "win_rate": win_rate,
            "total_pnl_usdt": total_pnl,
            "avg_win_usdt": avg_win,
            "avg_loss_usdt": avg_loss,
            "profit_factor": profit_factor,
            "open_positions": len(self._positions),
        }
