"""
Backtest Framework.

Dùng historical OI + klines để simulate bot.
Output: full metrics report (Sharpe, Sortino, max DD, profit factor...).

Usage:
    bt = Backtester(data_path="./data/historical")
    results = await bt.run(
        symbols=["BTCUSDT", "ETHUSDT", ...],
        start_date="2024-01-01",
        end_date="2024-06-01",
    )
    print(bt.generate_report(results))
"""
import logging
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import math

import pandas as pd

from modules.schemas import Side
from config.settings import config

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    symbol: str
    side: Side
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    size_usdt: float = 100.0
    leverage: int = 3
    stop_loss: float = 0.0
    take_profit: float = 0.0
    exit_reason: str = ""
    pnl_usdt: float = 0.0
    pnl_pct: float = 0.0
    fees_usdt: float = 0.0


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    initial_capital: float = 10_000.0
    final_capital: float = 10_000.0
    
    @property
    def total_return_pct(self) -> float:
        return ((self.final_capital - self.initial_capital) / self.initial_capital) * 100
    
    @property
    def num_trades(self) -> int:
        return len(self.trades)
    
    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0
        wins = [t for t in self.trades if t.pnl_usdt > 0]
        return len(wins) / len(self.trades)
    
    @property
    def profit_factor(self) -> float:
        total_win = sum(t.pnl_usdt for t in self.trades if t.pnl_usdt > 0)
        total_loss = abs(sum(t.pnl_usdt for t in self.trades if t.pnl_usdt < 0))
        return total_win / total_loss if total_loss > 0 else float("inf")
    
    @property
    def max_drawdown_pct(self) -> float:
        if not self.equity_curve:
            return 0
        values = [e[1] for e in self.equity_curve]
        peak = values[0]
        max_dd = 0
        for v in values:
            if v > peak:
                peak = v
            dd = ((peak - v) / peak) * 100
            if dd > max_dd:
                max_dd = dd
        return max_dd
    
    @property
    def sharpe_ratio(self) -> float:
        """Annualized Sharpe, giả định 0 risk-free rate."""
        if len(self.equity_curve) < 2:
            return 0
        values = [e[1] for e in self.equity_curve]
        returns = []
        for i in range(1, len(values)):
            if values[i-1] > 0:
                returns.append((values[i] - values[i-1]) / values[i-1])
        if not returns:
            return 0
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        std = math.sqrt(variance)
        if std == 0:
            return 0
        # Annualize — assume 365 data points ~ daily
        return (mean / std) * math.sqrt(365)
    
    @property
    def sortino_ratio(self) -> float:
        if len(self.equity_curve) < 2:
            return 0
        values = [e[1] for e in self.equity_curve]
        returns = []
        for i in range(1, len(values)):
            if values[i-1] > 0:
                returns.append((values[i] - values[i-1]) / values[i-1])
        if not returns:
            return 0
        mean = sum(returns) / len(returns)
        negative = [r for r in returns if r < 0]
        if not negative:
            return float("inf")
        downside_std = math.sqrt(sum(r**2 for r in negative) / len(negative))
        if downside_std == 0:
            return 0
        return (mean / downside_std) * math.sqrt(365)


