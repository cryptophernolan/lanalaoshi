# OI Divergence Bot — Strategy Documentation

> Tài liệu này mô tả đầy đủ chiến lược, bộ lọc, và tham số của bot.
> **Cập nhật mỗi khi thêm/sửa filter hoặc logic mới.**

---

## 1. Ý tưởng cốt lõi (Core Thesis)

**OI Divergence = Smart Money Accumulation Signal**

Khi Open Interest tăng mạnh nhưng giá **chưa di chuyển tương ứng**, nghĩa là một lực lượng lớn (smart money / whale) đang âm thầm xây dựng position. Retail chưa biết → giá chưa phản ánh → tiềm năng breakout.

```
OI tăng mạnh  +  Giá đứng yên  →  Smart money đang vào lệnh
→ Bot xác định chiều rồi theo vào
```

Ngược lại với momentum trading (theo giá khi đã chạy), bot này vào **trước khi giá chạy**.

---

## 2. Pipeline xử lý tín hiệu

```
Binance Public API
      │
      ▼
[OI Scanner] ──── scan 100 symbols mỗi 30s
      │                 phát hiện OI Divergence
      ▼
[Signal Aggregator] ── kết hợp OI + Sentiment
      │                  xếp hạng WEAK / MEDIUM / STRONG
      ▼
[Risk Manager] ──── 11 safety checks
      │
      ▼
[Executor] ──── đặt bracket order (Entry + SL + TP)
      │         DRY_RUN: chỉ log, không đặt lệnh thật
      ▼
[Position Tracker] ── theo dõi PnL, trailing stop
      │
      ▼
[Dashboard / Telegram] ── hiển thị realtime
```

---

## 3. Bộ lọc (Filters)

### 3.1 OI Divergence Filter *(PRIMARY)*
**File:** `backend/modules/oi_scanner.py`

Điều kiện để 1 symbol được coi là có divergence:

| Điều kiện | Tham số | Default (demo) | Default (production) |
|---|---|---|---|
| OI thay đổi tối thiểu | `min_oi_change_pct` | 3.0% | **15.0%** |
| Giá di chuyển tối đa | `max_price_change_pct` | 10.0% | **5.0%** |
| Tỉ lệ OI/Price tối thiểu | `min_divergence_ratio` | 1.5x | **3.0x** |

> ⚠️ **Lưu ý:** Giá trị demo thấp hơn để test UI. Khi deploy production phải raise về production values.

**Công thức divergence ratio:**
```
ratio = |ΔOI%| / max(|ΔPrice%|, 0.1)
```

**Time window:** `medium_window = 60 phút` (default), cũng scan `short_window=15m`, `long_window=240m`

---

### 3.2 Liquidity Filter
**File:** `backend/modules/oi_scanner.py`

Loại bỏ coin thanh khoản thấp / coin rác:

| Điều kiện | Tham số | Giá trị |
|---|---|---|
| Volume 24h tối thiểu | `min_24h_volume_usdt` | $50,000,000 |
| Chỉ lấy USDT pairs | hardcoded | `symbol.endswith("USDT")` |
| Loại trừ index | `excluded_symbols` | `["BTCDOMUSDT", "DEFIUSDT"]` |

---

### 3.3 Direction Filter (Xác định LONG/SHORT)
**File:** `backend/modules/oi_scanner.py`

3 tín hiệu được vote để xác định chiều:

| Tín hiệu | Điều kiện LONG | Điều kiện SHORT |
|---|---|---|
| **Taker Buy/Sell Ratio** | `ratio > 1.2` | `ratio < 0.833` |
| **Funding Rate** | `rate < -0.05%` (short crowded → squeeze) | `rate > +0.05%` (long crowded → squeeze) |
| **Price Micro-bias** | `ΔPrice > +0.5%` | `ΔPrice < -0.5%` |

```
long_signals > short_signals  →  LONG
short_signals > long_signals  →  SHORT
bằng nhau                     →  bỏ qua (không trade)
```

**Confidence:** `0.5 + 0.15 × (winning_signals - losing_signals)`, max 1.0

| Tham số | Giá trị |
|---|---|
| `taker_ratio_threshold` | 1.2 |
| `funding_rate_threshold` | 0.0005 (0.05%) |

---

### 3.4 Signal Strength Filter
**File:** `backend/modules/signal_aggregator.py`

Mỗi signal được chấm điểm để xếp loại:

| Tiêu chí | Điểm |
|---|---|
| `divergence_ratio >= 8` | +3 |
| `divergence_ratio >= 5` | +2 |
| `divergence_ratio >= 1.5` | +1 |
| `confidence >= 0.8` | +2 |
| `confidence >= 0.65` | +1 |
| Sentiment score ≥ 60 | +2 |
| Sentiment score ≥ 30 | +1 |
| Volume 24h ≥ $500M | +1 |

| Tổng điểm | Loại |
|---|---|
| ≥ 7 | **STRONG** |
| ≥ 4 | **MEDIUM** |
| < 4 | **WEAK** |

> **Production:** Chỉ trade MEDIUM và STRONG. WEAK bị bỏ qua.
> **Dry-run/demo:** Cho phép cả WEAK để hiển thị đầy đủ.

