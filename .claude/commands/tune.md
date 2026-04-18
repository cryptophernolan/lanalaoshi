# /tune - Analyze and suggest parameter tuning

Analyze current strategy parameters and suggest improvements.

Steps:
1. Read `backend/config/settings.py`
2. Check for recent backtest results in `data/backtest_trades.csv`
3. Analyze:
   - Win rate vs R:R ratio (if WR < 40%, R:R should be > 2.5)
   - Max DD vs position sizing (if DD high, reduce max_position_size_usdt)
   - Profit factor by exit reason (SL too tight? TP too far?)
4. Present suggestions as a table:
   | Parameter | Current | Suggested | Rationale |
5. DO NOT apply changes. Only propose. User must approve each change.

Safety rules:
- Never suggest `max_leverage > 5`
- Never suggest `dry_run = false`
- Never suggest disabling kill switches
- Always suggest `stop_loss_pct >= 2.0`

Usage: `/tune` or `/tune <specific aspect>`