class Backtester:
    def __init__(self, data_path: str = None):
        self.cfg = config.backtest
        self.data_path = Path(data_path or self.cfg.data_path)
        self.oi_cfg = config.oi_scanner
        self.risk_cfg = config.risk
    
    def _load_symbol_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        Load historical data cho 1 symbol.
        Format CSV expected:
            timestamp, open, high, low, close, volume, oi, funding_rate, taker_ratio
        """
        file = self.data_path / f"{symbol}.csv"
        if not file.exists():
            logger.warning(f"No data file: {file}")
            return None
        df = pd.read_csv(file, parse_dates=["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df
    
    def _check_divergence(
        self, df: pd.DataFrame, idx: int, window_bars: int
    ) -> Optional[tuple[Side, float]]:
        """Return (direction, confidence) or None."""
        if idx < window_bars:
            return None
        
        start = df.iloc[idx - window_bars]
        now = df.iloc[idx]
        
        if start["oi"] == 0 or start["close"] == 0:
            return None
        
        oi_change = ((now["oi"] - start["oi"]) / start["oi"]) * 100
        price_change = ((now["close"] - start["close"]) / start["close"]) * 100
        
        if abs(oi_change) < self.oi_cfg.min_oi_change_pct:
            return None
        if abs(price_change) > self.oi_cfg.max_price_change_pct:
            return None
        
        ratio = abs(oi_change) / max(abs(price_change), 0.1)
        if ratio < self.oi_cfg.min_divergence_ratio:
            return None
        
        # Direction
        long_signals = 0
        short_signals = 0
        
        taker_ratio = now.get("taker_ratio", 1.0)
        funding = now.get("funding_rate", 0.0)
        
        if taker_ratio > self.oi_cfg.taker_ratio_threshold:
            long_signals += 1
        elif taker_ratio < (1 / self.oi_cfg.taker_ratio_threshold):
            short_signals += 1
        
        if funding > self.oi_cfg.funding_rate_threshold:
            short_signals += 1
        elif funding < -self.oi_cfg.funding_rate_threshold:
            long_signals += 1
        
        if price_change > 0.5:
            long_signals += 1
        elif price_change < -0.5:
            short_signals += 1
        
        if long_signals > short_signals:
            return Side.LONG, 0.5 + 0.15 * (long_signals - short_signals)
        elif short_signals > long_signals:
            return Side.SHORT, 0.5 + 0.15 * (short_signals - long_signals)
        return None
    
    async def run(
        self,
        symbols: list[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> BacktestResult:
        result = BacktestResult(initial_capital=self.cfg.initial_capital)
        capital = self.cfg.initial_capital
        open_trades: dict[str, BacktestTrade] = {}
        
        # Load all data
        all_data = {}
        for s in symbols:
            df = self._load_symbol_data(s)
            if df is None:
                continue
            if start_date:
                df = df[df["timestamp"] >= pd.Timestamp(start_date)]
            if end_date:
                df = df[df["timestamp"] <= pd.Timestamp(end_date)]
            all_data[s] = df.reset_index(drop=True)
        
        if not all_data:
            logger.error("No data loaded")
            return result
        
        # Align timestamps — iterate by 1 ref timeline
        ref_symbol = list(all_data.keys())[0]
        timestamps = all_data[ref_symbol]["timestamp"].tolist()
        
        window_bars = 4  # e.g., 4 bars of 15min = 1h window
        
        for i, ts in enumerate(timestamps):
            # 1. Check exits first
            for sym in list(open_trades.keys()):
                df = all_data.get(sym)
                if df is None or i >= len(df):
                    continue
                bar = df.iloc[i]
                trade = open_trades[sym]
                
                exit_price = None
                exit_reason = ""
                
                if trade.side == Side.LONG:
                    if bar["low"] <= trade.stop_loss:
                        exit_price = trade.stop_loss
                        exit_reason = "SL"
                    elif bar["high"] >= trade.take_profit:
                        exit_price = trade.take_profit
                        exit_reason = "TP"
                else:
                    if bar["high"] >= trade.stop_loss:
                        exit_price = trade.stop_loss
                        exit_reason = "SL"
                    elif bar["low"] <= trade.take_profit:
                        exit_price = trade.take_profit
                        exit_reason = "TP"
                
                if exit_price is not None:
                    trade.exit_time = bar["timestamp"]
                    trade.exit_price = exit_price
                    trade.exit_reason = exit_reason
                    
                    if trade.side == Side.LONG:
                        pnl_pct = (exit_price - trade.entry_price) / trade.entry_price
                    else:
                        pnl_pct = (trade.entry_price - exit_price) / trade.entry_price
                    
                    trade.pnl_pct = pnl_pct * 100 * trade.leverage
                    notional = trade.size_usdt * trade.leverage
                    trade.fees_usdt = notional * (self.cfg.commission_pct / 100) * 2
                    gross = trade.size_usdt * pnl_pct * trade.leverage
                    slippage_cost = notional * (self.cfg.slippage_pct / 100)
                    trade.pnl_usdt = gross - trade.fees_usdt - slippage_cost
                    
                    capital += trade.pnl_usdt
                    result.trades.append(trade)
                    del open_trades[sym]
            
            # 2. Check entries
            if len(open_trades) < self.risk_cfg.max_concurrent_positions:
                for sym, df in all_data.items():
                    if sym in open_trades or i >= len(df):
                        continue
                    signal = self._check_divergence(df, i, window_bars)
                    if not signal:
                        continue
                    direction, confidence = signal
                    if confidence < 0.6:
                        continue
                    
                    bar = df.iloc[i]
                    entry = bar["close"]
                    size = min(
                        self.risk_cfg.max_position_size_usdt,
                        capital * 0.05
                    )
                    leverage = 3
                    
                    if direction == Side.LONG:
                        sl = entry * (1 - self.risk_cfg.default_stop_loss_pct / 100)
                        tp = entry * (1 + self.risk_cfg.default_take_profit_pct / 100)
                    else:
                        sl = entry * (1 + self.risk_cfg.default_stop_loss_pct / 100)
                        tp = entry * (1 - self.risk_cfg.default_take_profit_pct / 100)
                    
                    trade = BacktestTrade(
                        symbol=sym,
                        side=direction,
                        entry_time=bar["timestamp"],
                        entry_price=entry,
                        size_usdt=size,
                        leverage=leverage,
                        stop_loss=sl,
                        take_profit=tp,
                    )
                    open_trades[sym] = trade
            
            # 3. Update equity curve
            unrealized = 0
            for trade in open_trades.values():
                df = all_data[trade.symbol]
                if i < len(df):
                    curr = df.iloc[i]["close"]
                    if trade.side == Side.LONG:
                        pct = (curr - trade.entry_price) / trade.entry_price
                    else:
                        pct = (trade.entry_price - curr) / trade.entry_price
                    unrealized += trade.size_usdt * pct * trade.leverage
            
            result.equity_curve.append((ts, capital + unrealized))
        
        result.final_capital = capital
        return result
    
    def generate_report(self, result: BacktestResult) -> str:
        return f"""