---

### 3.5 BTC Crash Filter
**File:** `backend/modules/risk_manager.py`

Nếu BTC giảm >5% trong vòng 1 giờ → **dừng tất cả giao dịch** (thị trường đang crash, không trade).

| Tham số | Giá trị |
|---|---|
| `auto_close_on_crash` | `true` |
| Ngưỡng crash | 5% drop trong 12 nến 5m |

---

### 3.6 Risk Management Filters (11 checks)
**File:** `backend/modules/risk_manager.py`

Thứ tự kiểm tra trước mỗi lệnh:

| # | Check | Tham số | Giá trị |
|---|---|---|---|
| 1 | Emergency stop (manual) | `emergency_stop` | false |
| 2 | Kill switch đang active | `_kill_switch_triggered` | — |
| 3 | BTC crash detection | — | 5% / 1h |
| 4 | Balance > 0 | — | fallback $10,000 (dry_run) |
| 5 | Max drawdown | `max_drawdown_pct` | **15%** |
| 6 | Daily loss limit | `max_daily_loss_pct` | **5%** |
| 7 | Max concurrent positions | `max_concurrent_positions` | **3** |
| 8 | Không trùng symbol | — | 1 position/symbol |
| 9 | Total exposure | `max_total_exposure_pct` | **30%** account |
| 10 | Per-trade size | `max_position_size_usdt` | **$500** |
| 11 | Leverage cap | `max_leverage` | **5x** |
| 12 | R:R tối thiểu | hardcoded | **≥ 1.5** |

---

### 3.7 Cooldown Filter
**File:** `backend/modules/signal_aggregator.py`

Sau khi signal cho 1 symbol được tạo, symbol đó bị **cooldown 30 phút** → không tạo thêm signal mới cho cùng symbol trong 30 phút.

| Tham số | Giá trị |
|---|---|
| `_cooldown_minutes` | 30 |

---

## 4. Exit Strategy

### 4.1 Fixed SL/TP
| | LONG | SHORT |
|---|---|---|
| **Stop Loss** | entry × (1 - 3%) | entry × (1 + 3%) |
| **Take Profit** | entry × (1 + 6%) | entry × (1 - 6%) |
| **R:R** | 1:2 | 1:2 |

### 4.2 Trailing Stop
Kích hoạt khi unrealized PnL đạt **+3%**, sau đó trailing **1.5%** phía sau mark price.

| Tham số | Giá trị |
|---|---|
| `use_trailing_stop` | true |
| `trailing_stop_activation_pct` | 3.0% |
| `trailing_stop_distance_pct` | 1.5% |

---

## 5. Position Sizing

```
base_size = min(max_position_size_usdt, account_balance × 33 × 1%)
```
*(risk 1% account với SL 3% → notional = balance × 33 × position_risk_pct)*

| Strength | Size | Leverage |
|---|---|---|
| STRONG | base_size (tối đa $500) | min(max_leverage, 5x) |
| MEDIUM | base_size × 60% | min(max_leverage, 3x) |
| WEAK | base_size × 30% | 2x |

| Tham số | Giá trị |
|---|---|
| `max_position_size_usdt` | $500 |
| `position_risk_pct` | 1.0% |
| `max_leverage` | 5x |

---

## 6. Sentiment Confirmation (phụ)

Không bắt buộc — chỉ dùng để boost signal strength.

| Nguồn | Weight | Key? | Ghi chú |
|---|---|---|---|
| **Binance Square** feed posts | boost mentions | Không cần | Playwright headless — intercept feed API, đếm tradingPairs tags |
| **CryptoPanic** news mentions + votes | 45% | Free (register) | Mentions per ticker + bullish/bearish vote ratio |
| **Reddit** r/CryptoCurrency hot posts | 45% | Không cần | Fallback khi không có CryptoPanic key — đếm ticker mentions |
| **Binance 24h Gainers** rank | 25% | Không cần | Top 20 gainers by % change |
| **CoinGecko Trending** rank | 15% | Không cần | Top trending coins |
| **Fear & Greed Index** | 15% | Không cần | Global market bias (alternative.me) |
| Twitter/X | 0% | Tắt | Cần API key riêng |

> **Binance Square:** Dùng Playwright headless browser, load 3 tabs (`/square`, `/square/hot`, `/square/new`), scroll N lần mỗi tab để trigger infinite scroll, intercept tất cả responses từ `feed-recommend/list`. Mỗi post có `tradingPairs[].code` (tag chính xác) + scan title/content text thêm. Tối đa: 3 tabs × 5 scrolls × ~20 posts ≈ 300 posts/scan. Mentions được ghi vào `square_mentions` riêng.
>
> **CryptoPanic/Reddit fallback:** Nếu `CRYPTOPANIC_API_KEY` trống → tự động dùng Reddit thay thế (cùng weight 45%). Reddit không có vote data nên `bullish/bearish` = 0, chỉ dùng `mentions` count.

**Composite score formula (0–100):**

```
score = CryptoPanic_score × 0.45
      + Gainers_score    × 0.25
      + Trending_score   × 0.15
      + FearGreed_score  × 0.15
```

