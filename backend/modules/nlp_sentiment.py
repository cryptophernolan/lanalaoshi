"""
NLP Sentiment Analyzer — phân tích sắc thái của mention, không chỉ đếm.

Vấn đề: Bài gốc chỉ đếm số mention $BTC — không phân biệt được:
- "$BTC going to $150k!!" → bullish
- "$BTC dumping hard, short the f..." → bearish
- "$BTC rugpull warning" → bearish + FUD

Module này dùng keyword + rule-based scoring (lightweight, không cần model).
Có thể upgrade lên VADER hoặc transformer model nếu cần.
"""
import re
import logging
from dataclasses import dataclass
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)


# ============ LEXICON ============
# Đa ngôn ngữ: English + Chinese + Vietnamese (vì user base Binance Square)

BULLISH_KEYWORDS = {
    # English
    "moon", "pump", "rocket", "bullish", "breakout", "ath", "all time high",
    "to the moon", "buy the dip", "btd", "diamond hands", "hodl", "long",
    "rally", "squeeze", "gem", "100x", "1000x", "undervalued", "accumulate",
    "strong buy", "breakout", "uptrend", "bull", "green", "gains",
    # Chinese
    "冲", "涨", "牛", "暴涨", "突破", "多头", "看多", "抄底", "拉升", "建仓",
    "起飞", "启动", "爆拉", "空头挤压",
    # Vietnamese
    "tăng", "pump", "đu lên", "múc", "vào lệnh", "long", "xanh", "đỉnh mới",
    "bứt phá", "đột phá", "hold chắc",
}

BEARISH_KEYWORDS = {
    # English
    "dump", "crash", "bearish", "short", "sell", "exit", "rugpull", "rug",
    "scam", "dead", "rekt", "liquidation", "liquidate", "breakdown",
    "downtrend", "correction", "capitulation", "bear", "red", "losses",
    "avoid", "warning", "caution", "risky",
    # Chinese
    "跌", "崩", "熊", "暴跌", "破位", "空头", "看空", "砸盘", "减仓", "割肉",
    "爆仓", "清仓", "跳水", "雷",
    # Vietnamese
    "giảm", "dump", "xả", "thoát hàng", "short", "đỏ", "lỗ", "bay acc",
    "sập", "bán tháo", "cắt lỗ", "rug", "lừa đảo",
}

# Hype noise — không tính là bullish vì dễ bị pump&dump
NOISE_KEYWORDS = {
    "shill", "not financial advice", "nfa", "dyor", "to the moon everybody",
}

INTENSIFIERS = {
    "very", "super", "extremely", "massively", "huge", "massive",
    "非常", "超", "巨", "极",
    "rất", "cực kỳ", "siêu", "quá",
}


@dataclass
class SentimentAnalysis:
    ticker: str
    mention_count: int
    bullish_score: float        # 0-100
    bearish_score: float        # 0-100
    net_sentiment: float        # -100 to +100
    confidence: float           # 0-1, dựa trên sample size
    sample_texts: list[str]     # để debug