╔══════════════════════════════════════════╗
║         BACKTEST REPORT                  ║
╠══════════════════════════════════════════╣
║ Initial Capital:  ${result.initial_capital:>14,.2f}║
║ Final Capital:    ${result.final_capital:>14,.2f}║
║ Total Return:     {result.total_return_pct:>14.2f}% ║
║ Max Drawdown:     {result.max_drawdown_pct:>14.2f}% ║
╠══════════════════════════════════════════╣
║ Number of Trades: {result.num_trades:>15} ║
║ Win Rate:         {result.win_rate*100:>14.2f}% ║
║ Profit Factor:    {result.profit_factor:>15.2f} ║
║ Sharpe Ratio:     {result.sharpe_ratio:>15.2f} ║
║ Sortino Ratio:    {result.sortino_ratio:>15.2f} ║
╚══════════════════════════════════════════╝

Verdict:
{self._verdict(result)}
"""
    
    def _verdict(self, r: BacktestResult) -> str:
        issues = []
        if r.num_trades < 100:
            issues.append("⚠️  Sample size < 100 trades, không đủ statistical significance")
        if r.max_drawdown_pct > 25:
            issues.append(f"⚠️  Max DD {r.max_drawdown_pct:.1f}% quá cao")
        if r.profit_factor < 1.3:
            issues.append(f"⚠️  Profit factor {r.profit_factor:.2f} yếu, cần >1.5")
        if r.sharpe_ratio < 1.0:
            issues.append(f"⚠️  Sharpe {r.sharpe_ratio:.2f} dưới chuẩn")
        if r.win_rate < 0.4:
            issues.append(f"⚠️  Win rate {r.win_rate*100:.0f}% thấp — cần R:R cao bù")
        
        if not issues:
            return "✅ Strategy passes initial validation. Vẫn phải forward-test trước khi live."
        return "\n".join(issues)
