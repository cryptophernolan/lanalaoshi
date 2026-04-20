"""
Central config. Tất cả magic numbers ở đây để dễ tune.
Tuyệt đối KHÔNG hardcode thresholds ở chỗ khác.
"""
from dataclasses import dataclass, field
from typing import List
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class BinanceConfig:
    api_key: str = os.getenv("BINANCE_API_KEY", "")
    api_secret: str = os.getenv("BINANCE_API_SECRET", "")
    testnet: bool = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
    
    # URLs
    @property
    def base_url(self) -> str:
        return "https://testnet.binancefuture.com" if self.testnet else "https://fapi.binance.com"
    
    @property
    def ws_url(self) -> str:
        return "wss://stream.binancefuture.com" if self.testnet else "wss://fstream.binance.com"


@dataclass
class OIScannerConfig:
    # Core divergence thresholds
    # NOTE: Hạ thấp cho local demo/test — raise lại khi deploy production
    min_oi_change_pct: float = 3.0           # OI phải tăng ít nhất 3% trong window
    max_price_change_pct: float = 10.0        # Giá di chuyển không quá 10%
    min_divergence_ratio: float = 1.5         # OI_change / price_change >= 1.5
    
    # Time windows (phút)
    short_window: int = 15
    medium_window: int = 60
    long_window: int = 240
    
    # Filters
    min_24h_volume_usdt: float = 50_000_000   # Loại coin rác thanh khoản thấp
    excluded_symbols: List[str] = field(default_factory=lambda: [
        "BTCDOMUSDT", "DEFIUSDT"  # index, không phải coin thực
    ])
    
    # Direction confirmation
    funding_rate_threshold: float = 0.0005    # |funding| > 0.05% = lệch lớn
    use_taker_buy_sell_ratio: bool = True
    taker_ratio_threshold: float = 1.2        # taker_buy/taker_sell > 1.2 = long bias
    
    # Polling
    scan_interval_seconds: int = 30


@dataclass
class SentimentConfig:
    # Binance Square (Playwright headless — không cần key)
    binance_square_enabled: bool = True
    binance_square_pages_per_scene: int = 8   # 6 tabs × 8 scrolls × 20 posts ≈ 960 posts (Playwright)
                                               # + Direct API pagination (HOT+NEW): +300-600 posts
                                               # Total target: ~1000-1500 unique posts/scan

    # CryptoPanic (https://cryptopanic.com — đăng ký free để lấy key)
    cryptopanic_enabled: bool = True
    cryptopanic_api_key: str = os.getenv("CRYPTOPANIC_API_KEY", "")
    cryptopanic_weight: float = 0.45
    cryptopanic_pages: int = 3            # số trang fetch (20 posts/trang)

    # Alternative.me Fear & Greed Index (không cần key)
    fear_greed_enabled: bool = True
    fear_greed_weight: float = 0.15       # global market bias

    # CoinGecko Trending (không cần key)
    coingecko_enabled: bool = True
    coingecko_weight: float = 0.15

    # Binance 24h Gainers (không cần key)
    gainers_weight: float = 0.25

    # Scan interval
    scan_interval: int = 120              # giây

    # Twitter/X (tắt — cần key riêng)
    twitter_enabled: bool = False
    twitter_bearer_token: str = os.getenv("TWITTER_BEARER_TOKEN", "")

    # Stablecoin filter
    excluded_tickers: List[str] = field(default_factory=lambda: [
        "USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDP", "USD"
    ])

    # Min mentions để tính điểm
    min_mentions: int = 2


@dataclass
class RiskConfig:
    # Position sizing
    max_position_size_usdt: float = 500.0     # per trade
    max_leverage: int = 5                     # KHÔNG dùng leverage cao
    position_risk_pct: float = 1.0            # risk 1% account per trade
    
    # Portfolio limits
    max_concurrent_positions: int = 3
    max_total_exposure_pct: float = 30.0      # tổng exposure ≤ 30% account
    max_daily_loss_pct: float = 5.0           # lỗ 5% trong ngày → kill switch
    max_drawdown_pct: float = 15.0            # drawdown 15% → stop
    
    # Per-trade risk
    default_stop_loss_pct: float = 3.0
    default_take_profit_pct: float = 6.0      # R:R = 1:2 tối thiểu
    use_trailing_stop: bool = True
    trailing_stop_activation_pct: float = 3.0
    trailing_stop_distance_pct: float = 1.5
    
    # Kill switches
    emergency_stop: bool = False              # manual override
    auto_close_on_crash: bool = True          # close all nếu BTC drop >5% trong 1h


