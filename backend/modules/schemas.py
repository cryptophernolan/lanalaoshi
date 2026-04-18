"""Core data structures — dùng Pydantic để validate strict."""
from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class SignalStrength(str, Enum):
    WEAK = "WEAK"
    MEDIUM = "MEDIUM"
    STRONG = "STRONG"


class OISnapshot(BaseModel):
    """Snapshot của OI + price tại 1 thời điểm."""
    symbol: str
    timestamp: datetime
    open_interest: float              # số lượng contracts
    open_interest_value: float        # USDT value
    mark_price: float
    volume_24h_usdt: float
    funding_rate: float
    taker_buy_volume: float
    taker_sell_volume: float
    
    @property
    def taker_ratio(self) -> float:
        if self.taker_sell_volume == 0:
            return float("inf")
        return self.taker_buy_volume / self.taker_sell_volume


class OIDivergence(BaseModel):
    """Kết quả phân tích divergence trong 1 window."""
    symbol: str
    window_minutes: int
    oi_change_pct: float
    price_change_pct: float
    divergence_ratio: float
    direction: Side                   # long/short bias
    confidence: float = Field(ge=0, le=1)
    timestamp: datetime
    
    # Supporting data
    funding_rate: float
    taker_ratio: float
    volume_24h_usdt: float


class SentimentScore(BaseModel):
    """Điểm sentiment cho 1 ticker."""
    symbol: str

    # Binance Square
    square_mentions: int = 0

    twitter_mentions: int = 0
    coingecko_trending_rank: Optional[int] = None
    gainers_rank: Optional[int] = None

    # CryptoPanic / Reddit
    cryptopanic_mentions: int = 0
    cryptopanic_bullish: int = 0       # positive vote count từ posts
    cryptopanic_bearish: int = 0       # negative vote count từ posts

    # Fear & Greed (global, 0-100)
    fear_greed_value: Optional[int] = None
    fear_greed_label: Optional[str] = None

    composite_score: float = Field(ge=0, le=100)
    timestamp: datetime


class TradeSignal(BaseModel):
    """Signal cuối cùng được bắn ra để execute."""
    symbol: str
    side: Side
    strength: SignalStrength
    signal_type: str = "OI_DIVERGENCE"   # OI_DIVERGENCE | NEW_LISTING_PUMP

    # Entry
    entry_price: float
    suggested_size_usdt: float
    leverage: int

    # Risk
    stop_loss: float
    take_profit: float
    risk_reward_ratio: float

    # Reasoning (cho dashboard + log)
    oi_divergence: Optional[OIDivergence] = None
    sentiment: Optional[SentimentScore] = None
    reasoning: str
    confidence: float = Field(ge=0, le=1)

    timestamp: datetime
    signal_id: str


class NewListingSetup(BaseModel):
    """Trạng thái theo dõi 1 coin mới list — cho new-listing pump strategy."""
    symbol: str
    listing_time: datetime
    listing_age_hours: float

    # Consolidation
    consolidation_high: float = 0.0
    consolidation_low: float = 0.0
    consolidation_range_pct: float = 0.0
    consolidation_hours: float = 0.0

    # 5 conditions
    cond_consolidation: bool = False   # ① cons>12h & range<20%
    cond_funding: bool = False          # ② funding < -0.05%
    cond_volume: bool = False           # ③ vol < peak/3
    cond_oi_stable: bool = False        # ④ OI stable/↑
    cond_ls_ratio: bool = False         # ⑤ L/S < 1.5
    conditions_met: int = 0
    all_conditions_met: bool = False

    # Raw values
    funding_rate: float = 0.0
    current_volume_1h: float = 0.0
    peak_volume_1h: float = 0.0
    volume_ratio: float = 0.0
    oi_change_4h_pct: float = 0.0
    ls_ratio: Optional[float] = None
    current_price: float = 0.0

    # Danger signals (names of active danger conditions)
    danger_signals: list[str] = Field(default_factory=list)

    # Status
    status: str = "WATCHING"           # WATCHING | READY | TRIGGERED | DANGER
    triggered_at: Optional[datetime] = None

    timestamp: datetime


class Position(BaseModel):
    """Open position."""
    symbol: str
    side: Side
    entry_price: float
    current_price: float
    size_usdt: float
    leverage: int
    
    stop_loss: float
    take_profit: float
    trailing_stop_price: Optional[float] = None
    
    unrealized_pnl_usdt: float
    unrealized_pnl_pct: float
    
    opened_at: datetime
    signal_id: str
    
    # Binance order IDs
    entry_order_id: Optional[str] = None
    sl_order_id: Optional[str] = None
    tp_order_id: Optional[str] = None


class ClosedTrade(BaseModel):
    """Trade đã đóng — cho stats + backtest."""
    symbol: str
    side: Side
    entry_price: float
    exit_price: float
    size_usdt: float
    leverage: int
    
    realized_pnl_usdt: float
    realized_pnl_pct: float
    fees_usdt: float
    
    opened_at: datetime
    closed_at: datetime
    exit_reason: Literal["TP", "SL", "TRAILING", "MANUAL", "EMERGENCY", "SIGNAL_EXIT"]
    
    signal_id: str