class NLPSentimentAnalyzer:
    """
    Rule-based sentiment analyzer.
    Lightweight, không cần model, chạy on-the-fly.
    """
    
    def __init__(self):
        self.bullish = {k.lower() for k in BULLISH_KEYWORDS}
        self.bearish = {k.lower() for k in BEARISH_KEYWORDS}
        self.noise = {k.lower() for k in NOISE_KEYWORDS}
        self.intensifiers = {k.lower() for k in INTENSIFIERS}
        self.ticker_re = re.compile(r"\$([A-Z]{2,10})\b")
    
    def _score_text(self, text: str) -> tuple[float, float]:
        """
        Trả về (bullish_score, bearish_score) cho 1 đoạn text.
        Score 0-1 cho mỗi loại.
        """
        text_lower = text.lower()
        
        # Tokenize rough
        tokens = re.findall(r"\w+", text_lower)
        tokens_set = set(tokens)
        
        # Tìm intensifiers
        intensity = 1.0
        for intens in self.intensifiers:
            if intens in text_lower:
                intensity = 1.5
                break
        
        # Count matches
        bull_matches = 0
        bear_matches = 0
        
        for keyword in self.bullish:
            if " " in keyword:
                if keyword in text_lower:
                    bull_matches += 1
            elif keyword in tokens_set:
                bull_matches += 1
        
        for keyword in self.bearish:
            if " " in keyword:
                if keyword in text_lower:
                    bear_matches += 1
            elif keyword in tokens_set:
                bear_matches += 1
        
        # Detect noise — giảm confidence nếu toàn shill
        noise_matches = sum(1 for n in self.noise if n in text_lower)
        noise_penalty = 1.0 - min(noise_matches * 0.3, 0.7)
        
        # Negation detection — simple: "not bullish" → bearish
        has_negation = any(
            neg in tokens_set for neg in ["not", "no", "never", "không", "不", "沒"]
        )
        if has_negation:
            # Swap scores trong nhiều trường hợp
            bull_matches, bear_matches = bear_matches * 0.7, bull_matches * 0.7
        
        # Normalize
        bull_score = min(bull_matches * intensity * noise_penalty / 3, 1.0)
        bear_score = min(bear_matches * intensity * noise_penalty / 3, 1.0)
        
        return bull_score, bear_score
    
    def analyze_posts(self, posts: list[str]) -> dict[str, SentimentAnalysis]:
        """
        Input: list of post texts.
        Output: dict {ticker: SentimentAnalysis}
        
        Mỗi post có thể mention nhiều ticker → gán score cho từng ticker trong post đó.
        """
        ticker_data: dict[str, dict] = defaultdict(lambda: {
            "mentions": 0,
            "bull_sum": 0.0,
            "bear_sum": 0.0,
            "samples": [],
        })
        
        for post in posts:
            if not post:
                continue
            
            # Tìm tất cả tickers trong post
            tickers = set(self.ticker_re.findall(post.upper()))
            if not tickers:
                continue
            
            # Score post 1 lần
            bull, bear = self._score_text(post)
            
            for ticker in tickers:
                ticker_data[ticker]["mentions"] += 1
                ticker_data[ticker]["bull_sum"] += bull
                ticker_data[ticker]["bear_sum"] += bear
                if len(ticker_data[ticker]["samples"]) < 3:
                    ticker_data[ticker]["samples"].append(post[:100])
        
        results = {}
        for ticker, data in ticker_data.items():
            mentions = data["mentions"]
            if mentions == 0:
                continue
            
            avg_bull = (data["bull_sum"] / mentions) * 100
            avg_bear = (data["bear_sum"] / mentions) * 100
            net = avg_bull - avg_bear
            
            # Confidence tăng theo log của mentions
            import math
            confidence = min(math.log(mentions + 1) / math.log(20), 1.0)
            
            results[ticker] = SentimentAnalysis(
                ticker=ticker,
                mention_count=mentions,
                bullish_score=round(avg_bull, 1),
                bearish_score=round(avg_bear, 1),
                net_sentiment=round(net, 1),
                confidence=round(confidence, 2),
                sample_texts=data["samples"],
            )
        
        return results
    
    def confirms_direction(
        self, analysis: SentimentAnalysis, expected_direction: str
    ) -> tuple[bool, str]:
        """
        Kiểm tra sentiment có confirm với hướng trade dự kiến không.
        
        expected_direction: "LONG" or "SHORT"
        Returns (confirmed, reason)
        """
        if analysis.confidence < 0.3:
            return True, "Low sample size, no veto"  # không đủ data → không reject
        
        if expected_direction == "LONG":
            if analysis.net_sentiment < -20:
                return False, f"Bearish sentiment {analysis.net_sentiment:.0f} conflicts LONG"
            return True, f"Sentiment {analysis.net_sentiment:+.0f} ok for LONG"
        else:  # SHORT
            if analysis.net_sentiment > 20:
                return False, f"Bullish sentiment {analysis.net_sentiment:.0f} conflicts SHORT"
            return True, f"Sentiment {analysis.net_sentiment:+.0f} ok for SHORT"
