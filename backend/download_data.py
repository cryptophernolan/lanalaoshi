"""
Download historical data từ Binance cho backtest.

Usage:
    python download_data.py BTCUSDT ETHUSDT --months 6 --interval 15m

Output CSV columns:
    timestamp, open, high, low, close, volume, oi, funding_rate, taker_ratio
"""
import asyncio
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")


BASE_URL = "https://fapi.binance.com"


async def fetch_klines(client: httpx.AsyncClient, symbol: str, interval: str, start: int, end: int) -> list:
    all_klines = []
    current = start
    while current < end:
        r = await client.get(
            f"{BASE_URL}/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "startTime": current, "endTime": end, "limit": 1500}
        )
        data = r.json()
        if not data:
            break
        all_klines.extend(data)
        current = data[-1][0] + 1
        if len(data) < 1500:
            break
        await asyncio.sleep(0.2)
    return all_klines


async def fetch_oi_hist(client: httpx.AsyncClient, symbol: str, period: str, start: int, end: int) -> list:
    all_oi = []
    current = start
    while current < end:
        r = await client.get(
            f"{BASE_URL}/futures/data/openInterestHist",
            params={"symbol": symbol, "period": period, "startTime": current, "endTime": end, "limit": 500}
        )
        data = r.json()
        if not data:
            break
        all_oi.extend(data)
        current = data[-1]["timestamp"] + 1
        if len(data) < 500:
            break
        await asyncio.sleep(0.2)
    return all_oi


async def fetch_funding(client: httpx.AsyncClient, symbol: str, start: int, end: int) -> list:
    all_f = []
    current = start
    while current < end:
        r = await client.get(
            f"{BASE_URL}/fapi/v1/fundingRate",
            params={"symbol": symbol, "startTime": current, "endTime": end, "limit": 1000}
        )
        data = r.json()
        if not data:
            break
        all_f.extend(data)
        current = data[-1]["fundingTime"] + 1
        if len(data) < 1000:
            break
        await asyncio.sleep(0.2)
    return all_f


async def fetch_taker_ratio(client: httpx.AsyncClient, symbol: str, period: str, start: int, end: int) -> list:
    all_t = []
    current = start
    while current < end:
        r = await client.get(
            f"{BASE_URL}/futures/data/takerlongshortRatio",
            params={"symbol": symbol, "period": period, "startTime": current, "endTime": end, "limit": 500}
        )
        data = r.json()
        if not data:
            break
        all_t.extend(data)
        current = data[-1]["timestamp"] + 1
        if len(data) < 500:
            break
        await asyncio.sleep(0.2)
    return all_t


def merge_to_df(klines, oi_hist, fundings, takers) -> pd.DataFrame:
    # Klines base
    df = pd.DataFrame(klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    
    # OI
    if oi_hist:
        oi_df = pd.DataFrame(oi_hist)
        oi_df["timestamp"] = pd.to_datetime(oi_df["timestamp"], unit="ms")
        oi_df["oi"] = oi_df["sumOpenInterestValue"].astype(float)
        oi_df = oi_df[["timestamp", "oi"]]
        df = pd.merge_asof(df.sort_values("timestamp"), oi_df.sort_values("timestamp"),
                           on="timestamp", direction="backward")
    else:
        df["oi"] = 0
    
    # Funding
    if fundings:
        f_df = pd.DataFrame(fundings)
        f_df["timestamp"] = pd.to_datetime(f_df["fundingTime"], unit="ms")
        f_df["funding_rate"] = f_df["fundingRate"].astype(float)
        f_df = f_df[["timestamp", "funding_rate"]]
        df = pd.merge_asof(df.sort_values("timestamp"), f_df.sort_values("timestamp"),
                           on="timestamp", direction="backward")
    else:
        df["funding_rate"] = 0.0
    
    # Taker ratio
    if takers:
        t_df = pd.DataFrame(takers)
        t_df["timestamp"] = pd.to_datetime(t_df["timestamp"], unit="ms")
        t_df["taker_ratio"] = t_df["buySellRatio"].astype(float)
        t_df = t_df[["timestamp", "taker_ratio"]]
        df = pd.merge_asof(df.sort_values("timestamp"), t_df.sort_values("timestamp"),
                           on="timestamp", direction="backward")
    else:
        df["taker_ratio"] = 1.0
    
    df = df.fillna(method="ffill").fillna(0)
    return df


async def download_symbol(symbol: str, months: int, interval: str, output_dir: Path):
    end = int(datetime.utcnow().timestamp() * 1000)
    start = int((datetime.utcnow() - timedelta(days=months * 30)).timestamp() * 1000)
    
    logger.info(f"Downloading {symbol}...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        klines, oi_hist, fundings, takers = await asyncio.gather(
            fetch_klines(client, symbol, interval, start, end),
            fetch_oi_hist(client, symbol, interval, start, end),
            fetch_funding(client, symbol, start, end),
            fetch_taker_ratio(client, symbol, interval, start, end),
            return_exceptions=True,
        )
    
    for name, data in [("klines", klines), ("oi", oi_hist), ("funding", fundings), ("taker", takers)]:
        if isinstance(data, Exception):
            logger.warning(f"{symbol} {name} failed: {data}")
    
    if isinstance(klines, Exception) or not klines:
        logger.error(f"{symbol}: no kline data, skip")
        return
    
    df = merge_to_df(
        klines,
        oi_hist if not isinstance(oi_hist, Exception) else [],
        fundings if not isinstance(fundings, Exception) else [],
        takers if not isinstance(takers, Exception) else [],
    )
    
    output = output_dir / f"{symbol}.csv"
    df.to_csv(output, index=False)
    logger.info(f"{symbol}: saved {len(df)} rows → {output}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols", nargs="+", help="Symbols (e.g., BTCUSDT ETHUSDT)")
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--interval", default="15m", choices=["5m", "15m", "30m", "1h", "4h"])
    parser.add_argument("--output", default="./data/historical")
    args = parser.parse_args()
    
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    for symbol in args.symbols:
        await download_symbol(symbol, args.months, args.interval, output_dir)
    
    logger.info("Done!")


if __name__ == "__main__":
    asyncio.run(main())
