# /backtest - Run backtest with specified symbols

Run the backtest framework on historical data.

Steps:
1. Check if `backend/data/historical/*.csv` files exist for requested symbols
2. If missing, run `python backend/download_data.py <symbols> --months 6`
3. Run `python backend/run_backtest.py <symbols> --start <date>`
4. Parse the output and summarize:
   - Pass/fail against verdict criteria (PF > 1.5, DD < 20%, Sharpe > 1.0)
   - Key metrics in a clean table
   - Suggest parameter tweaks if failing

Do NOT modify `config/settings.py` without explicit user approval even if backtest suggests it.

Usage: `/backtest BTCUSDT ETHUSDT SOLUSDT`
