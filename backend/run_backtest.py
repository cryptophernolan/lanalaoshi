"""
Backtest runner CLI.

Usage:
    python run_backtest.py BTCUSDT ETHUSDT --start 2024-06-01 --end 2024-12-01

Chạy download_data.py trước để có historical data.
"""
import asyncio
import argparse
import logging

from backtest.backtester import Backtester

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols", nargs="+", help="Symbols to backtest")
    parser.add_argument("--start", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", help="End date YYYY-MM-DD")
    parser.add_argument("--data-path", default="./data/historical")
    args = parser.parse_args()
    
    bt = Backtester(data_path=args.data_path)
    result = await bt.run(
        symbols=args.symbols,
        start_date=args.start,
        end_date=args.end,
    )
    
    print(bt.generate_report(result))
    
    # Save trade log
    import csv
    from pathlib import Path
    log_file = Path("./data/backtest_trades.csv")
    log_file.parent.mkdir(exist_ok=True)
    with open(log_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "symbol", "side", "entry_time", "exit_time",
            "entry_price", "exit_price", "pnl_usdt", "pnl_pct", "exit_reason"
        ])
        for t in result.trades:
            writer.writerow([
                t.symbol, t.side.value, t.entry_time, t.exit_time,
                t.entry_price, t.exit_price, t.pnl_usdt, t.pnl_pct, t.exit_reason
            ])
    print(f"\nTrade log saved to {log_file}")


if __name__ == "__main__":
    asyncio.run(main())
