# /safety-check - Pre-flight check before going live

Audit the codebase and config for safety issues before live trading.

Run these checks and report PASS/FAIL for each:

## Config safety
- [ ] `BINANCE_TESTNET=true` OR explicitly confirmed mainnet ready
- [ ] `DRY_RUN=true` OR explicitly confirmed live ready  
- [ ] `max_leverage <= 5`
- [ ] `max_daily_loss_pct <= 10`
- [ ] `max_drawdown_pct <= 20`
- [ ] `default_stop_loss_pct >= 2.0`
- [ ] `position_risk_pct <= 2.0`
- [ ] Kill switches enabled (`auto_close_on_crash=true`)
- [ ] `.env` is in `.gitignore`

## Code integrity
- [ ] No hardcoded API keys in any Python file
- [ ] `risk.evaluate()` called before every `executor.execute_signal()`
- [ ] All `place_order` calls have error handling
- [ ] No `withdraw` or `transfer` API calls in codebase

## Operational readiness
- [ ] Has at least 30 successful testnet trades
- [ ] Has at least 1 successful Telegram kill switch test
- [ ] Has at least 1 week of paper-trading on mainnet with DRY_RUN=true
- [ ] Recent backtest shows PF > 1.5, DD < 20%, Sharpe > 1.0

Report format:
```
SAFETY REPORT
=============
Config:         X/9 PASS
Code:           X/4 PASS
Operations:     X/4 PASS (user must confirm)

❌ BLOCKERS (fix before live):
  - <list>

⚠️  WARNINGS:
  - <list>

✅ VERDICT: [READY FOR LIVE | NEEDS FIXES | NOT READY]
```

If any BLOCKERS exist, explicitly refuse to help enable live trading until they're fixed.

Usage: `/safety-check`
