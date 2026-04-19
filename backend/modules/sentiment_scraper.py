"""
Sentiment Scraper — tầng confirm cho OI signal.

Nguồn (theo thứ tự ưu tiên):
1. Binance Square   — feed posts với tradingPairs tags (Playwright headless)
2. CryptoPanic API  — news mentions + vote sentiment per ticker (free key required)
3. Reddit           — r/CryptoCurrency hot posts, count ticker mentions (no key)
4. Alternative.me   — Fear & Greed Index global (no key)
5. CoinGecko        — Trending coins (no key)
6. Binance Gainers  — 24h top movers (no key)

Binance Square dùng Playwright headless để bypass Cloudflare bot detection.
Nếu không có CRYPTOPANIC_API_KEY → tự động dùng Reddit thay thế.
"""
import re
import math
import logging
import asyncio
from datetime import datetime
from collections import defaultdict
from typing import Optional
import httpx

from modules.schemas import SentimentScore
from config.settings import config

logger = logging.getLogger(__name__)

TICKER_RE = re.compile(r"\b([A-Z]{2,10})\b")

# Common English words that match TICKER_RE but are not tickers
_COMMON_WORDS = {
    "I", "A", "THE", "AND", "OR", "BUT", "IN", "ON", "AT", "TO", "FOR", "OF",
    "IS", "IT", "BE", "DO", "GO", "MY", "WE", "HE", "ME", "US", "UP", "SO",
    "NO", "IF", "BY", "AS", "AN", "AM", "PM", "USD", "IMO", "FAQ", "TBH",
    "DCA", "ATH", "ATL", "NFT", "NFTs", "APR", "APY", "TVL", "CEX", "DEX",
    "TIL", "PSA", "OTC", "ETF", "ETFs", "AMA", "RIP", "FUD", "FOMO",
    "DYOR", "NFA", "HODL", "BEWARE", "THIS", "THAT", "WITH", "FROM",
    "HAVE", "YOUR", "WHEN", "WILL", "BEEN", "THAN", "SOME", "WHAT",
    "JUST", "LIKE", "MORE", "THEY", "THEIR", "ALSO", "VERY", "BEEN",
    "CAN", "GET", "ALL", "NEW", "HAS", "HAD", "NOT", "NOW", "OUT",
    "WHO", "HOW", "WHY", "YES", "ANY", "USE", "WAY", "MAY", "HELP",
}


