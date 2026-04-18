# /debug-signal - Debug why a specific signal was or wasn't generated

Debug the signal generation pipeline for a given symbol.

Steps:
1. Ask user which symbol to debug
2. Fetch current data from Binance API:
   - Current OI + price
   - OI from 15min, 60min, 240min ago
   - Current funding rate
   - Taker buy/sell ratio
3. Run the same logic `oi_scanner.scan_symbol()` uses and show EACH filter:
   - ✓ or ✗ `oi_change_pct >= min_oi_change_pct` (actual: X vs threshold Y)
   - ✓ or ✗ `price_change_pct <= max_price_change_pct`
   - ✓ or ✗ `divergence_ratio >= min_divergence_ratio`
   - Direction resolution: show taker_ratio, funding, price_bias votes
4. If all pass, run `signal_aggregator._determine_strength()` logic
5. If signal would generate but none appeared, check:
   - `_recent_signals` cooldown (30 min default)
   - `risk.evaluate()` — run and show which of the 11 checks rejects
   - Check if in `executor._order_times` rate limit
6. Present as a flowchart showing where the signal was filtered

Do NOT actually place orders or modify state.

Usage: `/debug-signal BTCUSDT`