**CryptoPanic score:**
```
mention_score = min(log(mentions + 1) × 20, 100)
sentiment_boost = (bullish/(bullish+bearish) - 0.5) × 40   # -20 to +20
cp_score = clamp(mention_score + sentiment_boost, 0, 100)
```

**Fear & Greed mapping (contrarian):**
- ≤ 25 (Extreme Fear) → score 70 (market fearful → potential bounce)
- ≥ 75 (Extreme Greed) → score 30 (market greedy → potential dump)
- 25–75 (Neutral) → score 50

---

## 7. Tham số tổng hợp (settings.py)

```python
# OI Scanner
min_oi_change_pct    = 15.0   # % (production) / 3.0 (demo)
max_price_change_pct = 5.0    # % (production) / 10.0 (demo)
min_divergence_ratio = 3.0    # x  (production) / 1.5 (demo)
short_window         = 15     # phút
medium_window        = 60     # phút
long_window          = 240    # phút
scan_interval        = 30     # giây
min_24h_volume_usdt  = 50_000_000

# Risk
max_position_size_usdt     = 500.0
max_leverage               = 5
position_risk_pct          = 1.0
max_concurrent_positions   = 3
max_total_exposure_pct     = 30.0
max_daily_loss_pct         = 5.0
max_drawdown_pct           = 15.0
default_stop_loss_pct      = 3.0
default_take_profit_pct    = 6.0
trailing_stop_activation   = 3.0
trailing_stop_distance     = 1.5

# Executor
dry_run                    = true   # LUÔN true cho đến khi backtest xong
require_manual_confirm     = $1000  # confirm thủ công nếu size > $1000
```

---

---

## 7b. CatTrade Multi-Timeframe Confirmation

**File:** `backend/modules/cattrade_scraper.py`
**Sheet:** https://docs.google.com/spreadsheets/d/1k16nGFCE7oBXrEqvTpHSA2Z5530GM_kou-wiWklTsfY (cập nhật ~2 phút/lần)

Fetch CSV từ community sheet, parse 7 bảng ranking:

| Bảng | Dữ liệu sử dụng |
|---|---|
| 5m/15m/1h/4h/1d/1w 异动榜 | Volume/OI Z-score + timeframe ranking |
| 多窗口持仓量/额榜 | 方向一致性 (directional consistency), 异常综合分 |
| 结构分歧榜 | 主动买卖 (taker ratio), 结构形态 (whale structure pattern) |
| 市场份额相对榜 | 异常综合分 (market share anomaly score) |

**Signal scoring additions** (tích hợp vào `signal_aggregator.py`):

| Điều kiện | Điểm |
|---|---|
| Symbol trong 1h + 4h rankings | +2 |
| Symbol trong ≥ 2 timeframe rankings | +1 |
| 方向一致性 (同向上/上拐) khớp với LONG signal | +2 |
| 方向一致性 (同向下/下拐) khớp với SHORT signal | +2 |
| 方向一致性 ngược chiều signal | -1 |
| 结构形态 (大户领先做多/多头共振) + LONG | +2 |
| 结构形态 (大户领先做空/空头共振) + SHORT | +2 |
| 结构形态 ngược chiều signal | -1 |
| 异常综合分 ≥ 10 (multi-window) | +1 |
| 市场份额 异常综合分 ≥ 20 | +1 |

**API endpoint:** `GET /api/cattrade` — xem toàn bộ parsed data.

---

## 8. Những gì chưa implement (TODO)

- [ ] **Regime Filter:** Dừng trade khi thị trường choppy/sideways (ATR thấp, volatility thấp)
- [ ] **Kelly Criterion:** Sizing động dựa trên win rate thực tế từ backtest
- [ ] **NLP Sentiment Veto:** Nếu sentiment NLP phân tích text bearish mạnh → block LONG signal
- [ ] **Correlation Filter:** Không mở quá nhiều position cùng sector/correlation cao
- [ ] **Multi-timeframe Confirmation:** Signal medium window phải được xác nhận bởi long window

---

## 9. Lịch sử thay đổi

| Ngày | Thay đổi |
|---|---|
| 2026-04-18 | Khởi tạo bot, implement OI Divergence + 3-signal direction voting |
| 2026-04-18 | Fix: cache volume ticker 1 lần/scan (tránh 100x API call) |
| 2026-04-18 | Fix: dry_run balance fallback $10,000 khi không có API key |
| 2026-04-18 | Fix: profit_factor inf → 0.0 khi chưa có loss |
| 2026-04-18 | Fix: datetime serialization dùng model_dump(mode="json") |
| 2026-04-18 | Sentiment: thay Binance Square bằng CryptoPanic + Fear&Greed Index |
| 2026-04-18 | Sentiment: thêm Reddit r/CryptoCurrency làm fallback khi không có CryptoPanic key |
| 2026-04-18 | Sentiment: thêm lại Binance Square via Playwright headless — intercept feed API, đếm tradingPairs ticker tags |
| 2026-04-18 | CatTrade: tích hợp Google Sheet community data — multi-timeframe OI Z-score + whale structure analysis |