class SentimentScraper:
    def __init__(self):
        self.cfg = config.sentiment
        self._client = httpx.AsyncClient(timeout=15.0)
        self._last_scores: dict[str, SentimentScore] = {}
        self._fear_greed_cache: Optional[dict] = None
        # Binance Square session — refresh khi hết hạn (30 phút)
        self._bs_cookies: dict = {}
        self._bs_headers: dict = {}
        self._bs_session_ts: float = 0.0
        # ── Data source status tracking ──
        self._last_scan_ts: Optional[float] = None
        self._last_bs_posts: int = 0
        self._last_bs_tickers: int = 0
        self._last_cp_tickers: int = 0
        self._last_reddit_tickers: int = 0
        self._last_fg: Optional[dict] = None
        self._last_trending_count: int = 0
        self._last_gainers_count: int = 0
        self._last_error: Optional[str] = None
        self._using_reddit_fallback: bool = False

    # ──────────────────────────────────────────────
    # SOURCE 0: Binance Square (Playwright headless)
    # ──────────────────────────────────────────────

    async def fetch_binance_square(self) -> dict[str, dict]:
        """
        Scrape Binance Square dùng Playwright scroll-intercept.

        Cơ chế: load trang → scroll xuống nhiều lần để trigger infinite scroll
        → intercept TẤT CẢ responses từ feed-recommend/list (browser tự gắn
        đủ cookies/WAF token — không bị chặn).

        Quét 3 URL tabs:
          /en/square        — "For You" homepage feed
          /en/square/hot    — Hot trending posts
          /en/square/new    — Newest posts

        Mỗi tab scroll binance_square_pages_per_scene lần × ~20 posts/scroll.
        Tối đa: 3 tabs × N scrolls × ~20 posts = ~300 posts.

        Returns: { "BTC": {"mentions": 5, "bullish": 0, "bearish": 0} }
        """
        if not self.cfg.binance_square_enabled:
            return {}

        results: dict[str, dict] = defaultdict(lambda: {"mentions": 0, "bullish": 0, "bearish": 0})
        pages_per_tab = self.cfg.binance_square_pages_per_scene

        tabs = [
            "https://www.binance.com/en/square",
            "https://www.binance.com/en/square/hot",
            "https://www.binance.com/en/square/new",
        ]

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
                )
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    locale="en-US",
                )

                all_posts: list[dict] = []
                seen_ids: set[str] = set()

                for tab_url in tabs:
                    page = await context.new_page()
                    tab_posts: list[dict] = []

                    async def on_feed_response(resp, _tab_posts=tab_posts):
                        if "feed-recommend/list" in resp.url and resp.status == 200:
                            try:
                                body = await resp.json()
                                vos = (body.get("data") or {}).get("vos") or []
                                _tab_posts.extend(vos)
                            except Exception:
                                pass

                    page.on("response", on_feed_response)

                    try:
                        await page.goto(tab_url, wait_until="domcontentloaded", timeout=30_000)
                        # Đợi batch đầu tiên load
                        await asyncio.sleep(2)

                        # Scroll để trigger infinite scroll nhiều lần
                        for _ in range(pages_per_tab - 1):
                            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            await asyncio.sleep(1.5)

                    except Exception as e:
                        logger.debug(f"Binance Square tab {tab_url}: {e}")

                    await page.close()

                    # Deduplicate và thêm vào all_posts
                    for post in tab_posts:
                        pid = post.get("id", "")
                        if pid and pid in seen_ids:
                            continue
                        if pid:
                            seen_ids.add(pid)
                        all_posts.append(post)

                await browser.close()

            for post in all_posts:
                # Dedup per-post: mỗi ticker chỉ được đếm 1 lần/post
                # dù xuất hiện cả trong tradingPairs lẫn title/content
                post_tickers: set[str] = set()

                # tradingPairs là tags chính xác do user/AI gán
                pairs = post.get("tradingPairs") or post.get("tradingPairsV2") or []
                for pair in pairs:
                    code = (pair.get("code") or "").upper()
                    if code and code not in self.cfg.excluded_tickers:
                        post_tickers.add(code)

                # Scan title + content text để bắt mentions không có tag
                text = f"{post.get('title') or ''} {post.get('content') or ''}"
                if text.strip():
                    for ticker in TICKER_RE.findall(text):
                        if ticker not in self.cfg.excluded_tickers and ticker not in _COMMON_WORDS:
                            post_tickers.add(ticker)

                # Count 1 lần per post per ticker
                for ticker in post_tickers:
                    results[ticker]["mentions"] += 1

            self._last_bs_posts = len(all_posts)
            self._last_bs_tickers = len(results)
            logger.info(
                f"Binance Square: {len(results)} tickers from {len(all_posts)} posts "
                f"({len(tabs)} tabs × {pages_per_tab} scrolls)"
            )

        except ImportError:
            logger.warning("Binance Square: playwright not installed, skipping")
        except Exception as e:
            logger.warning(f"Binance Square fetch failed: {e}")

        return dict(results)

    # ──────────────────────────────────────────────
    # SOURCE 1: CryptoPanic
    # ──────────────────────────────────────────────

    async def fetch_cryptopanic(self) -> dict[str, dict]:
        """
        Fetch posts từ CryptoPanic, trả về per-ticker:
          { "BTC": {"mentions": 5, "bullish": 12, "bearish": 3} }

        - Không có API key → dùng public endpoint (ít data hơn)
        - Có API key    → full endpoint với vote data
        """
        if not self.cfg.cryptopanic_enabled:
            return {}

        results: dict[str, dict] = defaultdict(lambda: {"mentions": 0, "bullish": 0, "bearish": 0})

        base = "https://cryptopanic.com/api/free/v1/posts/"
        params: dict = {"public": "true"}
        if self.cfg.cryptopanic_api_key:
            params["auth_token"] = self.cfg.cryptopanic_api_key

        try:
            for page in range(1, self.cfg.cryptopanic_pages + 1):
                params["page"] = page
                r = await self._client.get(base, params=params)

                if r.status_code == 401:
                    logger.warning("CryptoPanic: invalid API key, falling back to public")
                    params.pop("auth_token", None)
                    r = await self._client.get(base, params=params)

                if r.status_code != 200:
                    logger.warning(f"CryptoPanic page {page}: HTTP {r.status_code}")
                    break

                data = r.json()
                posts = data.get("results", [])
                if not posts:
                    break

                for post in posts:
                    currencies = post.get("currencies") or []
                    votes = post.get("votes") or {}
                    bullish = int(votes.get("positive", 0) or 0)
                    bearish = int(votes.get("negative", 0) or 0)

                    for c in currencies:
                        code = (c.get("code") or "").upper()
                        if not code or code in self.cfg.excluded_tickers:
                            continue
                        results[code]["mentions"] += 1
                        results[code]["bullish"] += bullish
                        results[code]["bearish"] += bearish

                # tránh rate limit
                await asyncio.sleep(0.5)

            logger.info(f"CryptoPanic: {len(results)} tickers found")

        except Exception as e:
            logger.warning(f"CryptoPanic fetch failed: {e}")

        return dict(results)

    # ──────────────────────────────────────────────
    # SOURCE 2: Fear & Greed Index
    # ──────────────────────────────────────────────

    async def fetch_fear_greed(self) -> Optional[dict]:
        """
        Alternative.me Fear & Greed Index — không cần API key.
        Returns: {"value": 25, "label": "Extreme Fear"} or None
        """
        if not self.cfg.fear_greed_enabled:
            return self._fear_greed_cache

        try:
            r = await self._client.get("https://api.alternative.me/fng/?limit=1")
            r.raise_for_status()
            data = r.json().get("data", [{}])[0]
            result = {
                "value": int(data.get("value", 50)),
                "label": data.get("value_classification", "Neutral"),
            }
            self._fear_greed_cache = result
            logger.info(f"Fear & Greed: {result['value']} ({result['label']})")
            return result
        except Exception as e:
            logger.warning(f"Fear & Greed fetch failed: {e}")
            return self._fear_greed_cache  # trả về cache nếu có

    # ──────────────────────────────────────────────
    # SOURCE 3: CoinGecko Trending
    # ──────────────────────────────────────────────

    async def fetch_coingecko_trending(self) -> dict[str, int]:
        """CoinGecko trending coins — { "BTC": 1, "ETH": 3, ... } (rank)."""
        try:
            r = await self._client.get("https://api.coingecko.com/api/v3/search/trending")
            r.raise_for_status()
            trending = {}
            for rank, item in enumerate(r.json().get("coins", []), start=1):
                symbol = (item.get("item", {}).get("symbol") or "").upper()
                if symbol and symbol not in self.cfg.excluded_tickers:
                    trending[symbol] = rank
            return trending
        except Exception as e:
            logger.warning(f"CoinGecko trending failed: {e}")
            return {}

    # ──────────────────────────────────────────────
    # SOURCE 2b: Reddit (fallback khi không có CryptoPanic key)
    # ──────────────────────────────────────────────

    async def fetch_reddit(self) -> dict[str, dict]:
        """
        Fetch hot posts từ r/CryptoCurrency, đếm ticker mentions.
        Dùng làm fallback khi không có CRYPTOPANIC_API_KEY.
        Returns: { "BTC": {"mentions": 12, "bullish": 0, "bearish": 0} }
        """
        results: dict[str, dict] = defaultdict(lambda: {"mentions": 0, "bullish": 0, "bearish": 0})
        try:
            r = await self._client.get(
                "https://www.reddit.com/r/CryptoCurrency/hot.json",
                params={"limit": 100},
                headers={"User-Agent": "oi-bot/1.0"},
            )
            r.raise_for_status()
            posts = r.json().get("data", {}).get("children", [])
            for post in posts:
                data = post.get("data", {})
                text = f"{data.get('title', '')} {data.get('selftext', '')}"
                # Dedup per-post — Reddit title hay thể lặp ticker nhiều lần
                post_tickers: set[str] = set()
                for ticker in TICKER_RE.findall(text):
                    if ticker not in self.cfg.excluded_tickers and ticker not in _COMMON_WORDS:
                        post_tickers.add(ticker)
                for ticker in post_tickers:
                    results[ticker]["mentions"] += 1
            logger.info(f"Reddit: {len(results)} tickers found")
        except Exception as e:
            logger.warning(f"Reddit fetch failed: {e}")
        return dict(results)

    # ──────────────────────────────────────────────
    # SOURCE 4: Binance 24h Gainers
    # ──────────────────────────────────────────────

    async def fetch_binance_gainers(self) -> dict[str, int]:
        """Top 24h gainers trên Binance futures — { "BTC": 1, ... } (rank)."""
        try:
            r = await self._client.get(f"{config.binance.base_url}/fapi/v1/ticker/24hr")
            r.raise_for_status()
            tickers = [
                t for t in r.json()
                if t["symbol"].endswith("USDT")
                and float(t.get("quoteVolume", 0)) > config.oi_scanner.min_24h_volume_usdt
            ]
            tickers.sort(key=lambda t: float(t["priceChangePercent"]), reverse=True)
            gainers = {}
            for rank, t in enumerate(tickers[:20], start=1):
                base = t["symbol"].replace("USDT", "")
                if base not in self.cfg.excluded_tickers:
                    gainers[base] = rank
            return gainers
        except Exception as e:
            logger.warning(f"Gainers fetch failed: {e}")
            return {}

    # ──────────────────────────────────────────────
    # COMPOSITE SCORING
    # ──────────────────────────────────────────────

    def _compute_composite(
        self,
        cp_data: dict,          # {"mentions": int, "bullish": int, "bearish": int}
        gainers_rank: Optional[int],
        trending_rank: Optional[int],
        fear_greed: Optional[dict],
    ) -> float:
        """
        Composite sentiment score 0-100 cho 1 ticker.

        Weights (sum ~1.0):
          CryptoPanic mentions+sentiment : 0.45
          Binance Gainers rank           : 0.25
          CoinGecko Trending rank        : 0.15
          Fear & Greed market bias       : 0.15
        """
        score = 0.0

        # ── CryptoPanic (0–100 component) ──
        mentions = cp_data.get("mentions", 0)
        bullish = cp_data.get("bullish", 0)
        bearish = cp_data.get("bearish", 0)

        if mentions >= self.cfg.min_mentions:
            mention_score = min(math.log(mentions + 1) * 20, 100)
            total_votes = bullish + bearish
            sentiment_ratio = (bullish / total_votes) if total_votes > 0 else 0.5
            # sentiment_ratio 0.5 = neutral, 1.0 = all bullish
            sentiment_boost = (sentiment_ratio - 0.5) * 40  # -20 to +20
            cp_score = min(max(mention_score + sentiment_boost, 0), 100)
            score += cp_score * self.cfg.cryptopanic_weight

        # ── Binance Gainers rank (rank 1 = best, rank 20 = lowest) ──
        if gainers_rank is not None:
            gainers_score = max(0, 100 - gainers_rank * 5)
            score += gainers_score * self.cfg.gainers_weight

        # ── CoinGecko Trending rank ──
        if trending_rank is not None:
            trending_score = max(0, 100 - trending_rank * 10)
            score += trending_score * self.cfg.coingecko_weight

        # ── Fear & Greed — global market bias ──
        # Extreme Fear (< 25) → thị trường sợ hãi, dễ bounce → bullish bias nhẹ
        # Extreme Greed (> 75) → thị trường tham lam, dễ dump → bearish bias nhẹ
        # Neutral zone (25-75) → không ảnh hưởng nhiều
        if fear_greed is not None:
            fg_val = fear_greed["value"]
            if fg_val <= 25:       # Extreme Fear → contrarian bullish
                fg_score = 70
            elif fg_val >= 75:     # Extreme Greed → contrarian bearish (lower score)
                fg_score = 30
            else:                  # Neutral zone
                fg_score = 50
            score += fg_score * self.cfg.fear_greed_weight

        return round(min(score, 100), 2)

    # ──────────────────────────────────────────────
    # MAIN SCAN
    # ──────────────────────────────────────────────

    async def scan(self) -> dict[str, SentimentScore]:
        """Chạy tất cả sources parallel, kết hợp thành SentimentScore dict."""
        use_reddit = not self.cfg.cryptopanic_api_key
        self._using_reddit_fallback = use_reddit
        cp_task = self.fetch_reddit() if use_reddit else self.fetch_cryptopanic()
        fg_task = self.fetch_fear_greed()
        trending_task = self.fetch_coingecko_trending()
        gainers_task = self.fetch_binance_gainers()
        bs_task = self.fetch_binance_square()

        cp_data, fear_greed, trending, gainers, bs_data = await asyncio.gather(
            cp_task, fg_task, trending_task, gainers_task, bs_task,
            return_exceptions=True,
        )

        if isinstance(cp_data, Exception):
            logger.error(f"Social data gather error: {cp_data}")
            self._last_error = str(cp_data)
            cp_data = {}
        if isinstance(fear_greed, Exception):
            fear_greed = self._fear_greed_cache
        if isinstance(trending, Exception):
            trending = {}
        if isinstance(gainers, Exception):
            gainers = {}
        if isinstance(bs_data, Exception):
            logger.warning(f"Binance Square gather error: {bs_data}")
            bs_data = {}

        # Track per-source counts
        if use_reddit:
            self._last_reddit_tickers = len(cp_data)
            self._last_cp_tickers = 0
        else:
            self._last_cp_tickers = len(cp_data)
            self._last_reddit_tickers = 0
        self._last_trending_count = len(trending) if isinstance(trending, dict) else 0
        self._last_gainers_count = len(gainers) if isinstance(gainers, dict) else 0
        if isinstance(fear_greed, dict):
            self._last_fg = fear_greed

        # Merge Binance Square vào cp_data (boost mentions)
        for ticker, d in bs_data.items():
            if ticker not in cp_data:
                cp_data[ticker] = {"mentions": 0, "bullish": 0, "bearish": 0}
            cp_data[ticker]["mentions"] += d["mentions"]

        # Union tất cả tickers
        all_tickers = set(cp_data) | set(trending) | set(gainers) | set(bs_data)

        scores: dict[str, SentimentScore] = {}
        now = datetime.utcnow()

        for ticker in all_tickers:
            if ticker in self.cfg.excluded_tickers:
                continue

            cp = cp_data.get(ticker, {"mentions": 0, "bullish": 0, "bearish": 0})
            composite = self._compute_composite(
                cp_data=cp,
                gainers_rank=gainers.get(ticker),
                trending_rank=trending.get(ticker),
                fear_greed=fear_greed if not isinstance(fear_greed, Exception) else None,
            )

            if composite < 8:
                continue

            sq = bs_data.get(ticker, {})
            scores[ticker] = SentimentScore(
                symbol=ticker,
                square_mentions=sq.get("mentions", 0),
                cryptopanic_mentions=cp.get("mentions", 0),
                cryptopanic_bullish=cp.get("bullish", 0),
                cryptopanic_bearish=cp.get("bearish", 0),
                coingecko_trending_rank=trending.get(ticker),
                gainers_rank=gainers.get(ticker),
                fear_greed_value=fear_greed.get("value") if isinstance(fear_greed, dict) else None,
                fear_greed_label=fear_greed.get("label") if isinstance(fear_greed, dict) else None,
                composite_score=composite,
                timestamp=now,
            )

        import time
        self._last_scan_ts = time.time()
        self._last_scores = scores
        self._last_error = None
        logger.info(
            f"Sentiment scan: {len(scores)} tickers | "
            f"Square: {len(bs_data)} tickers | "
            f"F&G: {fear_greed.get('value') if isinstance(fear_greed, dict) else 'N/A'} "
            f"({fear_greed.get('label') if isinstance(fear_greed, dict) else ''})"
        )
        return scores

    def get_status(self) -> dict:
        """Returns status dict for /api/datasources endpoint."""
        import time
        now = time.time()

        def _age(ts):
            if ts is None:
                return None
            return round(now - ts)

        bs_enabled = self.cfg.binance_square_enabled
        cp_has_key = bool(self.cfg.cryptopanic_api_key)

        return {
            "binance_square": {
                "enabled": bs_enabled,
                "status": "OK" if bs_enabled and self._last_bs_tickers > 0 else
                          ("DISABLED" if not bs_enabled else "NO_DATA"),
                "last_update_age_s": _age(self._last_scan_ts),
                "posts_scraped": self._last_bs_posts,
                "tickers_found": self._last_bs_tickers,
            },
            "cryptopanic": {
                "enabled": self.cfg.cryptopanic_enabled and cp_has_key,
                "status": "OK" if self.cfg.cryptopanic_enabled and cp_has_key and self._last_cp_tickers > 0 else
                          ("NO_KEY" if not cp_has_key else
                           ("DISABLED" if not self.cfg.cryptopanic_enabled else "NO_DATA")),
                "has_api_key": cp_has_key,
                "last_update_age_s": _age(self._last_scan_ts),
                "tickers_found": self._last_cp_tickers,
            },
            "reddit": {
                "enabled": self._using_reddit_fallback,
                "status": "OK" if self._using_reddit_fallback and self._last_reddit_tickers > 0 else
                          ("FALLBACK" if self._using_reddit_fallback else "STANDBY"),
                "is_fallback": self._using_reddit_fallback,
                "last_update_age_s": _age(self._last_scan_ts),
                "tickers_found": self._last_reddit_tickers,
            },
            "fear_greed": {
                "enabled": self.cfg.fear_greed_enabled,
                "status": "OK" if self._last_fg else ("DISABLED" if not self.cfg.fear_greed_enabled else "NO_DATA"),
                "last_update_age_s": _age(self._last_scan_ts),
                "value": self._last_fg.get("value") if self._last_fg else None,
                "label": self._last_fg.get("label") if self._last_fg else None,
            },
            "coingecko": {
                "enabled": True,
                "status": "OK" if self._last_trending_count > 0 else "NO_DATA",
                "last_update_age_s": _age(self._last_scan_ts),
                "trending_count": self._last_trending_count,
            },
            "binance_gainers": {
                "enabled": True,
                "status": "OK" if self._last_gainers_count > 0 else "NO_DATA",
                "last_update_age_s": _age(self._last_scan_ts),
                "tickers_found": self._last_gainers_count,
            },
            "last_error": self._last_error,
            "total_scored_tickers": len(self._last_scores),
        }

    async def run_forever(self, callback):
        while True:
            try:
                scores = await self.scan()
                if scores:
                    await callback(scores)
            except Exception as e:
                logger.error(f"Sentiment loop error: {e}", exc_info=True)
            await asyncio.sleep(self.cfg.scan_interval)

    async def close(self):
        await self._client.aclose()