@dataclass
class ExecutorConfig:
    # Execution
    dry_run: bool = os.getenv("DRY_RUN", "true").lower() == "true"
    order_type: str = "MARKET"                # hoặc LIMIT với post_only
    slippage_tolerance_pct: float = 0.2
    
    # Safety
    require_manual_confirm_above_usdt: float = 1000.0
    max_orders_per_minute: int = 5            # rate limit cho chính bot


@dataclass
class NewListingConfig:
    enabled: bool = True

    # Listing age window: skip early chaos, give up if too old
    min_listing_age_hours: float = 12.0    # skip first 12h price discovery
    max_listing_age_hours: float = 96.0    # stop watching after 4 days

    # ① Consolidation
    min_consolidation_hours: float = 12.0
    max_consolidation_range_pct: float = 22.0   # slightly relaxed from 20% for wicks

    # ② Funding (negative = shorts crowded = coiled spring)
    funding_rate_max: float = -0.0005            # must be < -0.05%

    # ③ Volume contraction
    volume_contraction_ratio: float = 0.40       # current 1h < 40% of peak 1h

    # ④ OI stability (no sharp dump = not distribution)
    oi_stability_max_drop_pct: float = 5.0       # OI can drop at most 5% in 4h

    # ⑤ Global long/short ratio
    max_ls_ratio: float = 1.5

    # Breakout trigger
    breakout_volume_multiplier: float = 2.0      # current 5m vol > 2x avg

    # TP/SL
    stop_loss_below_mid_pct: float = 5.0         # SL = midpoint - 5%
    tp1_pct: float = 15.0                         # TP1 (close 50%)
    tp2_pct: float = 30.0                         # TP2 / full exit

    # Danger signals (exit conditions for open positions)
    danger_funding_positive: float = 0.0005       # funding turns > +0.05%
    danger_oi_drop_pct: float = 15.0              # OI drops >15%
    danger_ls_max: float = 2.0                    # L/S euphoria > 2.0

    # Polling
    scan_interval_seconds: int = 60


@dataclass
class BTCBiasConfig:
    """Config cho BTCBiasAnalyzer (Smart Money signal từ Paul Wei's account)."""
    enabled: bool = True
    refresh_interval_seconds: int = 3600   # fetch mỗi 1 tiếng (repo update daily)
    # Ngưỡng confidence để tác động lên signal score
    min_confidence_to_boost: float = 0.35  # confidence >= 35% → cộng điểm
    min_confidence_to_suppress: float = 0.45  # confidence >= 45% → trừ điểm
    # Max score delta (+-) BTCBias có thể ảnh hưởng lên signal
    max_score_delta: int = 3


@dataclass
class BacktestConfig:
    data_path: str = "./data/historical"
    initial_capital: float = 10_000.0
    commission_pct: float = 0.04              # 0.04% Binance futures taker
    slippage_pct: float = 0.05
    
    # Walk-forward
    train_months: int = 3
    test_months: int = 1


@dataclass
class AppConfig:
    binance: BinanceConfig = field(default_factory=BinanceConfig)
    oi_scanner: OIScannerConfig = field(default_factory=OIScannerConfig)
    sentiment: SentimentConfig = field(default_factory=SentimentConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    executor: ExecutorConfig = field(default_factory=ExecutorConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    new_listing: NewListingConfig = field(default_factory=NewListingConfig)
    btc_bias: BTCBiasConfig = field(default_factory=BTCBiasConfig)
    
    # Telegram alerts
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    
    # Database
    db_url: str = os.getenv("DB_URL", "sqlite:///./data/bot.db")
    
    # API server
    api_host: str = "0.0.0.0"
    api_port: int = 8000


config = AppConfig()
